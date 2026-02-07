import feedparser
import sqlite3
import datetime
import os
import re
from config import RSS_FEEDS, DB_FOLDER, DB_FILE_NAME

DB_FILE = os.path.join(DB_FOLDER, DB_FILE_NAME)

def clean_html(raw_html):
    """Removes HTML tags and cleans up whitespace."""
    if not raw_html:
        return ""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return " ".join(cleantext.split())

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
    
    total_new_items = 0

    # Iterate through the dictionary from config.py
    for source_label, rss_url in RSS_FEEDS.items():
        print(f"Fetching: {source_label}...")
        
        try:
            feed = feedparser.parse(rss_url)
            
            for entry in feed.entries:
                link = entry.get('link', '')
                title = entry.get('title', 'No Title')
                
                # Clean Summary
                raw_summary = entry.get('summary', '')
                clean_summary = clean_html(raw_summary)
                
                # Parse Date
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    dt = datetime.datetime(*entry.published_parsed[:6])
                    published = dt.strftime('%Y-%m-%d %H:%M:%S')
                else:
                    published = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                
                if not link:
                    continue
                    
                try:
                    # We now insert 'source_label' (e.g. "AI") into the source_feed column
                    c.execute('''
                        INSERT OR IGNORE INTO articles (link, title, summary, published, source_feed, scraped_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (link, title, clean_summary, published, source_label, datetime.datetime.now().isoformat()))
                    
                    if c.rowcount > 0:
                        total_new_items += 1
                        print(f"   [NEW] {title[:40]}...")
                        
                except Exception as e:
                    print(f"   Error saving {link}: {e}")

        except Exception as e:
            print(f"   Failed to parse {source_label}: {e}")
            
    conn.commit()
    conn.close()
    
    print(f"--- Finished. Added {total_new_items} new articles across all feeds. ---")

if __name__ == "__main__":
    ingest()