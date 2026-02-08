import os
import re
import time
import html
import sys
import datetime
import feedparser
import psycopg2
from psycopg2.extras import Json
from config import RSS_FEEDS

# Fetch the connection string from environment variables
DB_URL = os.getenv("DATABASE_URL")

# Browser Header to prevent 403 Forbidden
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Force UTF-8 for Windows Consoles to prevent print crashes
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

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
    print(f"\n{'='*60}")
    print(f"🚀 STARTING ROBUST INGEST: {datetime.datetime.now()}")
    print(f"{'='*60}\n")
    
    init_db()
    conn = get_db_connection()
    cur = conn.cursor()
    total_new_items = 0

    for feed_idx, feed in enumerate(RSS_FEEDS):
        source_name = feed["source"]
        label_name = feed["label"]
        rss_url = feed["link"]
        
        print(f"\n[{feed_idx+1}/{len(RSS_FEEDS)}] Processing: {source_name} ({label_name})")
        
        # Rate Limit
        if "arxiv" in rss_url.lower() or "inoreader" in rss_url.lower():
            time.sleep(1.0)

        try:
            # Parse
            parsed = feedparser.parse(rss_url, agent=USER_AGENT)
            
            status = getattr(parsed, 'status', 'Unknown')
            if status != 200 and status != 'Unknown':
                print(f"   ❌ FATAL: Server blocked request (HTTP {status})")
                continue

            if not parsed.entries:
                print(f"   ⚠️ WARNING: Feed is empty or parsing failed.")
                continue

            # Loop Entries
            added_this_feed = 0
            
            for i, entry in enumerate(parsed.entries):
                try:
                    link = entry.get('link') or entry.get('id', '')
                    if not link: continue 

                    title = clean_text_content(entry.get('title', 'No Title'))
                    image_url = extract_image(entry)
                    
                    raw_summary = entry.get('summary', '') or entry.get('description', '')
                    if 'content' in entry: raw_summary = entry.content[0].get('value', raw_summary)
                    clean_summary = clean_text_content(raw_summary)
                    
                    # --- FIX: ROBUST AUTHOR EXTRACTION ---
                    # Replaced `a.name` with `a.get('name', '')` to handle empty <dc:creator/>
                    authors_list = []
                    for a in entry.get('authors', []):
                        # Some feeds might return a string, others a dictionary
                        if isinstance(a, str):
                            authors_list.append(clean_text_content(a))
                        else:
                            authors_list.append(clean_text_content(a.get('name', '')))
                    
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
                            added_this_feed += 1
                            try:
                                print(f"        ✅ INSERTED: {title[:40]}...")
                            except:
                                print(f"        ✅ INSERTED: (Special Char Title)")
                            
                            # Commit frequently to save progress
                            if total_new_items % 5 == 0:
                                conn.commit()

                    except Exception as e:
                        print(f"        ❌ DB ERROR on Item {i}: {e}")
                        conn.rollback()

                except Exception as e:
                    print(f"      ❌ PARSING ERROR on Item {i}: {e}")

            print(f"   🏁 Added {added_this_feed} new items.")

        except Exception as e:
            print(f"   🔥 CRITICAL FEED ERROR: {e}")

    conn.commit()
    conn.close()
    print(f"\n{'='*60}")
    print(f"🏁 FINISHED. Total New Articles: {total_new_items}")
    print(f"{'='*60}")

if __name__ == "__main__":
    ingest()