import os
import re
import json
import time
import random
import psycopg2
import requests
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
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

WEIGHTS = {"engineering": 0.4, "industry": 0.4, "research": 0.2}
BATCH_SIZE = 8
MAX_RETRIES = 5
client = genai.Client(api_key=GEMINI_KEY)

# --- UTILS ---
def normalize_title(text):
    if not text: return ""
    text = re.sub(r'\[.*?\]', '', text) 
    return re.sub(r'[^a-zA-Z0-9]', '', text).lower()

def normalize_url(url):
    if not url: return ""
    url = url.lower().split('://')[-1].replace('www.', '')
    return url.strip('/')

def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        response = requests.post(url, data=payload)
        response.raise_for_status()
    except Exception as e:
        print(f"❌ Telegram Error: {e}")

# --- CORE LOGIC ---
def get_clean_articles():
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
    
    cleaned = [{"link": r['link'], "title": r['title'], "summary": r['summary'][:1500]} for r in rows]
    print(f"Found {len(cleaned)} AI articles.")
    return cleaned

def create_constrained_batches(articles):
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
    print(f"--- 🤖 Expert: {expert_name.upper()} starting ---")
    results = []
    
    for i, batch in enumerate(tqdm(batches, desc=f"Processing {expert_name}")):
        articles_text = "".join([f"ID: {idx}\nTitle: {art['title']}\nLink: {art['link']}\nSummary: {art['summary']}\n\n" 
                                 for idx, art in enumerate(batch, 1)])
        prompt = BASE_RANKING_PROMPT.format(articles_text=articles_text)
        
        success, attempts = False, 0
        while attempts < MAX_RETRIES and not success:
            try:
                response = client.models.generate_content(
                    model="gemini-2.0-flash",
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        response_mime_type="application/json",
                    ),
                    contents=prompt
                )
                clean_json = re.sub(r'[\x00-\x1F\x7F]', '', response.text)
                results.append(json.loads(clean_json))
                success = True
            except Exception as e:
                attempts += 1
                wait = attempts * 5
                time.sleep(wait)
        
    return results

def process_and_rank(expert_raw_results):
    print("--- 📊 De-duplicating & Scoring ---")
    master_store = {}

    for role, batch_list in expert_raw_results.items():
        for batch_data in batch_list:
            for entry in batch_data:
                t_key, u_key = normalize_title(entry.get('title')), normalize_url(entry.get('link'))
                
                existing_key = t_key if t_key in master_store else next((k for k, v in master_store.items() if normalize_url(v['link']) == u_key), None)
                
                if existing_key:
                    target = master_store[existing_key]
                else:
                    master_store[t_key] = {"link": entry.get('link'), "title": entry.get('title'), "raw_scores": {r: [] for r in WEIGHTS}}
                    target = master_store[t_key]

                if len(entry.get('title', '')) > len(target["title"]): target["title"] = entry.get('title')
                target["raw_scores"][role].append(entry.get('score', 0))

    ranked_list = []
    for data in master_store.values():
        scores = {r: (sum(data["raw_scores"][role]) / len(data["raw_scores"][role]) if data["raw_scores"][role] else 0) for r in WEIGHTS}
        data["combined_score"] = round(sum(scores[r] * WEIGHTS[r] for r in WEIGHTS), 2)
        ranked_list.append(data)

    ranked_list.sort(key=lambda x: x['combined_score'], reverse=True)
    return ranked_list

def main():
    articles = get_clean_articles()
    if not articles:
        send_telegram_msg("⚠️ No AI articles found in the last 24h.")
        return
    
    batches = create_constrained_batches(articles)
    experts = [("engineering", PRAGMATIC_ENGINEER_SYSTEM), ("industry", INDUSTRY_STRATEGIST_SYSTEM), ("research", RESEARCH_FRONTIERSMAN_SYSTEM)]
    
    all_expert_output = {name: run_gemini_on_batches(batches, name, sys) for name, sys in experts}
    final_ranked = process_and_rank(all_expert_output)

    # --- TELEGRAM TOP 5 ---
    top_5 = final_ranked[:5]
    msg = "🚀 <b>Top 5 AI Insights</b>\n\n"
    for i, art in enumerate(top_5, 1):
        msg += f"{i}. <b>{art['title']}</b>\n🔗 {art['link']}\n\n"
    
    send_telegram_msg(msg)
    print("✅ Pipeline complete. Top 5 sent to Telegram.")

if __name__ == "__main__":
    main()