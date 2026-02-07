import os
import re
import json
import time
import random
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from datetime import datetime
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Import wrappers from your existing prompts2.py
from prompts2 import (
    INDUSTRY_STRATEGIST_SYSTEM, 
    RESEARCH_FRONTIERSMAN_SYSTEM, 
    PRAGMATIC_ENGINEER_SYSTEM,
    BASE_RANKING_PROMPT
)

load_dotenv()

# --- CONFIG ---
DB_URL = os.getenv("DATABASE_URL")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
WEIGHTS = {"engineering": 0.4, "industry": 0.4, "research": 0.2}
BATCH_SIZE = 8
client = genai.Client(api_key=GEMINI_KEY)

def get_clean_articles():
    """Fetches and cleans articles from the database."""
    print("--- 📜 Fetching and Cleaning Articles ---")
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    query = """
        SELECT link, title, summary, metadata
        FROM articles 
        WHERE source ILIKE 'Inoreader%' 
          AND published >= now() - interval '24 hours'
    """
    cur.execute(query)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    cleaned = []
    for row in rows:
        meta = row.get('metadata', {})
        authors = ", ".join(meta.get('authors', [])) if meta.get('authors') else "Unknown"
        cleaned.append({
            "link": row['link'],
            "title": row['title'],
            "author": authors,
            "summary": row['summary'][:1500] 
        })
    print(f"Found {len(cleaned)} unique articles.")
    return cleaned

def create_constrained_batches(articles):
    """The Deck-to-Batch Algorithm with Spacing Constraints."""
    print("--- 🃏 Creating Shuffled Deck (Nx3) with Spacing ---")
    deck = articles * 3
    random.shuffle(deck)
    
    shuffled_order = []
    while deck:
        for i in range(len(deck)):
            candidate = deck[i]
            recent_items = shuffled_order[-8:] if len(shuffled_order) >= 8 else shuffled_order
            
            if candidate['link'] not in [item['link'] for item in recent_items]:
                shuffled_order.append(deck.pop(i))
                break
        else:
            shuffled_order.append(deck.pop(0))

    batches = [shuffled_order[i:i + BATCH_SIZE] for i in range(0, len(shuffled_order), BATCH_SIZE)]
    
    if batches and len(batches[-1]) < BATCH_SIZE:
        orphans = batches.pop()
        for art in orphans:
            for b in reversed(batches):
                if len(b) < 9 and art['link'] not in [x['link'] for x in b]:
                    b.append(art)
                    break
    
    print(f"Produced {len(batches)} batches for evaluation.")
    return batches

def run_gemini_on_batches(batches, expert_name, system_instruction):
    """Sends each batch to Gemini with automated retry logic for rate limits."""
    print(f"--- 🤖 Expert: {expert_name.upper()} starting ---")
    results = []
    
    i = 0
    while i < len(batches):
        batch = batches[i]
        print(f"  Processing Batch {i+1}/{len(batches)}...")
        
        articles_text = ""
        for idx, art in enumerate(batch, 1):
            articles_text += f"ID: {idx}\nTitle: {art['title']}\nLink: {art['link']}\nSummary: {art['summary']}\n\n"

        prompt = BASE_RANKING_PROMPT.format(articles_text=articles_text)
        
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                ),
                contents=prompt
            )
            batch_scores = json.loads(response.text)
            results.append(batch_scores)
            
            # If successful, move to the next batch
            i += 1
            # Small delay to reduce the chance of hitting the limit again immediately
            time.sleep(2) 

        except Exception as e:
            # Check if it's a rate limit error (429)
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                print(f"  ⚠️ Rate limit hit at Batch {i+1}. Sleeping for 60s...")
                time.sleep(60)
                # We do NOT increment 'i', so the loop will try Batch 'i' again
            else:
                print(f"  ❌ Critical Error in batch {i+1}: {e}")
                # For non-429 errors, we skip to avoid infinite loops on bad data
                i += 1 
                
    return results

def aggregate_and_save(expert_raw_results):
    """Combines all scores and rationales into final weighted list."""
    print("--- 📊 Aggregating Results ---")
    master_map = {} 

    for role, batch_list in expert_raw_results.items():
        for batch_data in batch_list:
            for entry in batch_data:
                link = entry['link']
                score = entry.get('score', 0)
                rationale = entry.get('rationale', "")

                if link not in master_map:
                    master_map[link] = {
                        "link": link,
                        "title": entry.get('title', "Unknown"),
                        "raw_scores": {r: [] for r in WEIGHTS},
                        "rationales": {r: [] for r in WEIGHTS},
                        "sub_combined": {r: 0 for r in WEIGHTS}
                    }
                
                master_map[link]["raw_scores"][role].append(score)
                master_map[link]["rationales"][role].append(rationale)

    final_list = []
    for link, data in master_map.items():
        for role in WEIGHTS:
            scores = data["raw_scores"][role]
            data["sub_combined"][role] = round(sum(scores)/len(scores), 2) if scores else 0
        
        data["combined_score"] = round(
            (data["sub_combined"]["engineering"] * WEIGHTS["engineering"]) +
            (data["sub_combined"]["industry"] * WEIGHTS["industry"]) +
            (data["sub_combined"]["research"] * WEIGHTS["research"]), 2
        )
        final_list.append(data)

    final_list.sort(key=lambda x: x['combined_score'], reverse=True)
    
    with open("final_aggregated_intelligence.json", "w") as f:
        json.dump(final_list, f, indent=4)
    print("✅ Pipeline complete. Results saved to final_aggregated_intelligence.json")

def main():
    articles = get_clean_articles()
    if not articles: return
    
    batches = create_constrained_batches(articles)
    
    experts = [
        ("engineering", PRAGMATIC_ENGINEER_SYSTEM),
        ("industry", INDUSTRY_STRATEGIST_SYSTEM),
        ("research", RESEARCH_FRONTIERSMAN_SYSTEM)
    ]
    
    all_expert_output = {}
    for name, sys_prompt in experts:
        all_expert_output[name] = run_gemini_on_batches(batches, name, sys_prompt)
        
    aggregate_and_save(all_expert_output)

if __name__ == "__main__":
    main()