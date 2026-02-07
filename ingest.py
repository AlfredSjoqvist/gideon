import feedparser
import sqlite3
import datetime
import os
import re
import time
from config import RSS_FEEDS, DB_FOLDER, DB_FILE_NAME

DB_FILE = os.path.join(DB_FOLDER, DB_FILE_NAME)

def clean_html(raw_html):
    if not raw_html: return ""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return " ".join(cleantext.split())

def init_db():
    if not os.path.exists(DB_FOLDER): os.makedirs(DB_FOLDER)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS articles 
                 (link TEXT PRIMARY KEY, title TEXT, summary TEXT, published TEXT, source_feed TEXT, scraped_at TEXT)''')
    conn.commit()
    return conn

def ingest():
    print(f"--- Starting Ingest: {datetime.datetime.now()} ---")
    conn = init_db()
    c = conn.cursor()
    total_new_items = 0

    for feed in RSS_FEEDS:
        source = feed["source"]
        label = feed["label"]
        rss_url = feed["link"]
        source_id = f"{source}: {label}"
        
        # Respect ArXiv rate limits
        if "ArXiv" in source:
            time.sleep(3)

        print(f"Fetching: {source_id}...")
        
        try:
            parsed = feedparser.parse(rss_url)
            
            for entry in parsed.entries:
                # --- 1. ROBUST LINK EXTRACTION (The Fix) ---
                link = entry.get('link', '')
                
                # Fallback 1: Check the 'links' list for the alternate (HTML) version
                if not link and 'links' in entry:
                    for l in entry.links:
                        if l.get('rel') == 'alternate':
                            link = l.get('href')
                            break
                
                # Fallback 2: Use the ID (ArXiv IDs are usually the URL)
                if not link:
                    link = entry.get('id', '')

                if not link:
                    continue

                title = entry.get('title', 'No Title')
                
                # --- 2. METADATA ENRICHMENT ---
                authors = [a.name for a in entry.get('authors', [])]
                tags = [t.get('term') for t in entry.get('tags', [])]
                
                header_info = []
                if authors: header_info.append(f"AUTHORS: {', '.join(authors)}")
                if tags: header_info.append(f"TAGS: {', '.join(tags)}")
                
                clean_text = clean_html(entry.get('summary', ''))
                final_summary = " | ".join(header_info + [clean_text])

                # --- 3. DATE PARSING ---
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    dt = datetime.datetime(*entry.published_parsed[:6])
                    published = dt.strftime('%Y-%m-%d %H:%M:%S')
                elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                    # Atom feeds often use 'updated' instead of 'published'
                    dt = datetime.datetime(*entry.updated_parsed[:6])
                    published = dt.strftime('%Y-%m-%d %H:%M:%S')
                else:
                    published = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

                try:
                    c.execute('''INSERT OR IGNORE INTO articles VALUES (?, ?, ?, ?, ?, ?)''', 
                              (link, title, final_summary, published, source_id, datetime.datetime.now().isoformat()))
                    if c.rowcount > 0:
                        total_new_items += 1
                        print(f"   [NEW] {title[:50]}...")
                except Exception as e:
                    print(f"   Error saving DB: {e}")

        except Exception as e:
            print(f"   Failed to parse feed: {e}")

    conn.commit()
    conn.close()
    print(f"--- Finished. Added {total_new_items} articles. ---")

if __name__ == "__main__":
    ingest()