import os
import re
import json
import time
import random
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
from google import genai
from google.genai import types
from prompts2 import (
    BASE_RANKING_PROMPT
)

# --- CONFIGURATION ---
DEBUG_MODE = True
DEBUG_FOLDER = "debug"

PRICING = {
    "gemini-3-pro-preview":        {"input": 2.00,  "output": 12.00}, # >200k: $4.00 / $18.00
    "gemini-3-flash-preview":      {"input": 0.50,  "output": 3.00},
    "gemini-3-pro-image-preview":  {"input": 2.00,  "output": 12.00}, # Text generation pricing
    "gemini-2.5-pro":              {"input": 1.25,  "output": 10.00}, # >200k: $2.50 / $15.00
    "gemini-2.5-flash":            {"input": 0.30,  "output": 2.50},
    "gemini-2.5-flash-preview-09-2025": {"input": 0.30, "output": 2.50},
    "gemini-2.5-flash-lite":       {"input": 0.10,  "output": 0.40},
    "gemini-2.5-flash-lite-preview-09-2025": {"input": 0.10, "output": 0.40},
    "gemini-2.5-flash-image":      {"input": 0.30,  "output": 2.50},  # Text pricing
    "gemini-2.5-flash-native-audio-preview-12-2025": {"input": 0.50, "output": 2.00},
    "gemini-2.5-computer-use-preview-10-2025": {"input": 1.25, "output": 10.00}, # >200k: $2.50 / $15.00
    "gemini-2.0-flash":            {"input": 0.10,  "output": 0.40},
    "gemini-2.0-flash-lite":       {"input": 0.075, "output": 0.30},
    "gemini-robotics-er-1.5-preview": {"input": 0.30, "output": 2.50},
    "gemini-embedding-001":        {"input": 0.15,  "output": 0.00},
    "gemini-2.5-flash-preview-tts": {"input": 0.50, "output": 10.00}, 
    "gemini-2.5-pro-preview-tts":   {"input": 1.00, "output": 20.00},
}

# --- HELPERS ---
def _debug_dump(filename, data, description=""):
    """Helper to dump intermediate data to JSON files for debugging."""
    if not DEBUG_MODE:
        return
    
    if not os.path.exists(DEBUG_FOLDER):
        os.makedirs(DEBUG_FOLDER)
        
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    clean_name = re.sub(r'[^a-zA-Z0-9_-]', '', filename)
    filepath = os.path.join(DEBUG_FOLDER, f"{timestamp}_{clean_name}.json")
    
    dump_data = {
        "timestamp": timestamp,
        "description": description,
        "data": data
    }
    
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(dump_data, f, indent=2, default=str)
    
    print(f"🐛 [DEBUG] Dumped {filename}")

def normalize_url(u):
    """Normalizes URLs for consistent matching."""
    return u.lower().split('://')[-1].replace('www.', '').strip('/') if u else ""


# --- DATA STRUCTURES ---
class Article:
    def __init__(self, link=None, title=None, summary=None, published=None, source=None, feed_label=None, metadata=None, scraped_at=None):
        self.link = link
        self.title = title
        self.summary = summary
        self.published = published
        self.source = source
        self.feed_label = feed_label
        self.metadata = metadata or {}
        self.scraped_at = scraped_at
    
    def to_xml(self, anchor_id=""):
        """Returns a slim XML representation for the AI context."""
        authors = self.metadata.get('authors', [])
        
        lines = [
            "<article>",
            f"<ID>{anchor_id}</ID>" if anchor_id else None,
            f"<link>{self.link}</link>",
            f"<title>{self.title}</title>",
            f"<author>{', '.join(authors)}</author>" if authors else None,
            f"<summary>{self.summary}</summary>",
            "</article>"
        ]
        return "\n".join(line for line in lines if line)


