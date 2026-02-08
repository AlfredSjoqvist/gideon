import os
import json
import time
import requests
from dotenv import load_dotenv
from google import genai 

# --- IMPORTS FROM YOUR CORE LIBRARY ---
from gideon_core import Corpus, Stage1Trial, DailyTrial, Article
from psycopg2.extras import RealDictCursor
import psycopg2

# --- IMPORT PROMPTS ---
from prompts2 import (
    INDUSTRY_STRATEGIST_SYSTEM, 
    RESEARCH_FRONTIERSMAN_SYSTEM, 
    PRAGMATIC_ENGINEER_SYSTEM,
    CIVILIZATIONAL_ENGINEER_SYSTEM,
    SWEDISH_INNOVATION_SCOUT_SYSTEM
)

load_dotenv()

# --- CONFIGURATION ---
DB_URL = os.getenv("DATABASE_URL")
PUSHCUT_URL = os.getenv("PUSHCUT_TRIGGER_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

def generate_notification_hook(client, title, analysis):
    """Uses Gemini to generate a high-density hook."""
    prompt = f"""
    You are a Breaking Tech News Editor. Write a notification body for this article.
    HEADLINE: {title}
    ANALYSIS: {analysis[:2000]}
    CONSTRAINTS:
    1. Length: STRICTLY between 250-350 characters.
    2. Tone: Dense, functional, urgent. No "click here".
    3. Content: State the core implication for a Senior AI Engineer.
    OUTPUT: Just the text.
    """
    try:
        # Using gemini-3-flash-preview for speed/cost efficiency
        resp = client.models.generate_content(
            model="gemini-3-flash-preview", 
            contents=prompt
        )
        return resp.text.strip()
    except Exception as e:
        print(f"      ⚠️ Hook generation failed: {e}")
        return analysis[:300] + "..."

def send_simple_pushcut(articles):
    if not PUSHCUT_URL:
        print("⚠️ Pushcut URL missing. Skipping notifications.")
        return
    if not GEMINI_API_KEY:
        print("⚠️ Gemini API Key missing. Hooks will fail.")
        return
    
    client = genai.Client(api_key=GEMINI_API_KEY)

    # Filter Strategy (Prioritize Score 2)
    unanimous_picks = [a for a in articles if a.metadata.get('ensemble_score', 0) >= 2]
    split_picks = [a for a in articles if a.metadata.get('ensemble_score', 0) == 1]
    
    final_queue = unanimous_picks[:]
    slots_needed = max(0, 4 - len(final_queue))
    
    if slots_needed > 0:
        final_queue.extend(split_picks[:slots_needed])

    # Cap at 6
    final_queue = final_queue[:6]

    if not final_queue:
        print("⚠️ No articles qualified for notification.")
        return

    print(f"\n🚀 Sending {len(final_queue)} Notifications...")
    
    for art in final_queue:
        title_text = f"GIDEON: {art.title}"
        
        print(f"   ✨ Generating hook for: {art.title[:30]}...")
        raw_analysis = art.metadata.get('deep_analysis', '')
        hook_text = generate_notification_hook(client, art.title, raw_analysis)

        # --- DYNAMIC PAYLOAD CONSTRUCTION (Prevents 400 Error) ---
        img_url = art.metadata.get('thumbnail') or art.metadata.get('image')
        
        payload = {
            "title": title_text,
            "text": hook_text,
            "defaultAction": {"url": art.link}
        }

        # Only add image key if it is a valid URL string
        if img_url and isinstance(img_url, str) and img_url.startswith('http'):
            payload["image"] = img_url
        
        try:
            requests.post(PUSHCUT_URL, json=payload, timeout=10)
            print(f"      🔔 Sent.")
            time.sleep(2.0) # 2s delay for reliability
        except Exception as e:
            print(f"      ❌ Push failed: {e}")

    # --- SEND SUMMARY NOTIFICATION ---
    send_daily_summary_notification(client)

def send_daily_summary_notification(client):
    print("\n--- 📜 SENDING DAILY BRIEFING NOTIFICATION ---")
    
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        # Fetch today's blog entry
        cur.execute("SELECT content FROM blog_entries WHERE entry_date = CURRENT_DATE")
        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            print("⚠️ No blog entry found for today. Skipping summary notification.")
            return

        print("   ✅ Found daily briefing. Generating hook...")
        content = row['content']
        hook_text = generate_notification_hook(client, "Daily Intelligence Briefing", content)

        payload = {
            "title": "🚨 GIDEON: Daily Briefing", 
            "text": hook_text,
            # No image for summary to be safe
            "defaultAction": {"url": "http://www.google.com"} # Placeholder
        }

        print(f"   📡 Sending Briefing to Pushcut...")
        r = requests.post(PUSHCUT_URL, json=payload, timeout=10)
        
        if r.status_code == 200: 
            print(f"   ✅ Summary Sent Successfully.")
        else: 
            print(f"   ⚠️ Summary Failed: {r.status_code} | {r.text}")

    except Exception as e:
        print(f"   ❌ Summary Logic Error: {e}")

def run_stage1_job(query, judge_panel, winners_count, ai_model, run_name):
    print(f"\n🏃 Starting Stage 1 Job: [{run_name}]")
    
    corpus = Corpus()
    corpus.fetch_from_db(DB_URL, query)
    
    if not corpus.articles:
        print(f"   ⚠️ No articles found for [{run_name}]. Skipping.")
        return None, 0.0

    trial = Stage1Trial(winners_count=winners_count, judge_configs=judge_panel)
    
    try:
        # result is usually (winner_corpus, winner_json, cost)
        result = trial.convene(corpus, ai_model=ai_model)
        
        if isinstance(result, tuple) and len(result) == 3:
            return result[0], result[2] # Return (Corpus, Cost)
        
        return result, 0.0 
    except Exception as e:
        print(f"   ❌ Stage 1 Error: {e}")
        return None, 0.0

def main():
    if not DB_URL:
        print("❌ Error: DATABASE_URL not set.")
        return

    print("="*60)
    print(f"🚀 GIDEON PIPELINE STARTED AT {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)

    MASTER_CORPUS = Corpus()
    STAGE_1_TOTAL_COST = 0.0
    
    jobs = [
        {
            "run_name": "reddit_ai_top",
            "query": "SELECT link, title, summary, published, source, feed_label, metadata, scraped_at FROM articles WHERE source ILIKE 'Inoreader%' AND feed_label = 'Reddit AI' AND published >= now() - interval '24 hours'",
            "judge_panel": [{"name": "Pragmatic Engineer", "prompt": PRAGMATIC_ENGINEER_SYSTEM, "weight": 1.0}],
            "winners_count": 3,
            "ai_model": "gemini-3-flash-preview"
        },
        {
            "run_name": "general_ai_engineering",
            "query": "SELECT link, title, summary, published, source, feed_label, metadata, scraped_at FROM articles WHERE source ILIKE 'Inoreader%' AND feed_label = 'AI News' AND published >= now() - interval '24 hours'",
            "judge_panel": [
                {"name": "Industry Expert", "prompt": INDUSTRY_STRATEGIST_SYSTEM, "weight": 0.5},
                {"name": "Pragmatic Engineer", "prompt": PRAGMATIC_ENGINEER_SYSTEM, "weight": 0.5}
            ],
            "winners_count": 3,
            "ai_model": "gemini-3-flash-preview"
        },
        {
            "run_name": "geopolitical",
            "query": "SELECT link, title, summary, published, source, feed_label, metadata, scraped_at FROM articles WHERE source ILIKE 'Inoreader%' AND feed_label = 'World News' AND published >= now() - interval '24 hours'",
            "judge_panel": [{"name": "Civilizational Expert", "prompt": CIVILIZATIONAL_ENGINEER_SYSTEM, "weight": 1.0}],
            "winners_count": 3,
            "ai_model": "gemini-2.0-flash"
        },
        {
            "run_name": "research_papers",
            "query": "SELECT link, title, summary, published, source, feed_label, metadata, scraped_at FROM articles WHERE source ILIKE 'ArXiv%' AND published >= now() - interval '24 hours'",
            "judge_panel": [{"name": "Research Frontiersman", "prompt": RESEARCH_FRONTIERSMAN_SYSTEM, "weight": 1.0}],
            "winners_count": 3,
            "ai_model": "gemini-2.0-flash"
        },
        {
            "run_name": "sweden",
            "query": "SELECT link, title, summary, published, source, feed_label, metadata, scraped_at FROM articles WHERE source ILIKE 'Inoreader%' AND feed_label = 'Sverige' AND published >= now() - interval '24 hours'",
            "judge_panel": [{"name": "Swedish Innovator", "prompt": SWEDISH_INNOVATION_SCOUT_SYSTEM, "weight": 1.0}],
            "winners_count": 3,
            "ai_model": "gemini-2.0-flash"
        },
        {
            "run_name": "general_tech",
            "query": "SELECT link, title, summary, published, source, feed_label, metadata, scraped_at FROM articles WHERE source ILIKE 'Inoreader%' AND feed_label = 'Tech' AND published >= now() - interval '24 hours'",
            "judge_panel": [{"name": "Civilizational Expert", "prompt": CIVILIZATIONAL_ENGINEER_SYSTEM, "weight": 1.0}],
            "winners_count": 2,
            "ai_model": "gemini-2.0-flash"
        },
        {
            "run_name": "hackernews",
            "query": "SELECT link, title, summary, published, source, feed_label, metadata, scraped_at FROM articles WHERE source ILIKE 'HackerNews%' AND published >= now() - interval '24 hours'",
            "judge_panel": [{"name": "Pragmatic Engineer", "prompt": PRAGMATIC_ENGINEER_SYSTEM, "weight": 1.0}],
            "winners_count": 2,
            "ai_model": "gemini-3-flash-preview"
        }
    ]

    for job in jobs:
        winner_corpus, job_cost = run_stage1_job(**job)
        STAGE_1_TOTAL_COST += job_cost
        
        if winner_corpus:
            for art in winner_corpus.articles:
                MASTER_CORPUS.add_article(art)
            print(f"   ✅ Added {len(winner_corpus.articles)} articles. (Job Cost: ${job_cost:.4f})")
        time.sleep(1) 

    total_candidates = len(MASTER_CORPUS.articles)
    print(f"\n📦 Master Corpus Assembled: {total_candidates} Articles")
    print(f"💰 Stage 1 Cost: ${STAGE_1_TOTAL_COST:.4f}")
    
    if total_candidates == 0:
        print("❌ No candidates found. Aborting DailyTrial.")
        return

    # --- PART 2: THE DAILY TRIAL ---
    print("\n" + "-"*40)
    print("⚖️  INITIATING DAILY TRIAL (Deep Analysis & Voting)")
    print("-" * 40)

    daily = DailyTrial(db_url=DB_URL)
    daily.run_stage_1_analysis(MASTER_CORPUS)
    top_picks = daily.run_stage_2_ensemble()

    # --- PART 3: NEWSLETTER ---
    # We generate the newsletter first so it exists in DB for the notification to find
    daily.run_stage_3_newsletter()

    # --- PART 4: NOTIFICATIONS ---
    # Now we send both individual article notifications AND the summary
    if top_picks:
        send_simple_pushcut(top_picks)
    else:
        print("⚠️ No top picks returned from ensemble.")

    # --- FINAL TALLY ---
    GRAND_TOTAL = STAGE_1_TOTAL_COST + daily.total_cost

    print("\n" + "="*60)
    print(f"🏁 PIPELINE COMPLETE")
    print(f"   Stage 1 (Filtering):  ${STAGE_1_TOTAL_COST:.4f}")
    print(f"   Stage 2/3 (Daily):    ${daily.total_cost:.4f}")
    print(f"   ------------------------------")
    print(f"   💰 GRAND TOTAL:       ${GRAND_TOTAL:.4f}")
    print("="*60)

if __name__ == "__main__":
    main()