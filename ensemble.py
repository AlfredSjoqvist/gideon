import psycopg2
import random
import os
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor
from google import genai
from google.genai import types
from prompts2 import (
    INDUSTRY_STRATEGIST_SYSTEM, 
    RESEARCH_FRONTIERSMAN_SYSTEM, 
    PRAGMATIC_ENGINEER_SYSTEM,
    BASE_RANKING_PROMPT
)

# Simple pricing table for estimation (Update based on your specific model)
PRICING = {
    # --- GEMINI 3 SERIES (Frontier) ---
    "gemini-3-pro-preview":        {"input": 2.00,  "output": 12.00}, # >200k: $4.00 / $18.00
    "gemini-3-flash-preview":      {"input": 0.50,  "output": 3.00},
    "gemini-3-pro-image-preview":  {"input": 2.00,  "output": 12.00}, # Text generation pricing

    # --- GEMINI 2.5 SERIES (Current State-of-the-Art) ---
    "gemini-2.5-pro":              {"input": 1.25,  "output": 10.00}, # >200k: $2.50 / $15.00
    "gemini-2.5-flash":            {"input": 0.30,  "output": 2.50},
    "gemini-2.5-flash-preview-09-2025": {"input": 0.30, "output": 2.50},
    
    # Flash-Lite (High Efficiency)
    "gemini-2.5-flash-lite":       {"input": 0.10,  "output": 0.40},
    "gemini-2.5-flash-lite-preview-09-2025": {"input": 0.10, "output": 0.40},

    # Specialized 2.5 Models
    "gemini-2.5-flash-image":      {"input": 0.30,  "output": 2.50},  # Text pricing
    "gemini-2.5-flash-native-audio-preview-12-2025": {"input": 0.50, "output": 2.00},
    "gemini-2.5-computer-use-preview-10-2025": {"input": 1.25, "output": 10.00}, # >200k: $2.50 / $15.00

    # --- GEMINI 2.0 SERIES (Standard / Balanced) ---
    "gemini-2.0-flash":            {"input": 0.10,  "output": 0.40},
    "gemini-2.0-flash-lite":       {"input": 0.075, "output": 0.30},

    # --- SPECIALIZED / UTILITY ---
    "gemini-robotics-er-1.5-preview": {"input": 0.30, "output": 2.50},
    "gemini-embedding-001":        {"input": 0.15,  "output": 0.00},
    
    # --- TTS Models (Audio Output Pricing) ---
    # Note: Output here is technically calculated per character/second in some contexts, 
    # but mapped here to token equivalent for estimation if token counts are returned.
    "gemini-2.5-flash-preview-tts": {"input": 0.50, "output": 10.00}, 
    "gemini-2.5-pro-preview-tts":   {"input": 1.00, "output": 20.00},
}

load_dotenv()

class Corpus:

    def __init__(self):
        self.articles = []

    def run_query(self, db_url, query):
        
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(query)
        rows = cur.fetchall()

        articles_to_go = [
            Article(
                link=r.get('link'),
                title=r.get('title'),
                summary=r.get('summary')[:1500],
                published=r.get('published'),
                source=r.get('source'),
                feed_label=r.get('feed_label'),
                metadata=r.get('metadata'),
                scraped_at=r.get('scraped_at')
            ) for r in rows
        ]

        for article in articles_to_go:
            self.add_article(article)

        cur.close()
        conn.close()
    
    def add_article(self, article):
        self.articles.append(article)


class Article:

    def __init__(self, link=None, title=None, summary=None, published=None, source=None, feed_label=None, metadata=None, scraped_at=None):
        self.link = link
        self.title = title
        self.summary = summary
        self.published = published
        self.source = source
        self.feed_label = feed_label
        self.metadata = metadata
        self.scraped_at = scraped_at
    
    def XML_repr(self, anchor=""):
        """
        Returns a slim XML representation for the AI.
        Only includes tags that have data, avoiding empty rows.
        """
        # 1. Safely extract authors
        authors_list = self.metadata.get('authors', [])
        
        # 2. Build a list of lines for the XML structure
        lines = [
            "<article>",
            f"<ID>{anchor}</ID>" if anchor else None,
            f"<link>{self.link}</link>",
            f"<title>{self.title}</title>",
            f"<author>{', '.join(authors_list)}</author>" if authors_list else None,
            f"<summary>{self.summary}</summary>",
            "</article>"
        ]

        # 3. Filter out None or empty strings and join with a single newline
        return "\n".join(line for line in lines if line)