class Corpus:
    def __init__(self):
        self.articles = []

    def fetch_from_db(self, db_url, query):
        print("--- 📜 Fetching Articles from DB ---")
        try:
            conn = psycopg2.connect(db_url)
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(query)
            rows = cur.fetchall()
            
            self.articles = [
                Article(
                    link=r.get('link'),
                    title=r.get('title'),
                    summary=r.get('summary')[:1500] if r.get('summary') else "",
                    published=r.get('published'),
                    source=r.get('source'),
                    feed_label=r.get('feed_label'),
                    metadata=r.get('metadata'),
                    scraped_at=r.get('scraped_at')
                ) for r in rows
            ]
            
            cur.close()
            conn.close()
            print(f"✅ Loaded {len(self.articles)} articles.")
            
            # Debug dump
            debug_data = [{"title": a.title, "link": a.link} for a in self.articles]
            _debug_dump("1_corpus_fetch", debug_data, f"Fetched {len(self.articles)} rows")
            
        except Exception as e:
            print(f"❌ Database Error: {e}")

    def add_article(self, article):
        self.articles.append(article)


# --- BATCHING LOGIC ---
class BatchingAlgorithm:
    def shuffle(self, size):
        return list(range(size))

class ContextSort(BatchingAlgorithm):
    def shuffle(self, corpus_size, batch_size):
        # Create a deck with 3x redundancy to ensure coverage
        deck = list(range(corpus_size)) * 3
        random.shuffle(deck)
        
        shuffled_order = []
        while deck:
            for i in range(len(deck)):
                candidate = deck[i]
                # Avoid duplicates in the active window (batch)
                recent_items = shuffled_order[-batch_size:]
                if candidate not in recent_items:
                    shuffled_order.append(deck.pop(i))
                    break
            else:
                # If stuck, just pop the first one
                shuffled_order.append(deck.pop(0))
        
        _debug_dump("2_batch_indices", shuffled_order, "Indices after ContextSort")
        
        # Generator yielding chunks
        return [shuffled_order[i:i + batch_size] for i in range(0, len(shuffled_order), batch_size)]


class BatchDeck:
    def __init__(self, corpus, batch_size, algorithm, base_prompt_template):
        self.deck = []
        
        # Get list of lists (indices)
        batches_of_indices = algorithm.shuffle(len(corpus.articles), batch_size)

        for batch_idx, indices in enumerate(batches_of_indices):
            articles_xml = "\n\n".join(
                [corpus.articles[idx].to_xml(str(i+1)) for i, idx in enumerate(indices)]
            )
            
            final_prompt = base_prompt_template.format(articles_text=articles_xml)
            self.deck.append(final_prompt)
            
            # Debug first batch only
            if batch_idx == 0:
                _debug_dump("3_sample_prompt", final_prompt, "First batch prompt content")


# --- AI COMPONENTS ---
class GeminiRunner:
    def __init__(self, api_key, model_name):
        self.client = genai.Client(api_key=api_key)
        self.model = model_name
        self.churn_cost = 0.0

    def run(self, prompt, system_instruction, temperature=0.2):
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json", 
            temperature=temperature
        )

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=config
            )
            
            # Calculate Cost
            if response.usage_metadata:
                in_tok = response.usage_metadata.prompt_token_count
                out_tok = response.usage_metadata.candidates_token_count
                rates = PRICING.get(self.model, {"input": 0, "output": 0})
                cost = (in_tok / 1e6 * rates["input"]) + (out_tok / 1e6 * rates["output"])
                self.churn_cost += cost
                
            return response.text

        except Exception as e:
            print(f"   ⚠️ Gemini API Error: {e}")
            return None


class Judge:
    def __init__(self, system_prompt, model_id, title):
        self.system_prompt = system_prompt
        self.model_id = model_id
        self.title = title
        self.bill = 0.0

