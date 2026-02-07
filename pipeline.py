import os
import re
import json
import time
import random
import psycopg2
from collections import defaultdict
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from google import genai
from google.genai import types
from tqdm import tqdm  # Loading bar

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
MAX_RETRIES = 5
client = genai.Client(api_key=GEMINI_KEY)

# --- UTILS FOR CLEANING ---
def normalize_title(text):
    """Deep normalization: removes punctuation, brackets, and spaces."""
    if not text: return ""
    # Remove things like [R], [P], [D] and all non-alphanumeric chars
    text = re.sub(r'\[.*?\]', '', text) 
    return re.sub(r'[^a-zA-Z0-9]', '', text).lower()

def normalize_url(url):
    """Normalize URL by removing protocol and trailing slashes."""
    if not url: return ""
    url = url.lower().split('://')[-1].replace('www.', '')
    return url.strip('/')

def get_clean_articles():
    """Fetches AI articles from the last 24h."""
    print("--- 📜 Fetching AI Articles ---")
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    query = """
        SELECT link, title, summary, metadata
        FROM articles 
        WHERE source ILIKE 'Inoreader%' 
          AND feed_label = 'AI'
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
    print(f"Found {len(cleaned)} AI-labeled articles.")
    return cleaned

def create_constrained_batches(articles):
    """Nx3 Deck Algorithm with spacing."""
    deck = articles * 3
    random.shuffle(deck)
    shuffled_order = []
    while deck:
        for i in range(len(deck)):
            candidate = deck[i]
            recent_links = [item['link'] for item in shuffled_order[-8:]]
            if candidate['link'] not in recent_links:
                shuffled_order.append(deck.pop(i))
                break
        else:
            shuffled_order.append(deck.pop(0))
    return [shuffled_order[i:i + BATCH_SIZE] for i in range(0, len(shuffled_order), BATCH_SIZE)]

def run_gemini_on_batches(batches, expert_name, system_instruction):
    """Sends batches to Gemini with 5 retries per batch and standardizes JSON cleanup."""
    print(f"--- 🤖 Expert: {expert_name.upper()} starting ---")
    results = []
    
    # Progress bar for batches
    for i, batch in enumerate(tqdm(batches, desc=f"Processing {expert_name}")):
        articles_text = ""
        for idx, art in enumerate(batch, 1):
            articles_text += f"ID: {idx}\nTitle: {art['title']}\nLink: {art['link']}\nSummary: {art['summary']}\n\n"

        prompt = BASE_RANKING_PROMPT.format(articles_text=articles_text)
        
        success = False
        attempts = 0
        while attempts < MAX_RETRIES and not success:
            try:
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        response_mime_type="application/json",
                    ),
                    contents=prompt
                )
                
                # Clean invalid control characters before parsing
                clean_json = re.sub(r'[\x00-\x1F\x7F]', '', response.text)
                batch_scores = json.loads(clean_json)
                results.append(batch_scores)
                success = True
                time.sleep(1) # Base delay

            except Exception as e:
                attempts += 1
                if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                    wait = attempts * 10
                    print(f"\n  ⚠️ Rate limit. Retry {attempts}/{MAX_RETRIES} in {wait}s...")
                    time.sleep(wait)
                else:
                    print(f"\n  ❌ Error in batch {i+1} (Attempt {attempts}): {e}")
                    time.sleep(2)
        
        if not success:
            print(f"\n  🚫 Skipping batch {i+1} after {MAX_RETRIES} failed attempts.")

    return results

def aggregate_and_save(expert_raw_results):
    """Merges duplicates using Title-OR-Link matching."""
    print("--- 📊 De-duplicating & Scoring ---")
    
    # map: { normalized_key: final_entry_dict }
    master_store = {}

    for role, batch_list in expert_raw_results.items():
        for batch_data in batch_list:
            for entry in batch_data:
                title_key = normalize_title(entry.get('title', ''))
                url_key = normalize_url(entry.get('link', ''))
                
                # Check if we have seen this before via Title OR URL
                existing_key = None
                if title_key in master_store:
                    existing_key = title_key
                else:
                    # Fallback check URLs in store
                    for k, v in master_store.items():
                        if normalize_url(v['link']) == url_key:
                            existing_key = k
                            break
                
                if existing_key:
                    target = master_store[existing_key]
                else:
                    master_store[title_key] = {
                        "link": entry.get('link', ''),
                        "title": entry.get('title', ''),
                        "raw_scores": {r: [] for r in WEIGHTS},
                        "rationales": {r: [] for r in WEIGHTS}
                    }
                    target = master_store[title_key]

                # Update if longer (usually more complete)
                if len(entry.get('title', '')) > len(target["title"]):
                    target["title"] = entry.get('title')

                target["raw_scores"][role].append(entry.get('score', 0))
                target["rationales"][role].append(entry.get('rationale', ""))

    final_list = []
    for data in master_store.values():
        sub_combined = {}
        for role in WEIGHTS:
            scores = data["raw_scores"][role]
            sub_combined[role] = round(sum(scores)/len(scores), 2) if scores else 0
        
        data["sub_combined"] = sub_combined
        data["combined_score"] = round(sum(sub_combined[r] * WEIGHTS[r] for r in WEIGHTS), 2)
        final_list.append(data)

    final_list.sort(key=lambda x: x['combined_score'], reverse=True)
    
    with open("final_aggregated_intelligence.json", "w") as f:
        json.dump(final_list, f, indent=4)
    print(f"✅ Saved {len(final_list)} unique records.")

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