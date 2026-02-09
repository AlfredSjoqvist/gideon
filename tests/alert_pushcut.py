import os
import json
import time
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from google import genai

# --- IMPORT CORE LOGIC ---
from gideon_core import DailyTrial, Article

load_dotenv()

# --- CONFIGURATION ---
DB_URL = os.getenv("DATABASE_URL")
PUSHCUT_URL = os.getenv("PUSHCUT_TRIGGER_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# --- HOOK GENERATOR ---
def generate_notification_hook(client, title, analysis):
    print(f"      📝 Generating hook for: '{title[:20]}...'")
    prompt = f"""
    You are a Breaking Tech News Editor. Write a notification body for this article.
    HEADLINE: {title}
    ANALYSIS: {analysis[:2000]}
    CONSTRAINTS: Length 250-350 chars. Dense, functional tone. No fluff.
    OUTPUT: Just the text.
    """
    try:
        resp = client.models.generate_content(
            model="gemini-3-flash-preview", 
            contents=prompt
        )
        return resp.text.strip()
    except Exception as e:
        print(f"      ⚠️ Hook Gen Failed: {e}")
        return analysis[:300] + "..."

# --- NOTIFICATION LOGIC ---
def debug_send_pushcut(articles):
    print("\n--- 🕵️ DEBUGGING NOTIFICATION LOGIC ---")
    
    if not PUSHCUT_URL:
        print("❌ Error: PUSHCUT_TRIGGER_URL is missing.")
        return

    client = genai.Client(api_key=GEMINI_API_KEY)

    # 1. Filter Strategy
    unanimous_picks = [a for a in articles if a.metadata.get('ensemble_score', 0) >= 2]
    split_picks = [a for a in articles if a.metadata.get('ensemble_score', 0) == 1]
    
    print(f"   📊 Stats: {len(unanimous_picks)} Unanimous (2+), {len(split_picks)} Split (1)")

    final_queue = unanimous_picks[:]
    slots_needed = max(0, 4 - len(final_queue))
    
    if slots_needed > 0:
        print(f"   Positions open: {slots_needed}. Filling with split picks...")
        final_queue.extend(split_picks[:slots_needed])
    
    final_queue = final_queue[:6]
    
    print(f"   🚀 Final Send Queue: {len(final_queue)} items")
    
    if not final_queue:
        print("❌ Queue is empty!")
        return

    # 2. Sending Loop
    print("\n--- 📨 STARTING SEND LOOP ---")
    for i, art in enumerate(final_queue):
        print(f"\n[Item {i+1}/{len(final_queue)}] {art.title[:50]}")
        
        raw_analysis = art.metadata.get('deep_analysis', '')
        hook_text = generate_notification_hook(client, art.title, raw_analysis)
        
        # --- FIX: DYNAMIC PAYLOAD CONSTRUCTION ---
        # Pushcut rejects "image": null. We must omit the key if no image exists.
        img_url = art.metadata.get('thumbnail') or art.metadata.get('image')
        
        payload = {
            "title": f"GIDEON: {art.title}",
            "text": hook_text,
            "defaultAction": {"url": art.link}
        }

        # Only add image if it is a valid string
        if img_url and isinstance(img_url, str) and img_url.startswith('http'):
            payload["image"] = img_url
        else:
            print("      ⚠️ No valid image found (omitting key).")

        # DEBUG: Print exact payload
        print(f"      📦 Payload: {json.dumps(payload, indent=None)}")
        
        try:
            print(f"   📡 Sending POST to Pushcut...")
            r = requests.post(PUSHCUT_URL, json=payload, timeout=10)
            
            if r.status_code == 200: 
                print(f"   ✅ Success (200 OK)")
            else: 
                print(f"   ⚠️ Status: {r.status_code} | Body: {r.text}")
            
            time.sleep(2.0) 
        except Exception as e:
            print(f"   ❌ Failed: {e}")

    # 3. Send Daily Summary
    send_daily_summary_notification(client)

# --- DAILY SUMMARY NOTIFICATION ---
def send_daily_summary_notification(client):
    print("\n--- 📜 SENDING DAILY BRIEFING NOTIFICATION ---")
    
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT content FROM blog_entries WHERE entry_date = CURRENT_DATE")
        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            print("⚠️ No blog entry found for today. Skipping.")
            return

        print("   ✅ Found daily briefing. Generating hook...")
        content = row['content']
        hook_text = generate_notification_hook(client, "Daily Intelligence Briefing", content)

        # --- FIX: DYNAMIC PAYLOAD FOR SUMMARY ---
        payload = {
            "title": "🚨 GIDEON: Daily Briefing", 
            "text": hook_text,
            "defaultAction": {"url": "http://www.google.com"} 
        }
        # Note: We intentionally omit "image" here because we don't have one for the summary

        print(f"      📦 Payload: {json.dumps(payload)}")
        print(f"   📡 Sending Briefing to Pushcut...")
        
        r = requests.post(PUSHCUT_URL, json=payload, timeout=10)
        
        if r.status_code == 200: 
            print(f"   ✅ Summary Sent Successfully.")
        else: 
            print(f"   ⚠️ Summary Failed: {r.status_code} | Body: {r.text}")

    except Exception as e:
        print(f"   ❌ Summary Logic Error: {e}")

# --- MAIN ---
if __name__ == "__main__":
    if not DB_URL:
        print("❌ DATABASE_URL missing.")
        exit()

    print("🔌 Connecting to DB...")
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    # 1. Fetch recent articles
    print("🔍 Fetching articles from the last 24 hours...")
    cur.execute("""
        SELECT link, title, summary, published, source, feed_label, metadata
        FROM important 
        WHERE chosen_at >= NOW() - INTERVAL '24 hours'
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    if not rows:
        print("❌ No articles found in DB from the last 24 hours.")
        exit()

    print(f"✅ Found {len(rows)} articles. Re-initializing Article objects...")
    
    # 2. Convert to Objects
    articles = [
        Article(
            link=r['link'],
            title=r['title'],
            summary=r['summary'],
            published=r['published'],
            source=r['source'],
            feed_label=r['feed_label'],
            metadata=r['metadata'], 
            scraped_at=None 
        ) for r in rows
    ]

    # 3. Initialize DailyTrial
    print("\n⚖️  Running Mock 'Stage 2' Vote on Cached Data...")
    daily = DailyTrial(db_url=DB_URL)
    daily.summarized_articles = articles 

    # 4. Run the Vote
    top_picks = daily.run_stage_2_ensemble()

    # 5. Run the Notification Logic
    if top_picks:
        debug_send_pushcut(top_picks) 
    else:
        print("❌ The Board voted 0 on all articles.")