class Stage1Judge(Judge):
    def verdict(self, batch_deck):
        print(f"⚖️  {self.title} is deliberating...")
        
        api_key = os.getenv("GEMINI_API_KEY")
        runner = GeminiRunner(api_key, self.model_id)
        
        # Results: { "link": {title, score, rationale} }
        final_results = {}

        for i, batch_prompt in enumerate(batch_deck.deck):
            print(f"   ↳ Batch {i+1}/{len(batch_deck.deck)}...")
            
            success = False
            attempts = 0
            
            while attempts < 3 and not success:
                try:
                    raw_text = runner.run(batch_prompt, self.system_prompt)
                    if not raw_text: raise Exception("Empty response")

                    # Clean JSON
                    clean_text = re.sub(r'```json|```', '', raw_text).strip()
                    clean_text = re.sub(r'[\x00-\x1F\x7F]', '', clean_text)
                    
                    data = json.loads(clean_text)
                    
                    for item in data:
                        link = item.get('link')
                        if link:
                            final_results[link] = {
                                "judge": self.title,
                                "title": item.get('title'),
                                "score": item.get('score', 0),
                                "rationale": item.get('rationale', 'N/A')
                            }
                    success = True
                except Exception as e:
                    attempts += 1
                    time.sleep(attempts * 2)
        
        self.bill += runner.churn_cost
        print(f"   ✅ Finished. Cost: ${self.bill:.4f}")
        
        _debug_dump(f"4_verdict_{self.title}", final_results)
        return final_results


class Stage1Trial:
    def __init__(self, winners_count, judge_configs):
        """
        judge_configs: List of dicts [{"name": "...", "prompt": "...", "weight": 0.4}]
        """
        self.winners_count = winners_count
        self.judge_configs = judge_configs

    def convene(self, corpus, ai_model):
        print("\n--- 🏛️  Convening Stage 1 Trial ---")
        
        # 1. Prepare Batches
        batching_algo = ContextSort()
        deck = BatchDeck(corpus, 8, batching_algo, BASE_RANKING_PROMPT)
        
        all_verdicts = {}
        total_trial_cost = 0.0

        # 2. Run Judges
        for config in self.judge_configs:
            judge = Stage1Judge(config["prompt"], ai_model, config["name"])
            verdict = judge.verdict(deck)
            
            all_verdicts[config["name"]] = verdict
            total_trial_cost += judge.bill

        print(f"💰 Trial Complete. Total Cost: ${total_trial_cost:.4f}")

        # 3. Aggregate Scores
        final_scores = {}
        all_links = set().union(*[v.keys() for v in all_verdicts.values()])
        detailed_debug = {}

        for link in all_links:
            weighted_sum = 0
            breakdown = {}
            
            for config in self.judge_configs:
                name = config["name"]
                weight = config["weight"]
                
                # Default to 0 if judge didn't see/rank this article
                raw_score = all_verdicts[name].get(link, {}).get('score', 0)
                weighted_sum += (raw_score * weight)
                breakdown[name] = raw_score
            
            norm_link = normalize_url(link)
            final_scores[norm_link] = weighted_sum
            detailed_debug[norm_link] = {"weighted": weighted_sum, "breakdown": breakdown, "orig_link": link}

        _debug_dump("5_aggregation", detailed_debug, "Score Aggregation")

        # 4. Sort Winners
        sorted_candidates = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)
        top_links = [item[0] for item in sorted_candidates[:self.winners_count]]

        # 5. Build Result (Sorted)
        winner_corpus = Corpus()
        winner_json = []

        print("\n🏆 Top Selections:")
        for norm_link in top_links:
            # Find original article object
            for art in corpus.articles:
                if normalize_url(art.link) == norm_link:
                    winner_corpus.add_article(art)
                    
                    score_info = detailed_debug.get(norm_link, {})
                    entry = {
                        "title": art.title,
                        "link": art.link,
                        "score": score_info.get("weighted", 0),
                        "scores_breakdown": score_info.get("breakdown", {}),
                        "summary": art.summary[:200] + "..."
                    }
                    winner_json.append(entry)
                    print(f"  {entry['score']:.1f} | {entry['title'][:60]}...")
                    break # Stop searching corpus for this link

        _debug_dump("6_final_winners", winner_json)
        return winner_corpus