class BatchingAlgorithm:

    def shuffle(self, size: int):
        return list(range(size))

class ContextSort(BatchingAlgorithm):

    def shuffle(self, corpus_size, batch_size):
        deck = list(range(corpus_size)) * 3
        random.shuffle(deck)
        shuffled_order = []
        while deck:
            for i in range(len(deck)):
                candidate = deck[i]
                recent_links = [item for item in shuffled_order[-batch_size:]]
                if candidate not in recent_links:
                    shuffled_order.append(deck.pop(i))
                    break
            else:
                shuffled_order.append(deck.pop(0))
        return [shuffled_order[i:i + batch_size] for i in range(0, len(shuffled_order), batch_size)]


class BatchDeck:
    def __init__(self, corpus, batch_size, algorithm, base_prompt):
        deck_alias = algorithm.shuffle(len(corpus.articles), batch_size)

        self.deck = []

        for alias in deck_alias:
            articles_xml = "\n\n\n"
            for i in range(batch_size):
                articles_xml += corpus.articles[alias].XML_repr(str(i+1)) + "\n\n\n"
            batch = base_prompt.format(articles_text=articles_xml)
            self.deck.append(batch)


class AIModel:
    def __init__(self):
        pass




class Gemini(AIModel):
    def __init__(self, gemini_api_key, model):
        self.client = genai.Client(api_key=gemini_api_key)
        self.model = model
        self.churn = 0.0

    def run(self, prompt, system_instruction=None, temperature=0.2):
        """
        Sends a prompt + system instruction to Gemini and tracks cost.
        """
        # Create config with the system instruction
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
            
            # --- Cost Tracking Logic ---
            if response.usage_metadata:
                in_tokens = response.usage_metadata.prompt_token_count
                out_tokens = response.usage_metadata.candidates_token_count
                
                # Get rates or default to 0
                rates = PRICING.get(self.model)
                
                cost = (in_tokens / 1_000_000 * rates["input"]) + \
                       (out_tokens / 1_000_000 * rates["output"])
                
                self.churn += cost
                
            return response.text

        except Exception as e:
            print(f"Gemini API Error: {e}")
            return None


class Judge:
    def __init__(self, system_prompt: str, model_id: str, title="General Judge", bill=0.0):
        self.system_prompt = system_prompt
        self.model_id = model
        self.title = title
        self.bill = bill

class Stage1Judge(Judge):
    def __init__(self, system_prompt, model, title):
        super().__init__(system_prompt, model, title, bill=0.0)
    
    def verdict(self, batch_deck):
        """
        Runs the judge on all batches and returns a dictionary of full results.
        Format: { "url_string": { "title": "...", "score": 85, "rationale": "..." } }
        """
        print(f"⚖️  {self.title} is starting deliberation...")

        gemini_api_key = os.getenv("GEMINI_API_KEY")
        model_name = self.model_id
        
        gemini_runner = Gemini(gemini_api_key, model_name)

        # UPDATED: Stores full objects now, not just integer scores
        final_results = {}
        
        for i, batch_prompt in enumerate(batch_deck.deck):
            print(f"   ↳ Processing Batch {i+1}/{len(batch_deck.deck)}...")
            
            success = False
            attempts = 0
            
            while attempts < 5 and not success:
                try:
                    response_text = gemini_runner.run(
                        prompt=batch_prompt, 
                        system_instruction=self.system_prompt, 
                        temperature=0.2
                    )
                    
                    if not response_text:
                        raise Exception("Empty response from Gemini")

                    # JSON Cleaning
                    clean_json_text = re.sub(r'```json|```', '', response_text).strip()
                    clean_json_text = re.sub(r'[\x00-\x1F\x7F]', '', clean_json_text)
                    
                    batch_results = json.loads(clean_json_text)
                    
                    # Process the list returned by AI
                    for item in batch_results:
                        link = item.get('link')
                        if link:
                            # UPDATED: Storing the whole dictionary
                            final_results[link] = {
                                "judge": self.title,
                                "title": item.get('title'),
                                "score": item.get('score', 0),
                                "rationale": item.get('rationale', 'No rationale provided.')
                            }
                            
                    success = True

                except Exception as e:
                    attempts += 1
                    wait_time = attempts * 2
                    print(f"      ⚠️ Error on batch {i+1} (Attempt {attempts}): {e}. Retrying in {wait_time}s...")
                    time.sleep(wait_time)
            
            if not success:
                print(f"      ❌ Failed to process Batch {i+1} after {attempts} attempts.")

        self.bill += gemini_runner.churn
        
        print(f"✅ {self.title} finished. Total Estimated Cost: ${self.bill:.6f}")
        return final_results





