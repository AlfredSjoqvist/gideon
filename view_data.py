import sqlite3
import os

DB_FILE = "data/news.db"
OUTPUT_FILE = "latest_scraped_data.txt"

def export_db_to_txt():
    if not os.path.exists(DB_FILE):
        print(f"Error: Database file {DB_FILE} not found.")
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    try:
        # Fetch everything from the articles table
        cursor.execute("SELECT * FROM articles ORDER BY scraped_at DESC")
        rows = cursor.fetchall()
        
        # Get column names to use as headers
        column_names = [description[0] for description in cursor.description]
        
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            # Write headers
            f.write(f"{' | '.join(column_names)}\n")
            f.write("-" * 100 + "\n")
            
            # Write data rows
            for row in rows:
                # Convert each element in the row to a string and join them
                line = " | ".join(str(item) for item in row)
                f.write(line + "\n")
                
        print(f"Success! Entire database printed to {OUTPUT_FILE}")
        
    except sqlite3.Error as e:
        print(f"Database error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    export_db_to_txt()