import sqlite3
import json
import os
from config import DB_FOLDER, DB_FILE_NAME

DB_FILE = os.path.join(DB_FOLDER, DB_FILE_NAME)
OUTPUT_FILE = "latest_scraped_data.json"

def export_db_to_json():
    if not os.path.exists(DB_FILE):
        print(f"Error: Database file {DB_FILE} not found.")
        return

    conn = sqlite3.connect(DB_FILE)
    # This allows us to access columns by name (like a dictionary)
    conn.row_factory = sqlite3.Row 
    cursor = conn.cursor()
    
    try:
        # Fetch everything, sorted by most recently scraped
        cursor.execute("SELECT * FROM articles ORDER BY scraped_at DESC")
        rows = cursor.fetchall()
        
        # Convert the SQLite rows into a list of standard Python dictionaries
        data_list = [dict(row) for row in rows]
        
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            # indent=4 makes it pretty-printed and readable in VS Code
            # ensure_ascii=False ensures emojis and special chars print correctly
            json.dump(data_list, f, indent=4, ensure_ascii=False)
                
        print(f"Success! Database exported to {OUTPUT_FILE} ({len(data_list)} items)")
        
    except sqlite3.Error as e:
        print(f"Database error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    export_db_to_json()