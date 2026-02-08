import os
import json
import time
import requests
from dotenv import load_dotenv
from google import genai

# --- IMPORTS FROM YOUR CORE LIBRARY ---
from gideon_core import Corpus, Stage1Trial, DailyTrial, Article

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
    """
    Uses Gemini Flash to generate a high-density, 300-char hook.
    """
    prompt = f"""
    You are a Breaking Tech News Editor. Write a notification body for this article.
    
    HEADLINE: {title}
    ANALYSIS: {analysis[:2000]}
    
    CONSTRAINTS:
    1. Length: STRICTLY between 250-350 characters.
    2. Tone: Dense, functional, urgent. No "click here" or "read more".
    3. Content: State the core implication or value add for a smart AI/ML Engineer M.Sc student.
    
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
        # Fallback to truncation if AI fails
        return analysis[:300] + "..."

def send_simple_pushcut(articles):
    """Sends high priority articles with AI-generated hooks."""
    if not PUSHCUT_URL:
        print("⚠️ Pushcut URL missing. Skipping notifications.")
        return

    # Initialize Gemini Client for Hook Generation
    if not GEMINI_API_KEY:
        print("⚠️ Gemini API Key missing. Hooks will fail.")
        return
        
    client = genai.Client(api_key=GEMINI_API_KEY)

    # Filter Strategy
    unanimous_picks = [a for a in articles if a.metadata.get('ensemble_score', 0) == 2]
    split_picks = [a for a in articles if a.metadata.get('ensemble_score', 0) == 1]
    
    final_queue = unanimous_picks[:]
    slots_needed = max(0, 4 - len(final_queue))
    
    if slots_needed > 0:
        final_queue.extend(split_picks[:slots_needed])

    # Cap at 6 to avoid spamming if too many unanimous
    final_queue = final_queue[:6]

    if not final_queue:
        print("⚠️ No articles qualified for notification.")
        return

    print(f"\n🚀 Sending {len(final_queue)} Notifications...")
    
    for art in final_queue:
        # Title logic
        title_text = f"GIDEON: {art.title}"
        
        # Image logic
        img_url = art.metadata.get('thumbnail') or art.metadata.get('image')
        
        # Body logic: Generate custom hook
        print(f"   ✨ Generating hook for: {art.title[:30]}...")
        raw_analysis = art.metadata.get('deep_analysis', '')
        hook_text = generate_notification_hook(client, art.title, raw_analysis)

        payload = {
            "title": title_text,
            "text": hook_text,
            "image": img_url, 
            "defaultAction": {"url": art.link}
        }
        
        try:
            requests.post(PUSHCUT_URL, json=payload)
            print(f"      🔔 Sent.")
            time.sleep(1.0) 
        except Exception as e:
            print(f"      ❌ Push failed: {e}")

def run_stage1_job(query, judge_panel, winners_count, ai_model, run_name):
    """Orchestrates a single Stage 1 trial run."""
    print(f"\n🏃 Starting Stage 1 Job: [{run_name}]")
    
    # 1. Populate Corpus from DB
    corpus = Corpus()
    corpus.fetch_from_db(DB_URL, query)
    
    if not corpus.articles:
        print(f"   ⚠️ No articles found for [{run_name}]. Skipping.")
        return None, 0.0

    # 2. Initialize Trial
    trial = Stage1Trial(winners_count=winners_count, judge_configs=judge_panel)
    
    # 3. Convene Judges
    try:
        # Assuming convene returns (corpus, data, cost)
        result = trial.convene(corpus, ai_model=ai_model)
        
        # FIX: Ensure we unpack properly.
        if isinstance(result, tuple) and len(result) == 3:
            return result[0], result[2] # Return (Corpus, Cost)
        
        # Fallback if return signature is different
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

    # --- PART 1: GATHER CANDIDATES (Stage 1 Trials) ---
    MASTER_CORPUS = Corpus()
    STAGE_1_TOTAL_COST = 0.0
    
    # Define your specific queries and personas here
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
            "winners_count": 4,
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
        time.sleep(1) # Rate limit safety

    total_candidates = len(MASTER_CORPUS.articles)
    print(f"\n📦 Master Corpus Assembled: {total_candidates} Articles")
    print(f"💰 Stage 1 Cost: ${STAGE_1_TOTAL_COST:.4f}")
    
    if total_candidates == 0:
        print("❌ No candidates found. Aborting DailyTrial.")
        return

    # --- PART 2: THE DAILY TRIAL (Deep Analysis & Voting) ---
    print("\n" + "-"*40)
    print("⚖️  INITIATING DAILY TRIAL (Deep Analysis & Voting)")
    print("-" * 40)

    daily = DailyTrial(db_url=DB_URL)

    # A. Scrape & Analyze (Gemini 3 Pro)
    daily.run_stage_1_analysis(MASTER_CORPUS)

    # B. The Board Vote (Gemini + Claude Ensembe)
    top_picks = daily.run_stage_2_ensemble()

    # --- PART 3: NOTIFICATIONS ---
    if top_picks:
        send_simple_pushcut(top_picks)
    else:
        print("⚠️ No top picks returned from ensemble.")

    # --- PART 4: NEWSLETTER GENERATION ---
    daily.run_stage_3_newsletter()

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