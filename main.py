import os
import json
import time
import requests
from dotenv import load_dotenv

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

def send_simple_pushcut(articles):
    """Sends top 4 articles as notifications without AI Image gen."""
    if not PUSHCUT_URL:
        print("⚠️ Pushcut URL missing. Skipping notifications.")
        return

    print(f"\n🚀 Sending Notifications for Top {len(articles[:4])} Picks...")
    
    for art in articles[:4]:
        score = art.metadata.get('ensemble_score', 0)
        # Visual stars for the notification title
        score_icon = "⭐⭐⭐" if score >= 2 else "⭐"
        
        # Try to find an existing image in metadata, else None
        img_url = art.metadata.get('thumbnail') or art.metadata.get('image')
        
        # Rationale comes from the DailyTrial Stage 1 analysis
        rationale = art.metadata.get('deep_analysis', '')[:300] + "..."

        payload = {
            "title": f"{score_icon} Gideon: {art.title}",
            "text": rationale,
            "image": img_url, # Optional, might be null
            "defaultAction": {"url": art.link}
        }
        
        try:
            requests.post(PUSHCUT_URL, json=payload)
            print(f"   🔔 Sent: {art.title[:40]}...")
            time.sleep(0.5) # Slight delay to ensure delivery order
        except Exception as e:
            print(f"   ❌ Push failed: {e}")

def run_stage1_job(query, judge_panel, winners_count, ai_model, run_name):
    """Orchestrates a single Stage 1 trial run."""
    print(f"\n🏃 Starting Stage 1 Job: [{run_name}]")
    
    # 1. Populate Corpus from DB
    corpus = Corpus()
    corpus.fetch_from_db(DB_URL, query)
    
    if not corpus.articles:
        print(f"   ⚠️ No articles found for [{run_name}]. Skipping.")
        return None

    # 2. Initialize Trial
    trial = Stage1Trial(winners_count=winners_count, judge_configs=judge_panel)
    
    # 3. Convene Judges
    # Note: Depending on your exact Stage1Trial implementation, 
    # run .convene() and capture the resulting corpus
    try:
        # Assuming convene returns (corpus, data, cost) based on previous context
        result = trial.convene(corpus, ai_model=ai_model)
        if isinstance(result, tuple):
            return result[0] # Return just the corpus of winners
        return result
    except Exception as e:
        print(f"   ❌ Stage 1 Error: {e}")
        return None

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
            "query": """
                SELECT link, title, summary, published, source, feed_label, metadata, scraped_at
                FROM articles 
                WHERE source ILIKE 'Inoreader%' 
                  AND feed_label = 'Reddit AI'
                  AND published >= now() - interval '12 hours'
            """,
            "judge_panel": [{"name": "Pragmatic Engineer", "prompt": PRAGMATIC_ENGINEER_SYSTEM, "weight": 1.0}],
            "winners_count": 3,
            "ai_model": "gemini-3-flash-preview"
        },
        {
            "run_name": "general_ai_engineering",
            "query": """
                SELECT link, title, summary, published, source, feed_label, metadata, scraped_at
                FROM articles 
                WHERE source ILIKE 'Inoreader%' 
                  AND feed_label = 'AI News'
                  AND published >= now() - interval '12 hours'
            """,
            "judge_panel": [
                {"name": "Industry Expert", "prompt": INDUSTRY_STRATEGIST_SYSTEM, "weight": 0.5},
                {"name": "Pragmatic Engineer", "prompt": PRAGMATIC_ENGINEER_SYSTEM, "weight": 0.5}
            ],
            "winners_count": 3,
            "ai_model": "gemini-3-flash-preview"
        },
        {
            "run_name": "geopolitical",
            "query": """
                SELECT link, title, summary, published, source, feed_label, metadata, scraped_at
                FROM articles 
                WHERE source ILIKE 'Inoreader%' 
                  AND feed_label = 'World News'
                  AND published >= now() - interval '12 hours'
            """,
            "judge_panel": [{"name": "Civilizational Expert", "prompt": CIVILIZATIONAL_ENGINEER_SYSTEM, "weight": 1.0}],
            "winners_count": 3,
            "ai_model": "gemini-2.0-flash"
        },
        {
            "run_name": "general_tech",
            "query": """
                SELECT link, title, summary, published, source, feed_label, metadata, scraped_at
                FROM articles 
                WHERE source ILIKE 'Inoreader%' 
                  AND feed_label = 'Tech'
                  AND published >= now() - interval '12 hours'
            """,
            "judge_panel": [{"name": "Civilizational Expert", "prompt": CIVILIZATIONAL_ENGINEER_SYSTEM, "weight": 1.0}],
            "winners_count": 2,
            "ai_model": "gemini-2.0-flash"
        },
        {
            "run_name": "research_papers",
            "query": """
                SELECT link, title, summary, published, source, feed_label, metadata, scraped_at
                FROM articles 
                WHERE source ILIKE 'ArXiv%' 
                  AND published >= now() - interval '12 hours'
            """,
            "judge_panel": [{"name": "Research Frontiersman", "prompt": RESEARCH_FRONTIERSMAN_SYSTEM, "weight": 1.0}],
            "winners_count": 3,
            "ai_model": "gemini-2.0-flash"
        },
        {
            "run_name": "hackernews",
            "query": """
                SELECT link, title, summary, published, source, feed_label, metadata, scraped_at
                FROM articles 
                WHERE source ILIKE 'HackerNews%' 
                  AND published >= now() - interval '12 hours'
            """,
            "judge_panel": [{"name": "Pragmatic Engineer", "prompt": PRAGMATIC_ENGINEER_SYSTEM, "weight": 1.0}],
            "winners_count": 2,
            "ai_model": "gemini-3-flash-preview"
        },
        {
            "run_name": "sweden",
            "query": """
                SELECT link, title, summary, published, source, feed_label, metadata, scraped_at
                FROM articles 
                WHERE source ILIKE 'Inoreader%' 
                  AND feed_label = 'Sverige'
                  AND published >= now() - interval '12 hours'
            """,
            "judge_panel": [{"name": "Swedish Innovator", "prompt": SWEDISH_INNOVATION_SCOUT_SYSTEM, "weight": 1.0}],
            "winners_count": 3,
            "ai_model": "gemini-2.0-flash"
        }
    ]

    for job in jobs:
        winner_corpus = run_stage1_job(**job)
        if winner_corpus:
            for art in winner_corpus.articles:
                MASTER_CORPUS.add_article(art)
            print(f"   ✅ Added {len(winner_corpus.articles)} articles to Master Corpus.")
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
    # This also saves individual article analysis to the 'important' table
    daily.run_stage_1_analysis(MASTER_CORPUS)

    # B. The Board Vote (Gemini + Claude Ensembe)
    # Returns top 6 articles with scores
    top_picks = daily.run_stage_2_ensemble()

    # --- PART 3: NOTIFICATIONS ---
    if top_picks:
        send_simple_pushcut(top_picks)
    else:
        print("⚠️ No top picks returned from ensemble.")

    # --- PART 4: NEWSLETTER GENERATION ---
    # Generates the 5-min read and saves it to 'blog_entries' DB
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