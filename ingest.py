import os
import re
import time
import html
import datetime
import feedparser
import psycopg2
from psycopg2.extras import Json
from config import RSS_FEEDS

# Fetch the connection string from environment variables
# This keeps your password safe. locally, put this in your .env file.
DB_URL = os.getenv("DATABASE_URL")

def clean_text_content(raw_html):
    """
    Strips HTML tags, unescapes entities, and normalizes whitespace.
    """
    if not raw_html: 
        return ""
    
    # 1. Decode HTML entities
    text = html.unescape(raw_html)
    
    # 2. Remove HTML tags
    cleanr = re.compile('<.*?>')
    text = re.sub(cleanr, ' ', text)
    
    # 3. Normalize whitespace
    return " ".join(text.split())

def get_db_connection():
    if not DB_URL:
        raise ValueError("DATABASE_URL environment variable is not set. Please set it in .env or GitHub Secrets.")
    return psycopg2.connect(DB_URL)

def init_db():
    """
    Creates the table in Supabase if it doesn't exist.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Postgres Schema:
    # - metadata is JSONB (Better than TEXT for querying later)
    # - published/scraped_at are TIMESTAMP
    cur.execute('''
        CREATE TABLE IF NOT EXISTS articles (
            link TEXT PRIMARY KEY,
            title TEXT,
            summary TEXT,
            published TIMESTAMP,
            source TEXT,
            feed_label TEXT,
            metadata JSONB,
            scraped_at TIMESTAMP
        );
    ''')
    
    conn.commit()
    cur.close()
    conn.close()

def parse_date(entry):
    """
    Parses RSS/Atom dates into a Python datetime object.
    """
    dt = None
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        dt = datetime.datetime(*entry.published_parsed[:6])
    elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
        dt = datetime.datetime(*entry.updated_parsed[:6])
        
    if not dt:
        dt = datetime.datetime.now()
        
    return dt

def ingest():
    print(f"--- ☁️ Starting Cloud Ingest: {datetime.datetime.now()} ---")
    
    # Ensure table exists (safe to run every time)
    init_db()
    
    conn = get_db_connection()
    cur = conn.cursor()
    total_new_items = 0

    for feed in RSS_FEEDS:
        source_name = feed["source"]
        label_name = feed["label"]
        rss_url = feed["link"]
        
        # Respect ArXiv rate limits
        if "ArXiv" in source_name:
            time.sleep(3)

        print(f"Fetching: {source_name}...")
        
        try:
            parsed = feedparser.parse(rss_url)
            
            for entry in parsed.entries:
                # --- 1. LINK EXTRACTION ---
                link = entry.get('link', '')
                if not link:
                    link = entry.get('id', '')
                
                # Check duplication relies on ON CONFLICT below, so we proceed to processing

                title = clean_text_content(entry.get('title', 'No Title'))
                
                # --- 2. STRUCTURED METADATA ---
                meta_payload = {
                    "authors": [clean_text_content(a.name) for a in entry.get('authors', [])],
                    "tags": [t.get('term') for t in entry.get('tags', []) if t.get('term')],
                    "comments_url": entry.get('comments', ''), # For HN
                    "raw_score": entry.get('points', None) # Sometimes available in HN RSS
                }
                
                # --- 3. CLEAN SUMMARY ---
                raw_summary = entry.get('summary', '') or entry.get('description', '')
                clean_summary = clean_text_content(raw_summary)

                # --- 4. DATE ---
                published = parse_date(entry)

                # --- 5. INSERT INTO SUPABASE ---
                try:
                    # Postgres syntax uses %s placeholders
                    cur.execute(
                        """
                        INSERT INTO articles 
                        (link, title, summary, published, source, feed_label, metadata, scraped_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (link) DO NOTHING
                        """,
                        (
                            link, 
                            title, 
                            clean_summary, 
                            published, 
                            source_name, 
                            Json(meta_payload), # Automatically handles JSON serialization
                            datetime.datetime.now()
                        )
                    )
                    
                    # Check if a row was actually inserted (vs skipped)
                    if cur.rowcount > 0:
                        total_new_items += 1
                        print(f"   + [NEW] {title[:40]}...")
                
                except Exception as e:
                    print(f"   ! DB Error: {e}")
                    conn.rollback() # Essential in Postgres to reset cursor after an error

        except Exception as e:
            print(f"   ! Failed to parse feed: {e}")

    conn.commit()
    cur.close()
    conn.close()
    print(f"--- Finished. Added {total_new_items} articles to Supabase. ---")

if __name__ == "__main__":
    ingest()