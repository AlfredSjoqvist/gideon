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

def clean_text_content(raw_html):
    """
    Robust cleaner: Strips HTML, unescapes entities, and fixes whitespace.
    """
    if not raw_html: 
        return ""
    
    # 1. Decode HTML entities (e.g., &amp; -> &)
    text = html.unescape(raw_html)
    
    # 2. Remove HTML tags
    cleanr = re.compile('<.*?>')
    text = re.sub(cleanr, ' ', text)
    
    # 3. Normalize whitespace (removes newlines and multiple spaces)
    return " ".join(text.split())

def get_db_connection():
    if not DB_URL:
        raise ValueError("DATABASE_URL is not set.")
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
    """
    Universal date parser using feedparser's built-in normalization.
    """
    dt = None
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        dt = datetime.datetime(*entry.published_parsed[:6])
    elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
        dt = datetime.datetime(*entry.updated_parsed[:6])
    
    if not dt:
        dt = datetime.datetime.now()
    return dt

def extract_image(entry):
    """
    Tries 4 different ways to find an image in an RSS entry.
    """
    # 1. Check for 'media_content' (common in commercial feeds)
    if 'media_content' in entry:
        for media in entry.media_content:
            if 'image' in media.get('medium', 'image'): # Ensure it's an image
                return media.get('url')

    # 2. Check for 'media_thumbnail' (YouTube, some blogs)
    if 'media_thumbnail' in entry:
        return entry.media_thumbnail[0].get('url')

    # 3. Check for 'enclosures' (Ars Technica, Podcasts)
    if 'enclosures' in entry:
        for enc in entry.enclosures:
            if enc.get('type', '').startswith('image/'):
                return enc.get('href')

    # 4. Fallback: Regex scrape from Description or Content
    # We combine them to search everything
    content_blob = entry.get('summary', '')
    if 'content' in entry:
        for c in entry.content:
            content_blob += c.get('value', '')
            
    img_match = re.search(r'<img [^>]*src="([^"]+)"', content_blob)
    if img_match:
        return img_match.group(1)
        
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
        
        # Rate limit for ArXiv to be polite
        if "arxiv" in rss_url.lower():
            time.sleep(3)

        print(f"Fetching: {source_name}...")
        
        try:
            parsed = feedparser.parse(rss_url)
            
            # Warn if the feed is broken but try anyway
            if parsed.bozo:
                print(f"  ⚠️ Warning: Malformed XML in {source_name}")

            for entry in parsed.entries:
                # --- 1. Universal Link Extraction ---
                link = entry.get('link') or entry.get('id', '')
                if not link:
                    continue # Skip if no link found

                title = clean_text_content(entry.get('title', 'No Title'))
                
                # --- 2. Smart Image Extraction ---
                image_url = extract_image(entry)

                # --- 3. Content Strategy ---
                # Prefer full content (Atom), fallback to summary/description (RSS)
                raw_summary = entry.get('summary', '') or entry.get('description', '')
                if 'content' in entry: 
                    # If full content exists (like ArXiv), append it or use it
                    raw_summary = entry.content[0].get('value', raw_summary)

                clean_summary = clean_text_content(raw_summary)
                
                # --- 4. Robust Metadata ---
                # Handle missing authors/tags safely
                authors_list = [clean_text_content(a.name) for a in entry.get('authors', [])]
                tags_list = [t.get('term') for t in entry.get('tags', []) if t.get('term')]
                
                # Get Source URL (Handling Inoreader vs Standard)
                source_url = ""
                if 'source' in entry:
                    # Inoreader puts it in attributes, Feedparser moves attributes to dict
                    source_url = entry.source.get('href', '') or entry.source.get('url', '')

                meta_payload = {
                    "authors": authors_list,
                    "tags": tags_list,
                    "comments_url": entry.get('comments', ''), # Great for Hacker News
                    "thumbnail": image_url,
                    "source_url": source_url,
                    "guid": entry.get('id', '')
                }

                published = parse_date(entry)

                # --- 5. Database Insert ---
                try:
                    cur.execute(
                        """
                        INSERT INTO articles 
                        (link, title, summary, published, source, feed_label, metadata, scraped_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (link) DO NOTHING
                        """,
                        (
                            link, 
                            title, 
                            clean_summary[:5000], # Truncate massive summaries
                            published, 
                            source_name,
                            label_name,
                            Json(meta_payload), 
                            datetime.datetime.now()
                        )
                    )
                    
                    if cur.rowcount > 0:
                        total_new_items += 1
                        print(f"   + [NEW] {title[:40]}...")
                
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