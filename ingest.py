import feedparser
import sqlite3
import datetime
import os
import re
import time
import html
import json
from config import RSS_FEEDS, DB_FOLDER, DB_FILE_NAME

DB_FILE = os.path.join(DB_FOLDER, DB_FILE_NAME)

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

def init_db():
    if not os.path.exists(DB_FOLDER): 
        os.makedirs(DB_FOLDER)
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # --- SCHEMA UPDATE ---
    # We added a 'metadata' column to store JSON (Authors, Tags, etc.)
    # 'summary' now contains ONLY the clean text content.
    c.execute('''CREATE TABLE IF NOT EXISTS articles 
                 (link TEXT PRIMARY KEY, 
                  title TEXT, 
                  summary TEXT, 
                  published TEXT, 
                  source_feed TEXT, 
                  metadata TEXT, 
                  scraped_at TEXT)''')
    conn.commit()
    return conn

def parse_date(entry):
    dt = None
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        dt = datetime.datetime(*entry.published_parsed[:6])
    elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
        dt = datetime.datetime(*entry.updated_parsed[:6])
        
    if not dt:
        dt = datetime.datetime.now()
        
    return dt.strftime('%Y-%m-%d %H:%M:%S')

def ingest():
    print(f"--- Starting Ingest: {datetime.datetime.now()} ---")
    conn = init_db()
    c = conn.cursor()
    total_new_items = 0

    for feed in RSS_FEEDS:
        source_name = feed["source"]
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
                
                # Check duplication
                c.execute("SELECT 1 FROM articles WHERE link = ?", (link,))
                if c.fetchone():
                    continue

                title = clean_text_content(entry.get('title', 'No Title'))
                
                # --- 2. STRUCTURED METADATA ---
                # Instead of merging strings, we build a dictionary
                meta_payload = {
                    "authors": [clean_text_content(a.name) for a in entry.get('authors', [])],
                    "tags": [t.get('term') for t in entry.get('tags', []) if t.get('term')],
                    "comments_url": entry.get('comments', ''), # For HN
                    "raw_score": entry.get('points', None) # Sometimes available in HN RSS extensions
                }
                
                # Convert dictionary to JSON string for storage
                metadata_json = json.dumps(meta_payload)

                # --- 3. CLEAN SUMMARY ---
                # Keep this pure text. No "AUTHORS:" prefixes.
                raw_summary = entry.get('summary', '') or entry.get('description', '')
                clean_summary = clean_text_content(raw_summary)

                # --- 4. DATE ---
                published = parse_date(entry)

                # --- 5. INSERT ---
                try:
                    c.execute('''INSERT INTO articles VALUES (?, ?, ?, ?, ?, ?, ?)''', 
                              (link, title, clean_summary, published, source_name, metadata_json, datetime.datetime.now().isoformat()))
                    total_new_items += 1
                    print(f"   + [NEW] {title[:40]}...")
                except Exception as e:
                    print(f"   ! DB Error: {e}")

        except Exception as e:
            print(f"   ! Failed to parse feed: {e}")

    conn.commit()
    conn.close()
    print(f"--- Finished. Added {total_new_items} articles. ---")

if __name__ == "__main__":
    ingest()