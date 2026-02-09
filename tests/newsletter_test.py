import os
import time
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# --- IMPORT YOUR REFACTORED CORE LOGIC ---
from gideon_core import IntelligencePipeline, Article 

load_dotenv()

# --- CONFIGURATION ---
DB_URL = os.getenv("DATABASE_URL")

# --- GLOBAL FLAG ---
# Set to True to actually run the expensive Stage 2 voting logic.
# Set to False to mock scores and just test the formatting (Free).
FORCE_RUN_STAGE_2 = True

def debug_newsletter_generation():
    if not DB_URL:
        print("❌ DATABASE_URL missing.")
        return

    print("🔌 Connecting to DB to fetch recent intelligence...")
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    # 1. Fetch articles from 'important' table (Last 24h)
    query = """
        SELECT link, title, summary, published, source, feed_label, metadata 
        FROM important 
        WHERE chosen_at >= NOW() - INTERVAL '24 hours'
    """
    cur.execute(query)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    if not rows:
        print("❌ No articles found in DB from the last 24 hours.")
        return

    print(f"✅ Found {len(rows)} articles in the database.")

    # 2. Re-construct Article Objects
    # The new dataclass structure in gideon_core handles this cleanly
    reconstructed_articles = []
    for r in rows:
        art = Article(
            link=r['link'],
            title=r['title'],
            summary=r['summary'],
            published=r['published'],
            source=r['source'],
            feed_label=r['feed_label'],
            metadata=r['metadata'], 
            scraped_at=None 
        )
        
        if not art.metadata.get('deep_analysis'):
            print(f"   ⚠️ Warning: Article '{art.title[:20]}...' has no deep_analysis. Skipping.")
            continue
            
        reconstructed_articles.append(art)

    if not reconstructed_articles:
        print("❌ No valid articles found.")
        return

    print(f"✅ Loaded {len(reconstructed_articles)} valid articles for processing.")

    # 3. Initialize IntelligencePipeline
    print("\n🧪 Initializing IntelligencePipeline for Debugging...")
    pipeline = IntelligencePipeline(db_url=DB_URL)
    
    # Inject the data manually since we aren't running Stage 1
    pipeline.summarized_articles = reconstructed_articles

    # --- CONDITIONAL STAGE 2 EXECUTION ---
    if FORCE_RUN_STAGE_2:
        print("\n🗳️  FORCE_RUN_STAGE_2 is TRUE. Running actual Consensus Voting (Costs Money)...")
        # This updates the metadata['ensemble_score'] and saves to DB
        pipeline.run_consensus_voting()
    else:
        print("\n🔧 FORCE_RUN_STAGE_2 is FALSE. Injecting MOCK scores for free formatting test...")
        for i, art in enumerate(pipeline.summarized_articles):
            # Mock Logic: First 4 get high score (Deep Dive), rest get low score (Sector Watch)
            if i < 4:
                art.metadata['ensemble_score'] = 3
            else:
                art.metadata['ensemble_score'] = 1
    # -------------------------------------

    # 4. RUN STAGE 3 (Newsletter Generation)
    start_time = time.time()
    html_output = pipeline.generate_newsletter()
    elapsed = time.time() - start_time

    # 5. Output Results
    print("\n" + "="*50)
    print("📊 DIAGNOSTIC RESULTS")
    print("="*50)
    
    if html_output:
        word_count = len(html_output.split())
        print(f"✅ Success!")
        print(f"⏱️  Time taken:   {elapsed:.2f} seconds")
        print(f"📝 Total Chars:  {len(html_output)}")
        print(f"📚 Approx Words: {word_count}")
        print("-" * 30)
        print("🔎 PREVIEW (First 500 chars):")
        print(html_output[:500])
        print("-" * 30)
        print("🔎 PREVIEW (Last 200 chars):")
        print(html_output[-200:])
        print("="*50)
        
        if word_count < 1500:
            print("⚠️ WARNING: Word count is low (< 1500). Check prompt constraints.")
    else:
        print("❌ Generation returned empty string.")

if __name__ == "__main__":
    debug_newsletter_generation()