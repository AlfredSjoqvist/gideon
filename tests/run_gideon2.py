import os
import json
import requests
from dotenv import load_dotenv
from gideon_core import Corpus, DailyTrial, Article

load_dotenv()

# --- CONFIGURATION ---
TEST_MODE = True  # Set True to use 'final_master_winners.json' instead of DB
PUSHCUT_URL = os.getenv("PUSHCUT_TRIGGER_URL")
DB_URL = os.getenv("DATABASE_URL")

def load_test_corpus():
    """Loads winners from JSON into a Corpus object."""
    print("🧪 TEST MODE: Loading from 'final_master_winners.json'...")
    try:
        with open("final_master_winners.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            
        corpus = Corpus()
        for item in data:
            # Reconstruct Article object
            art = Article(
                link=item.get('link'),
                title=item.get('title'),
                summary=item.get('summary', ''),
                source=item.get('source', 'Test'),
                feed_label=item.get('feed_label', 'Test'),
                metadata=item.get('metadata', {})
            )
            # Ensure metadata is a dict (sometimes JSON strings)
            if isinstance(art.metadata, str):
                try: art.metadata = json.loads(art.metadata)
                except: art.metadata = {}
                
            corpus.add_article(art)
        return corpus
    except Exception as e:
        print(f"❌ Failed to load test data: {e}")
        return None

def send_pushcut(articles):
    """Sends top 4 articles as notifications."""
    if not PUSHCUT_URL:
        print("⚠️ Pushcut URL missing.")
        return

    print(f"\n🚀 Sending Notifications for Top {len(articles)} Picks...")
    
    for art in articles:
        # Get score description
        score = art.metadata.get('ensemble_score', 0)
        score_icon = "⭐⭐⭐" if score == 3 else ("⭐⭐" if score == 2 else "⭐")
        
        # Get Thumbnail
        img_url = art.metadata.get('thumbnail')
        
        payload = {
            "title": f"{score_icon} Gideon Daily: {art.title}",
            "text": art.metadata.get('deep_analysis', '')[:400], # The rationale from Stage 1
            "image": img_url,
            "defaultAction": {"url": art.link}
        }
        
        try:
            requests.post(PUSHCUT_URL, json=payload)
            print(f"   🔔 Sent: {art.title[:30]}...")
        except Exception as e:
            print(f"   ❌ Push failed: {e}")

def main():
    # 1. Initialize Trial
    trial = DailyTrial(db_url=DB_URL)
    
    # 2. Load Data (Test vs Real)
    if TEST_MODE:
        corpus = load_test_corpus()
    else:
        # Real Mode: Fetch from DB using your standard query logic or pass existing corpus
        # For this example, we assume you might want to run this AFTER run_gideon.py
        # Or fetch the last 24h winners from DB
        print("🌍 REAL MODE: Fetching 'important' candidates from DB...")
        # (Implementation depends on if you want to re-fetch winners or run raw)
        # Assuming we load a corpus here similar to run_gideon.py
        corpus = load_test_corpus() # Placeholder: Replace with DB fetch if needed

    if not corpus or not corpus.articles:
        print("❌ No articles to process.")
        return

    # 3. RUN STAGE 1: Summarize & Analyze (Visit URLs)
    trial.run_stage_1_analysis(corpus)
    
    # 4. RUN STAGE 2: Ensemble Voting
    # Note: Requires OPENAI_API_KEY and ANTHROPIC_API_KEY in .env
    top_picks = trial.run_stage_2_ensemble()
    
    # 5. RUN STAGE 3: Notifications (Top 4 only)
    send_pushcut(top_picks[:4])
    
    # 6. RUN STAGE 4: Newsletter Generation
    trial.run_stage_3_newsletter()

if __name__ == "__main__":
    main()