import os
import time
import requests
import json
from dotenv import load_dotenv
from google import genai 

# --- CORE IMPORTS ---
from gideon_core import (
    IntelligencePipeline, 
    ArticleRepository, 
    ModelRegistry,
    FilteringPipeline
)
from psycopg2.extras import RealDictCursor
import psycopg2

# --- PROMPTS ---
from system_prompts import (
    INDUSTRY_STRATEGIST_SYSTEM, 
    RESEARCH_FRONTIERSMAN_SYSTEM, 
    PRAGMATIC_ENGINEER_SYSTEM,
    CIVILIZATIONAL_ENGINEER_SYSTEM,
    SWEDISH_INNOVATION_SCOUT_SYSTEM
)

load_dotenv()

# --- CONFIGURATION ---
class Config:
    DB_URL = os.getenv("DATABASE_URL")
    PUSHCUT_URL = os.getenv("PUSHCUT_TRIGGER_URL")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

class NotificationService:
    """Handles distribution of intelligence via Pushcut."""
    
    @staticmethod
    def _generate_hook(client, title, analysis):
        """Uses fast model to create high-density hooks."""
        prompt = f"""
        You are a Breaking Tech News Editor. Write a notification body.
        HEADLINE: {title}
        ANALYSIS: {analysis[:2000]}
        CONSTRAINTS: Strictly 250-350 chars. Dense, urgent, technical tone.
        OUTPUT: Text only.
        """
        try:
            resp = client.models.generate_content(
                model=ModelRegistry.GEMINI_FAST, 
                contents=prompt
            )
            return resp.text.strip()
        except Exception as e:
            print(f"      ⚠️ Hook generation failed: {e}")
            return analysis[:300] + "..."

    @staticmethod
    def dispatch_articles(articles):
        if not Config.PUSHCUT_URL or not Config.GEMINI_API_KEY:
            print("⚠️ Missing Pushcut Config. Skipping.")
            return

        client = genai.Client(api_key=Config.GEMINI_API_KEY)
        
        # Priority Queue Logic
        high_pri = [a for a in articles if a.metadata.get('ensemble_score', 0) >= 2]
        low_pri = [a for a in articles if a.metadata.get('ensemble_score', 0) == 1]
        
        queue = high_pri[:]
        if len(queue) < 4:
            queue.extend(low_pri[:4-len(queue)])
        
        queue = queue[:6] # Hard Cap
        
        if not queue:
            print("⚠️ No qualified articles for notification.")
            return

        print(f"\n🚀 Sending {len(queue)} Article Notifications...")
        for art in queue:
            print(f"   ✨ Processing: {art.title[:30]}...")
            hook = NotificationService._generate_hook(client, art.title, art.metadata.get('deep_analysis', ''))
            
            payload = {
                "title": art.title,
                "text": hook,
                "defaultAction": {"url": art.link}
            }
            
            # Image handling
            img = art.metadata.get('thumbnail') or art.metadata.get('image')
            if img and isinstance(img, str) and img.startswith('http'):
                payload["image"] = img
                
            try:
                requests.post(Config.PUSHCUT_URL, json=payload, timeout=10)
                print("      🔔 Sent.")
                time.sleep(2.0)
            except Exception as e:
                print(f"      ❌ Failed: {e}")

        # Chain the summary notification
        NotificationService.dispatch_summary(client)

    @staticmethod
    def dispatch_summary(client):
        print("\n--- 📜 Sending Summary Notification ---")
        try:
            with psycopg2.connect(Config.DB_URL) as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT content FROM blog_entries WHERE entry_date = CURRENT_DATE")
                    row = cur.fetchone()
            
            if not row:
                print("⚠️ No blog entry found today.")
                return

            print("✅ Found daily briefing.")
            hook = NotificationService._generate_hook(client, "Daily Intelligence Briefing", row['content'])
            
            payload = {
                "title": "Daily Intelligence Briefing", 
                "text": hook,
                "defaultAction": {"url": "https://alfredsjoqvist.github.io/gideon-300/"}
            }
            
            requests.post(Config.PUSHCUT_URL, json=payload, timeout=10)
            print("   ✅ Summary Sent.")
        except Exception as e:
            print(f"   ❌ Summary Logic Error: {e}")

def run_job_definition(run_name, query, judge_panel, winners_count, model):
    """Executes a single filtering job definition."""
    print(f"\n🏃 Starting Job: [{run_name}]")
    
    repo = ArticleRepository(Config.DB_URL)
    repo.fetch_candidates(query)
    
    if not repo.articles:
        print(f"   ⚠️ No articles found. Skipping.")
        return None, 0.0

    pipeline = FilteringPipeline(target_count=winners_count, agent_configs=judge_panel)
    return pipeline.execute(repo, default_model=model)

