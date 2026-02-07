import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Load DATABASE_URL from your .env file
load_dotenv()
DB_URL = os.getenv("DATABASE_URL")

def get_latest_articles(limit=10):
    """
    Connects to Supabase and fetches the most recent articles.
    """
    if not DB_URL:
        print("Error: DATABASE_URL not found in environment.")
        return

    try:
        # We use RealDictCursor so we can access columns by name (e.g., row['title'])
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Query: Get the newest articles based on published date
        query = """
            SELECT title, source_feed, published, metadata, link
            FROM articles
            ORDER BY published DESC
            LIMIT %s;
        """
        
        cur.execute(query, (limit,))
        rows = cur.fetchall()

        cur.close()
        conn.close()
        return rows

    except Exception as e:
        print(f"Error reading from Supabase: {e}")
        return []

if __name__ == "__main__":
    articles = get_latest_articles(limit=5)
    
    print(f"--- 📡 Latest Intelligence ({len(articles)} items) ---\n")
    
    for row in articles:
        print(f"TITLE: {row['title']}")
        print(f"SOURCE: {row['source_feed']}")
        print(f"DATE: {row['published']}")
        
        # Accessing the JSONB metadata we created in ingest.py
        tags = row['metadata'].get('tags', [])
        if tags:
            print(f"TAGS: {', '.join(tags)}")
            
        print(f"URL: {row['link']}")
        print("-" * 30)