class Stage1Trial:
    def __init__(self, winners, judge_prompts):
        self.winners = winners
        self.judge_prompts = judge_prompts
    
    def convene(self, corpus):
        print("--- 🏛️  Stage 1 Trial Convening ---")

        # 1. Prepare the Deck
        base_prompt = BASE_RANKING_PROMPT
        batch_size = 8 # Keep small to ensure JSON validiy
        # Note: ContextSort expects (size, batch_size) based on your class def
        batching_algo = ContextSort(len(corpus.articles), batch_size) 
        batch_deck = BatchDeck(corpus, batch_size, batching_algo, base_prompt)

        # 2. Define the Panel with Weights
        # Order matches the list passed in __main__: [Industry, Research, Engineering]
        panel_config = [
            {"name": "industry",    "weight": 0.4, "prompt": self.judge_prompts[0]},
            {"name": "research",    "weight": 0.2, "prompt": self.judge_prompts[1]},
            {"name": "engineering", "weight": 0.4, "prompt": self.judge_prompts[2]}
        ]

        all_verdicts = {}
        total_bill = 0.0

        # 3. Execution: Run all judges
        for config in panel_config:
            # Instantiate Judge
            judge = Stage1Judge(config["prompt"], "gemini-2.5-flash", config["name"].title())
            
            # Get results: { "url": {score: 80, ...} }
            verdict = judge.verdict(batch_deck)
            
            all_verdicts[config["name"]] = verdict
            total_bill += judge.bill

        print(f"💰 All Judges Finished. Total Stage 1 Cost: ${total_bill:.6f}")

        # 4. Aggregation: Calculate Weighted Scores
        # Helper to normalize URLs for matching (strips http, www, etc)
        def norm(u): return u.lower().split('://')[-1].replace('www.', '').strip('/') if u else ""

        # Create a master map of { normalized_url: combined_score }
        final_scores = {}
        
        # Get a set of ALL unique links found by ANY judge
        all_found_links = set()
        for role in all_verdicts:
            all_found_links.update(all_verdicts[role].keys())

        for link in all_found_links:
            weighted_score = 0
            
            for config in panel_config:
                role = config["name"]
                weight = config["weight"]
                
                # Get the specific judge's data for this link
                judge_data = all_verdicts[role].get(link, {})
                raw_score = judge_data.get('score', 0)
                
                # Add weighted portion
                weighted_score += (raw_score * weight)
            
            final_scores[norm(link)] = weighted_score

        # 5. Selection: Sort and Pick Winners
        # Sort by score descending
        sorted_links = sorted(final_scores.items(), key=lambda item: item[1], reverse=True)
        
        # Take the top X winners
        winning_normalized_links = {item[0] for item in sorted_links[:self.winners]}

        print(f"📊 Ranking Complete. Top score: {sorted_links[0][1] if sorted_links else 0}")

        # 6. Re-Encapsulation: Build the Output Corpus
        # We need to create a NEW Corpus with the ORIGINAL Article objects
        winner_corpus = Corpus()
        
        for article in corpus.articles:
            if norm(article.link) in winning_normalized_links:
                winner_corpus.add_article(article)
                # Optional: Log who made it
                print(f"  🌟 Promote: {article.title[:50]}...")

        return winner_corpus
        
        




trial = Stage1Trial(5, [INDUSTRY_STRATEGIST_SYSTEM, RESEARCH_FRONTIERSMAN_SYSTEM, PRAGMATIC_ENGINEER_SYSTEM])
    







if __name__ == "__main__":
    DB_URL = os.getenv("DATABASE_URL")

    corpus = Corpus()

    query = """
            SELECT link, title, summary, metadata
            FROM articles 
            WHERE source ILIKE 'Inoreader%' 
            AND feed_label = 'AI News'
            AND published >= now() - interval '24 hours'
        """

    #           
    corpus.run_query(DB_URL, query)

    print(corpus.articles)

    for article in corpus.articles:
        print(article.XML_repr() + "\n\n\n")

