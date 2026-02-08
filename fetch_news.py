import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from dotenv import load_dotenv

# This loads the variables from .env into the environment
load_dotenv()

# Fetch connection string from environment variables
DB_URL = os.getenv("DATABASE_URL")
OUTPUT_FILE = "daily_digest.json"

def get_last_24h_articles():
    if not DB_URL:
        print("Error: DATABASE_URL not set.")
        return

    print(f"--- Fetching articles from the last 24 hours ({datetime.now()}) ---")

    try:
        # We use RealDictCursor so the results are returned as dictionaries
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # SQL Query using Postgres time intervals
        # This targets the 'published' column we created earlier
        query = """
            SELECT 
                link, 
                title, 
                summary, 
                published, 
                source,
                feed_label, 
                metadata
            FROM articles
            WHERE published >= now() - interval '72 hours'
            ORDER BY published DESC;
        """

        cur.execute(query)
        rows = cur.fetchall()

        # Convert datetime objects to strings for JSON serialization
        for row in rows:
            if row['published']:
                row['published'] = row['published'].isoformat()

        # Save to JSON
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=4, ensure_ascii=False)

        print(f"Success! Found {len(rows)} articles.")
        print(f"Data saved to {OUTPUT_FILE}")

    except Exception as e:
        print(f"Database error: {e}")
    finally:
        if 'cur' in locals(): cur.close()
        if 'conn' in locals(): conn.close()

if __name__ == "__main__":
    get_last_24h_articles()