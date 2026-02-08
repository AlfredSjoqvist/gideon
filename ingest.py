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
DB_URL = os.getenv("DATABASE_URL")

# Browser Header to prevent 403 Forbidden
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def clean_text_content(raw_html):
    if not raw_html: return ""
    text = html.unescape(raw_html)
    cleanr = re.compile('<.*?>')
    text = re.sub(cleanr, ' ', text)
    return " ".join(text.split())

def get_db_connection():
    if not DB_URL: raise ValueError("DATABASE_URL is not set.")
    return psycopg2.connect(DB_URL)

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
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
    dt = None
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        dt = datetime.datetime(*entry.published_parsed[:6])
    elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
        dt = datetime.datetime(*entry.updated_parsed[:6])
    if not dt: dt = datetime.datetime.now()
    return dt

def extract_image(entry):
    if 'media_content' in entry:
        for media in entry.media_content:
            if 'image' in media.get('medium', 'image'): return media.get('url')
    if 'media_thumbnail' in entry: return entry.media_thumbnail[0].get('url')
    if 'enclosures' in entry:
        for enc in entry.enclosures:
            if enc.get('type', '').startswith('image/'): return enc.get('href')
    content_blob = entry.get('summary', '')
    if 'content' in entry:
        for c in entry.content: content_blob += c.get('value', '')
    img_match = re.search(r'<img [^>]*src="([^"]+)"', content_blob)
    if img_match: return img_match.group(1)
    return None

def ingest():
    print(f"--- ☁️ Starting Universal Cloud Ingest: {datetime.datetime.now()} ---")
    init_db()
    conn = get_db_connection()
    cur = conn.cursor()
    total_new_items = 0

    for feed in RSS_FEEDS:
        source_name = feed["source"]
        label_name = feed["label"]
        rss_url = feed["link"]
        
        # --- FIX: Rate Limit for Inoreader ---
        # 2 second pause prevents hitting the 429 Too Many Requests wall
        if "arxiv" in rss_url.lower() or "inoreader" in rss_url.lower():
            time.sleep(2)

        print(f"Fetching: {source_name} ({label_name})...")
        
        try:
            # Parse with User Agent
            parsed = feedparser.parse(rss_url, agent=USER_AGENT)
            
            # --- DEBUG: Print HTTP Status ---
            # This will tell you if Inoreader is blocking you (429 or 403)
            status = getattr(parsed, 'status', 'Unknown')
            if status != 200 and status != 'Unknown':
                print(f"  ❌ Error: Server returned HTTP {status} (Blocked/Limit)")
                continue

            if not parsed.entries:
                print(f"  ⚠️ Warning: No entries found in feed. (Bozo={parsed.bozo})")

            for entry in parsed.entries:
                link = entry.get('link') or entry.get('id', '')
                if not link: continue 

                title = clean_text_content(entry.get('title', 'No Title'))
                image_url = extract_image(entry)

                raw_summary = entry.get('summary', '') or entry.get('description', '')
                if 'content' in entry: raw_summary = entry.content[0].get('value', raw_summary)
                clean_summary = clean_text_content(raw_summary)
                
                authors_list = [clean_text_content(a.name) for a in entry.get('authors', [])]
                tags_list = [t.get('term') for t in entry.get('tags', []) if t.get('term')]
                
                source_url = ""
                if 'source' in entry:
                    source_url = entry.source.get('href', '') or entry.source.get('url', '')

                meta_payload = {
                    "authors": authors_list,
                    "tags": tags_list,
                    "comments_url": entry.get('comments', ''),
                    "thumbnail": image_url,
                    "source_url": source_url,
                    "guid": entry.get('id', '')
                }

                published = parse_date(entry)

                try:
                    cur.execute(
                        """
                        INSERT INTO articles 
                        (link, title, summary, published, source, feed_label, metadata, scraped_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (link) DO NOTHING
                        """,
                        (link, title, clean_summary[:5000], published, source_name, label_name, Json(meta_payload), datetime.datetime.now())
                    )
                    if cur.rowcount > 0:
                        total_new_items += 1
                        # Use try/except on print to avoid Windows Unicode console crashes
                        try:
                            print(f"   + [NEW] {title[:40]}...")
                        except UnicodeEncodeError:
                            print(f"   + [NEW] (Title contains special characters)")
                
                except Exception as e:
                    print(f"   ! DB Error: {e}")
                    conn.rollback()

        except Exception as e:
            print(f"   ! Failed to parse feed: {e}")

    conn.commit()
    cur.close()
    conn.close()
    print(f"--- Finished. Added {total_new_items} articles. ---")

if __name__ == "__main__":
    ingest()