def main():
    if not Config.DB_URL:
        print("❌ Error: DATABASE_URL not set.")
        return

    print("="*60)
    print(f"🚀 GIDEON PIPELINE INITIALIZED: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)

    MASTER_REPO = ArticleRepository()
    filtering_cost = 0.0
    
    # --- JOB DEFINITIONS ---
    jobs = [
        {
            "run_name": "reddit_ai_top",
            "query": "SELECT link, title, summary, published, source, feed_label, metadata, scraped_at FROM articles WHERE source ILIKE 'Inoreader%' AND feed_label = 'Reddit AI' AND published >= now() - interval '24 hours'",
            "judge_panel": [{"name": "Pragmatic Engineer", "prompt": PRAGMATIC_ENGINEER_SYSTEM, "weight": 1.0}],
            "winners_count": 1,
            "model": ModelRegistry.GEMINI_FAST
        },
        {
            "run_name": "general_ai_engineering",
            "query": "SELECT link, title, summary, published, source, feed_label, metadata, scraped_at FROM articles WHERE source ILIKE 'Inoreader%' AND feed_label = 'AI News' AND published >= now() - interval '24 hours'",
            "judge_panel": [
                {"name": "Industry Expert", "prompt": INDUSTRY_STRATEGIST_SYSTEM, "weight": 0.5},
                {"name": "Pragmatic Engineer", "prompt": PRAGMATIC_ENGINEER_SYSTEM, "weight": 0.5}
            ],
            "winners_count": 3,
            "model": ModelRegistry.GEMINI_FAST
        },
        {
            "run_name": "geopolitical",
            "query": "SELECT link, title, summary, published, source, feed_label, metadata, scraped_at FROM articles WHERE source ILIKE 'Inoreader%' AND feed_label = 'World News' AND published >= now() - interval '24 hours'",
            "judge_panel": [{"name": "Civilizational Expert", "prompt": CIVILIZATIONAL_ENGINEER_SYSTEM, "weight": 1.0}],
            "winners_count": 3,
            "model": ModelRegistry.GEMINI_STABLE
        },
        {
            "run_name": "research_papers",
            "query": "SELECT link, title, summary, published, source, feed_label, metadata, scraped_at FROM articles WHERE source ILIKE 'ArXiv%' AND published >= now() - interval '24 hours'",
            "judge_panel": [{"name": "Research Frontiersman", "prompt": RESEARCH_FRONTIERSMAN_SYSTEM, "weight": 1.0}],
            "winners_count": 3,
            "model": ModelRegistry.GEMINI_STABLE
        },
        {
            "run_name": "sweden",
            "query": "SELECT link, title, summary, published, source, feed_label, metadata, scraped_at FROM articles WHERE source ILIKE 'Inoreader%' AND feed_label = 'Sverige' AND published >= now() - interval '24 hours'",
            "judge_panel": [{"name": "Swedish Innovator", "prompt": SWEDISH_INNOVATION_SCOUT_SYSTEM, "weight": 1.0}],
            "winners_count": 3,
            "model": ModelRegistry.GEMINI_STABLE
        },
        {
            "run_name": "general_tech",
            "query": "SELECT link, title, summary, published, source, feed_label, metadata, scraped_at FROM articles WHERE source ILIKE 'Inoreader%' AND feed_label = 'Tech' AND published >= now() - interval '24 hours'",
            "judge_panel": [{"name": "Civilizational Expert", "prompt": CIVILIZATIONAL_ENGINEER_SYSTEM, "weight": 1.0}],
            "winners_count": 2,
            "model": ModelRegistry.GEMINI_STABLE
        },
        {
            "run_name": "hackernews",
            "query": "SELECT link, title, summary, published, source, feed_label, metadata, scraped_at FROM articles WHERE source ILIKE 'HackerNews%' AND published >= now() - interval '24 hours'",
            "judge_panel": [{"name": "Pragmatic Engineer", "prompt": PRAGMATIC_ENGINEER_SYSTEM, "weight": 1.0}],
            "winners_count": 2,
            "model": ModelRegistry.GEMINI_FAST
        }
    ]

    # --- EXECUTE FILTERING ---
    for job in jobs:
        winner_repo, cost = run_job_definition(**job)
        filtering_cost += cost
        if winner_repo:
            for art in winner_repo.articles:
                MASTER_REPO.add(art)
            print(f"   ✅ Added {len(winner_repo.articles)} items.")
        time.sleep(1)

    if not MASTER_REPO.articles:
        print("❌ No candidates filtered. Aborting.")
        return

    print(f"\n📦 Master Corpus: {len(MASTER_REPO.articles)} Articles")
    print(f"💰 Filtering Cost: ${filtering_cost:.4f}")

    # --- EXECUTE INTELLIGENCE PIPELINE ---
    print("\n" + "-"*40)
    print("⚖️  INITIATING INTELLIGENCE PIPELINE")
    print("-" * 40)

    pipeline = IntelligencePipeline(db_url=Config.DB_URL)
    
    # 1. Analyze
    pipeline.run_deep_analysis(MASTER_REPO)
    
    # 2. Vote
    top_picks = pipeline.run_consensus_voting()
    
    # 3. Publish
    pipeline.generate_newsletter()
    
    # 4. Notify
    if top_picks:
        NotificationService.dispatch_articles(top_picks)
    else:
        print("⚠️ No top picks generated.")

    # --- TELEMETRY ---
    grand_total = filtering_cost + pipeline.total_cost
    print("\n" + "="*60)
    print(f"🏁 PIPELINE COMPLETE")
    print(f"   Filtering Cost:    ${filtering_cost:.4f}")
    print(f"   Intelligence Cost: ${pipeline.total_cost:.4f}")
    print(f"   ------------------------------")
    print(f"   💰 GRAND TOTAL:       ${grand_total:.4f}")
    print("="*60)

if __name__ == "__main__":
    main()