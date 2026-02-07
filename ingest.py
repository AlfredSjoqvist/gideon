import feedparser
import sqlite3
import datetime
import os
import re  # Added regex module

# --- CONFIGURATION ---
DB_FOLDER = "data"
DB_FILE = os.path.join(DB_FOLDER, "news.db")
RSS_URL = "https://www.inoreader.com/stream/user/1003596242/tag/AI"

def clean_html(raw_html):
    """Removes HTML tags and cleans up whitespace."""
    if not raw_html:
        return ""
    # Remove HTML tags (e.g., <div>, <a href...>)
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    # Replace multiple spaces/newlines with a single space
    cleantext = " ".join(cleantext.split())
    return cleantext

def init_db():
    if not os.path.exists(DB_FOLDER):
        os.makedirs(DB_FOLDER)
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS articles (
            link TEXT PRIMARY KEY,
            title TEXT,
            summary TEXT,
            published TEXT,
            source_feed TEXT,
            scraped_at TEXT
        )
    ''')
    conn.commit()
    return conn

def ingest():
    print(f"--- Starting Ingest: {datetime.datetime.now()} ---")
    conn = init_db()
    c = conn.cursor()
    
    feed = feedparser.parse(RSS_URL)
    print(f"Feed Status: {feed.get('status', 'Unknown')}")
    
    new_items = 0
    
    for entry in feed.entries:
        link = entry.get('link', '')
        title = entry.get('title', 'No Title')
        
        # --- NEW CLEANING STEP ---
        raw_summary = entry.get('summary', '')
        clean_summary = clean_html(raw_summary)[:1000] # Increased to 1000 since it's cleaner now
        
        published = entry.get('published', str(datetime.datetime.now()))
        
        if not link:
            continue
            
        try:
            c.execute('''
                INSERT OR IGNORE INTO articles (link, title, summary, published, source_feed, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (link, title, clean_summary, published, "Inoreader AI", datetime.datetime.now().isoformat()))
            
            if c.rowcount > 0:
                new_items += 1
                print(f"   [NEW] {title[:40]}...")
                
        except Exception as e:
            print(f"Error saving {link}: {e}")
            
    conn.commit()
    conn.close()
    
    print(f"--- Finished. Added {new_items} new articles. ---")

if __name__ == "__main__":
    ingest()