import json
from collections import defaultdict

def normalize_key(text):
    """Normalize strings to catch matches despite case, whitespace, or slug differences."""
    if not text: return ""
    # Remove protocol, www, and trailing slashes for URL matching
    text = text.lower().strip()
    text = text.replace("https://", "").replace("http://", "").replace("www.", "")
    if text.endswith('/'): text = text[:-1]
    return text

def merge_records(records):
    """Merges a list of records into a single consolidated record."""
    if not records: return None
    
    # Use the first record as the base
    base = {
        "link": records[0]["link"],
        "title": records[0]["title"],
        "raw_scores": defaultdict(list),
        "rationales": defaultdict(list)
    }

    for rec in records:
        # Merge Raw Scores
        for cat, scores in rec.get("raw_scores", {}).items():
            base["raw_scores"][cat].extend(scores)
        # Merge Rationales
        for cat, rats in rec.get("rationales", {}).items():
            base["rationales"][cat].extend(rats)
        # Keep the longest title found
        if len(rec.get("title", "")) > len(base["title"]):
            base["title"] = rec["title"]

    # Convert defaultdicts back to regular dicts for JSON serialization
    base["raw_scores"] = dict(base["raw_scores"])
    base["rationales"] = dict(base["rationales"])
    
    # Note: sub_combined and combined_score should be recalculated 
    # after merging, so we leave them out or set to null here.
    return base

def process_file(input_file, output_file):
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Dictionary to group records by a normalized title key
    grouped_data = defaultdict(list)

    for entry in data:
        # We create a unique key based on normalized title
        # You can also use normalized link as a fallback key
        key = normalize_key(entry.get("title", ""))
        grouped_data[key].append(entry)

    final_output = []
    for key, group in grouped_data.items():
        if len(group) > 1:
            final_output.append(merge_records(group))
        else:
            final_output.append(group[0])

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(final_output, f, indent=4)
    
    print(f"Original count: {len(data)}")
    print(f"Cleaned count: {len(final_output)}")
    print(f"Merged {len(data) - len(final_output)} duplicate entries.")

if __name__ == "__main__":
    # Ensure your input filename matches
    process_file('final_aggregated_intelligence.json', 'cleaned_output.json')