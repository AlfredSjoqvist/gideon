import feedparser
import sqlite3
import datetime
import os
import re
import time
from config import RSS_FEEDS, DB_FOLDER, DB_FILE_NAME

DB_FILE = os.path.join(DB_FOLDER, DB_FILE_NAME)

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
    
    total_new_items = 0

    # Iterate through the list of dictionaries from config.py
    for feed in RSS_FEEDS:
        source = feed["source"]
        label = feed["label"]
        rss_url = feed["link"]
        
        # Create a combined label, e.g., "ArXiv: Machine Learning"
        source_id = f"{source}: {label}"
        
        time.sleep(3)

        print(f"Fetching: {source_id}...")
        
        try:
            parsed_feed = feedparser.parse(rss_url)
            
            for entry in parsed_feed.entries:
                link = entry.get('link', '')
                title = entry.get('title', 'No Title')
                
                # --- METADATA ENRICHMENT ---
                # 1. Extract Authors (Specific to ArXiv/Academic feeds)
                authors = [a.name for a in entry.get('authors', [])]
                author_str = f"Authors: {', '.join(authors)}" if authors else ""

                # 2. Extract Categories/Tags (Specific to ArXiv)
                categories = [t.get('term') for t in entry.get('tags', [])]
                cat_str = f"Categories: {', '.join(categories)}" if categories else ""

                # 3. Clean the main summary text
                raw_summary = entry.get('summary', '')
                clean_text = clean_html(raw_summary)

                # 4. Combine everything into one rich text block for the AI
                # If it's ArXiv, it will look like: "Authors: X, Y... \n Categories: cs.LG \n Abstract..."
                # If it's Hacker News, it will just be the text.
                final_summary_parts = [part for part in [author_str, cat_str, clean_text] if part]
                final_summary = " | ".join(final_summary_parts)
                
                # --- DATE PARSING ---
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    dt = datetime.datetime(*entry.published_parsed[:6])
                    published = dt.strftime('%Y-%m-%d %H:%M:%S')
                else:
                    published = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                
                if not link:
                    continue
                    
                try:
                    c.execute('''
                        INSERT OR IGNORE INTO articles (link, title, summary, published, source_feed, scraped_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (link, title, final_summary, published, source_id, datetime.datetime.now().isoformat()))
                    
                    if c.rowcount > 0:
                        total_new_items += 1
                        print(f"   [NEW] {title[:40]}...")
                        
                except Exception as e:
                    print(f"   Error saving {link}: {e}")

        except Exception as e:
            print(f"   Failed to parse {source_id}: {e}")
            
    conn.commit()
    conn.close()
    
    print(f"--- Finished. Added {total_new_items} new articles across all feeds. ---")

if __name__ == "__main__":
    ingest()