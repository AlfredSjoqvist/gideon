import os
import json
import time
from dotenv import load_dotenv

# Import our core library
from gideon_core import Corpus, Stage1Trial
# Import prompts
from prompts2 import (
    INDUSTRY_STRATEGIST_SYSTEM, 
    RESEARCH_FRONTIERSMAN_SYSTEM, 
    PRAGMATIC_ENGINEER_SYSTEM,
    CIVILIZATIONAL_ENGINEER_SYSTEM,
    SYSTEMIC_RISK_SYSTEM,
    DIGITAL_ANTHROPOLOGIST_SYSTEM,
    SWEDISH_INNOVATION_SCOUT_SYSTEM,
    BASE_RANKING_PROMPT
)

load_dotenv()

def run_gideon_trial(query, judge_panel, winners_count=5, ai_model="gemini-2.0-flash", run_name="default_run"):
    """
    Orchestrates a full Gideon trial run and returns the winners.
    """
    print(f"\n🚀 Starting Run: [{run_name}]")
    
    # 1. Setup Database Connection
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("❌ Error: DATABASE_URL not set in .env")
        return None, None, 0.0

    # 2. Initialize and Populate Corpus
    corpus = Corpus()
    corpus.fetch_from_db(db_url, query)
    
    if not corpus.articles:
        print(f"⚠️ No articles found for [{run_name}]. Skipping.")
        return None, None, 0.0

    # 3. Initialize Trial
    # FIXED: Removed base_prompt and ai_model from init to match your core definition
    trial = Stage1Trial(
        winners_count=winners_count, 
        judge_configs=judge_panel
    )
    
    # 4. Run the Convening
    # FIXED: Passed ai_model here. Added safety check for return values.
    try:
        result = trial.convene(corpus, ai_model=ai_model)
        
        # Check if core returned (corpus, json, cost) or just (corpus)
        if isinstance(result, tuple) and len(result) == 3:
            winner_corpus, winner_data, total_churn = result
        else:
            # Fallback if core only returns corpus
            winner_corpus = result
            winner_data = [] # Empty list as placeholder
            total_churn = 0.0
            print("⚠️ Note: Detailed JSON/Cost data not returned by current gideon_core.py")

    except TypeError:
        # Fallback if convene doesn't accept ai_model yet
        print("⚠️ Warning: Core 'convene' does not accept ai_model. Running with default.")
        winner_corpus = trial.convene(corpus)
        winner_data = []
        total_churn = 0.0

    # 5. Save Individual Run Results
    # Only save if we actually got data back
    if winner_data:
        filename = f"debug/results_{run_name}.json"
        os.makedirs("debug", exist_ok=True)
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(winner_data, f, indent=2, default=str)
        print(f"💾 Saved [{run_name}] results to '{filename}'")
    
    print(f"💳 Run Cost: ${total_churn:.6f}")
    
    return winner_corpus, winner_data, total_churn


if __name__ == "__main__":
    # --- 1. Initialize Master Corpus ---
    MASTER_CORPUS = Corpus()
    ALL_WINNERS_JSON = []
    TOTAL_SESSION_COST = 0.0

    # --- 2. Define Jobs ---
    jobs = [
        {
            "run_name": "reddit_ai_top",
            "query": """
                SELECT link, title, summary, published, source, feed_label, metadata, scraped_at
                FROM articles 
                WHERE source ILIKE 'Inoreader%' 
                  AND feed_label = 'Reddit AI'
                  AND published >= now() - interval '24 hours'
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
                  AND published >= now() - interval '24 hours'
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
                  AND published >= now() - interval '24 hours'
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
                  AND published >= now() - interval '24 hours'
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
                  AND published >= now() - interval '24 hours'
            """,
            "judge_panel": [{"name": "Research Frontiersman", "prompt": RESEARCH_FRONTIERSMAN_SYSTEM, "weight": 1.0}],
            "winners_count": 3,
            "ai_model": "gemini-3-flash-preview"
        },
        {
            "run_name": "hackernews",
            "query": """
                SELECT link, title, summary, published, source, feed_label, metadata, scraped_at
                FROM articles 
                WHERE source ILIKE 'HackerNews%' 
                  AND published >= now() - interval '24 hours'
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
                  AND published >= now() - interval '24 hours'
            """,
            "judge_panel": [{"name": "Swedish Innovator", "prompt": SWEDISH_INNOVATION_SCOUT_SYSTEM, "weight": 1.0}],
            "winners_count": 3,
            "ai_model": "gemini-3-flash-preview"
        }
    ]

    # --- 3. Execute Loop ---
    for job in jobs:
        w_corpus, w_data, cost = run_gideon_trial(**job)
        
        if w_corpus:
            # Add to Master Corpus
            for article in w_corpus.articles:
                MASTER_CORPUS.add_article(article)
            
            # Add to Master JSON (enrich with run_name for context)
            if w_data:
                for item in w_data:
                    item['source_run'] = job['run_name']
                    ALL_WINNERS_JSON.append(item)
            
            TOTAL_SESSION_COST += cost
            count = len(w_data) if w_data else len(w_corpus.articles)
            print(f"   ✅ Added {count} winners to Master Corpus")
        
        # Respect rate limits between heavy runs
        time.sleep(1)

    # --- 4. Final Output ---
    print("\n" + "="*50)
    print(f"🏁 ALL RUNS COMPLETE")
    print(f"📚 Total Winners: {len(MASTER_CORPUS.articles)}")
    print(f"💰 Total Session Cost: ${TOTAL_SESSION_COST:.6f}")
    
    # Save the Master File
    if ALL_WINNERS_JSON:
        with open("final_master_winners.json", "w", encoding="utf-8") as f:
            json.dump(ALL_WINNERS_JSON, f, indent=2, default=str)
        print(f"💾 Saved consolidated results to 'final_master_winners.json'")
    else:
        print("⚠️ No detailed JSON data to save (Core might not be returning it yet).")
        
    print("="*50)