import feedparser
import sqlite3
import datetime
import os
import re
import time
import html
from config import RSS_FEEDS, DB_FOLDER, DB_FILE_NAME

DB_FILE = os.path.join(DB_FOLDER, DB_FILE_NAME)

def clean_text_content(raw_html):
    """
    Strips HTML tags, unescapes entities, and normalizes whitespace.
    Handles the messy CDATA from Inoreader and HN.
    """
    if not raw_html: 
        return ""
    
    # 1. Decode HTML entities (e.g., &amp; -> &, &quot; -> ")
    text = html.unescape(raw_html)
    
    # 2. Remove HTML tags
    cleanr = re.compile('<.*?>')
    text = re.sub(cleanr, ' ', text)
    
    # 3. Normalize whitespace (removes newlines from ArXiv abstracts)
    return " ".join(text.split())

def init_db():
    if not os.path.exists(DB_FOLDER): 
        os.makedirs(DB_FOLDER)
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # Schema: Simple flat structure as requested
    c.execute('''CREATE TABLE IF NOT EXISTS articles 
                 (link TEXT PRIMARY KEY, 
                  title TEXT, 
                  summary TEXT, 
                  published TEXT, 
                  source_feed TEXT, 
                  scraped_at TEXT)''')
    conn.commit()
    return conn

def parse_date(entry):
    """
    Robust date parsing handling standard RSS (RFC822) and Atom (ISO8601).
    Returns ISO format string 'YYYY-MM-DD HH:MM:SS'.
    """
    dt = None
    
    # Try published_parsed (standard)
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        dt = datetime.datetime(*entry.published_parsed[:6])
        
    # Try updated_parsed (common in Atom/ArXiv)
    elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
        dt = datetime.datetime(*entry.updated_parsed[:6])
        
    # Fallback to now
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
        source_id = f"{source_name}" # e.g. "ArXiv", "HackerNews"
        
        # Respect ArXiv rate limits
        if "ArXiv" in source_name:
            time.sleep(3)

        print(f"Fetching: {source_name}...")
        
        try:
            parsed = feedparser.parse(rss_url)
            
            # Check if feed download was successful
            if parsed.bozo and hasattr(parsed, 'bozo_exception'):
                print(f"  ! Warning: Feed format issue: {parsed.bozo_exception}")

            for entry in parsed.entries:
                # --- 1. ROBUST LINK EXTRACTION ---
                # ArXiv Atom feeds put the abstract link in 'id' or 'link'
                # HackerNews puts the external URL in 'link'
                link = entry.get('link', '')
                if not link:
                    link = entry.get('id', '')
                
                # Deduplication check before processing (Optimization)
                c.execute("SELECT 1 FROM articles WHERE link = ?", (link,))
                if c.fetchone():
                    continue

                title = clean_text_content(entry.get('title', 'No Title'))
                
                # --- 2. METADATA ENRICHMENT (Authors/Tags) ---
                # We flatten list structures into the summary string
                meta_parts = []
                
                # Authors (ArXiv uses <author><name>, HN uses <dc:creator>)
                authors = [clean_text_content(a.name) for a in entry.get('authors', [])]
                if authors: 
                    meta_parts.append(f"AUTHORS: {', '.join(authors[:5])}") # Limit to 5 to save space
                
                # Tags/Categories
                tags = [t.get('term') for t in entry.get('tags', []) if t.get('term')]
                if tags:
                    meta_parts.append(f"TAGS: {', '.join(tags[:5])}")

                # --- 3. SUMMARY CLEANING ---
                # Inoreader puts summary in 'description' (HTML)
                # ArXiv puts abstract in 'summary' (Text with newlines)
                raw_summary = entry.get('summary', '') or entry.get('description', '')
                clean_body = clean_text_content(raw_summary)
                
                # Combine metadata + body
                final_summary = " | ".join(meta_parts + [clean_body])

                # --- 4. DATE PARSING ---
                published = parse_date(entry)

                # --- 5. INSERT ---
                try:
                    c.execute('''INSERT INTO articles VALUES (?, ?, ?, ?, ?, ?)''', 
                              (link, title, final_summary, published, source_id, datetime.datetime.now().isoformat()))
                    total_new_items += 1
                    print(f"   + [NEW] {title[:40]}...")
                except sqlite3.IntegrityError:
                    # Should be caught by the check above, but double safety
                    pass
                except Exception as e:
                    print(f"   ! DB Error: {e}")

        except Exception as e:
            print(f"   ! Failed to parse feed: {e}")

    conn.commit()
    conn.close()
    print(f"--- Finished. Added {total_new_items} articles. ---")

if __name__ == "__main__":
    ingest()