import os
import re
import json
import time
import random
import logging
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any, Tuple, Union
from functools import wraps

import psycopg2
from psycopg2.extras import RealDictCursor, Json
import trafilatura

# AI Clients
from google import genai
from google.genai import types
try: 
    from anthropic import Anthropic
except ImportError: 
    Anthropic = None

# Prompts
from system_prompts import (
    BASE_RANKING_PROMPT,
    DAILY_NEWSLETTER_PROMPT_TEMPLATE,
    DAILY_SUMMARY_PROMPT_TEMPLATE,
    DAILY_VOTING_PROMPT_TEMPLATE,
    DAILY_NEWSLETTER_SYSTEM_PROMPT_TEMPLATE,
    BIBLIOGRAPHY_PROMPT
)

# --- CONFIGURATION & CONSTANTS ---
DEBUG_MODE = True
DEBUG_FOLDER = "debug"

class ModelRegistry:
    """Centralized configuration for LLM models and pricing."""
    GEMINI_REASONING = "gemini-3-pro-preview"
    GEMINI_FAST = "gemini-3-flash-preview"
    GEMINI_STABLE = "gemini-2.0-flash"
    CLAUDE_OPUS = "claude-opus-4-6"
    
    PRICING = {
        GEMINI_REASONING: {"input": 2.00, "output": 12.00},
        GEMINI_FAST:      {"input": 0.50, "output": 3.00},
        GEMINI_STABLE:    {"input": 0.10, "output": 0.40},
        CLAUDE_OPUS:      {"input": 5.00, "output": 25.00},
    }

# --- UTILITIES & DECORATORS ---

def retry_policy(retries: int = 5, delay: int = 60, description: str = "Operation"):
    """Decorator pattern for exponential backoff retries."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    print(f"      ⚠️ {description} failed (Attempt {attempt+1}/{retries}). Error: {e}")
                    if attempt < retries - 1:
                        sleep_time = delay * (attempt + 1) # Exponential-ish
                        print(f"      ⏳ Sleeping {sleep_time}s...")
                        time.sleep(sleep_time)
                    else:
                        print(f"      ❌ {description} PERMANENTLY FAILED.")
                        raise e
        return wrapper
    return decorator

def debug_dump(filename: str, data: Any):
    """Writes debug artifacts to disk for auditability."""
    if not DEBUG_MODE: return
    if not os.path.exists(DEBUG_FOLDER): os.makedirs(DEBUG_FOLDER)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    clean_name = re.sub(r'[^a-zA-Z0-9_-]', '', filename)
    filepath = os.path.join(DEBUG_FOLDER, f"{timestamp}_{clean_name}.json")
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump({"timestamp": timestamp, "data": data}, f, indent=2, default=str)

def normalize_url(u: str) -> str:
    if not u: return ""
    return u.lower().split('://')[-1].replace('www.', '').strip('/')

def fuzzy_match_article(ret_title: str, ret_link: str, articles: List['Article']) -> int:
    """Heuristic matching algorithm to correlate LLM output with Source Objects."""
    best_idx = -1
    best_score = 0.0
    
    def clean(s): return re.sub(r'\W+', ' ', s or "").lower().strip()
    
    ret_t_clean = clean(ret_title)
    
    for i, art in enumerate(articles):
        score = 0.0
        art_t_clean = clean(art.title)
        
        # 1. Strong Link Match
        if ret_link and ret_link == art.link: return i
        if ret_link and (ret_link in art.link or art.link in ret_link): score += 0.8
        
        # 2. Token Set Intersection (Jaccard-ish)
        t1 = set(ret_t_clean.split())
        t2 = set(art_t_clean.split())
        if t1 and t2:
            jaccard = len(t1 & t2) / len(t1 | t2)
            score += jaccard
            
        if score > best_score:
            best_score = score
            best_idx = i
            
    if best_score > 0.3: return best_idx
    return -1

# --- DOMAIN ENTITIES ---

@dataclass
class Article:
    """Data Transfer Object representing a single intelligence item."""
    link: str
    title: str
    summary: str
    published: Any
    source: str
    feed_label: str
    metadata: Dict = field(default_factory=dict)
    scraped_at: Any = None

    def to_xml_context(self, anchor_id: str = "") -> str:
        """Serializes article to XML format for LLM context injection."""
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

class ArticleRepository:
    """Data Access Layer for PostgreSQL/Supabase."""
    def __init__(self, db_url: Optional[str] = None):
        self.articles: List[Article] = []
        self.db_url = db_url or os.getenv("DATABASE_URL")

    def fetch_candidates(self, query: str):
        print("--- 📜 Repository: Fetching Candidates ---")
        if not self.db_url: return
        
        conn = None
        try:
            conn = psycopg2.connect(self.db_url)
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(query)
            rows = cur.fetchall()
            
            self.articles = [
                Article(
                    link=r.get('link'),
                    title=r.get('title'),
                    # Truncate summary to save context window tokens if needed
                    summary=r.get('summary')[:1500] if r.get('summary') else "", 
                    published=r.get('published'),
                    source=r.get('source'),
                    feed_label=r.get('feed_label'),
                    metadata=r.get('metadata') or {},
                    scraped_at=r.get('scraped_at')
                ) for r in rows
            ]
            print(f"✅ Loaded {len(self.articles)} candidates.")
        except Exception as e:
            print(f"❌ Repository Error: {e}")
        finally:
            if conn: conn.close()

    def add(self, article: Article):
        self.articles.append(article)

    def upsert_analysis(self, article: Article, analysis: str):
        """Updates the DB with the deep analysis content."""
        if not self.db_url: return
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    # Create table if not exists (Idempotent)
                    cur.execute('''CREATE TABLE IF NOT EXISTS important (link TEXT PRIMARY KEY, title TEXT, summary TEXT, published TIMESTAMP, source TEXT, feed_label TEXT, metadata JSONB, chosen_at TIMESTAMP, rationale TEXT);''')
                    
                    cur.execute("""
                        INSERT INTO important (link, title, summary, published, source, feed_label, metadata, chosen_at, rationale) 
                        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), %s) 
                        ON CONFLICT (link) DO UPDATE 
                        SET rationale = EXCLUDED.rationale, metadata = EXCLUDED.metadata, chosen_at = NOW()
                    """, (article.link, article.title, article.summary, article.published, article.source, article.feed_label, Json(article.metadata), analysis))
        except Exception as e:
            print(f"      ❌ DB Save Error: {e}")

    def save_blog_entry(self, content: str):
        """Persists the final generated newsletter."""
        if not self.db_url: return
        try:
            with psycopg2.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute('''CREATE TABLE IF NOT EXISTS blog_entries (entry_date DATE PRIMARY KEY, content TEXT, created_at TIMESTAMP DEFAULT NOW());''')
                    today = datetime.now().date()
                    cur.execute("""
                        INSERT INTO blog_entries (entry_date, content, created_at) 
                        VALUES (%s, %s, NOW()) 
                        ON CONFLICT (entry_date) DO UPDATE 
                        SET content = EXCLUDED.content, created_at = NOW()
                    """, (today, content))
            print(f"   ✅ Newsletter persisted to DB for date: {datetime.now().date()}")
        except Exception as e:
            print(f"      ❌ Blog DB Error: {e}")



# --- AI INFRASTRUCTURE ---
class GenerativeAIClient:
    """Wrapper for Google GenAI SDK with cost tracking and error handling."""
    def __init__(self, api_key: str, model_name: str):
        self.client = genai.Client(api_key=api_key)
        self.model = model_name
        self.session_cost = 0.0

    @retry_policy(description="Gemini Generation")
    def generate(self, prompt: str, system_instruction: str, temperature: float = 0.2) -> str:
        config = types.GenerateContentConfig(
            system_instruction=system_instruction, 
            response_mime_type="application/json", 
            temperature=temperature
        )
        
        response = self.client.models.generate_content(
            model=self.model, 
            contents=prompt, 
            config=config
        )
        
        # Token Accounting
        if response.usage_metadata:
            in_tok = response.usage_metadata.prompt_token_count
            out_tok = response.usage_metadata.candidates_token_count
            rates = ModelRegistry.PRICING.get(self.model, {"input": 0, "output": 0})
            self.session_cost += (in_tok / 1e6 * rates["input"]) + (out_tok / 1e6 * rates["output"])
            
        return response.text

class ContextBatcher:
    """Logic for shuffling and batching articles to prevent position bias."""
    @staticmethod
    def create_batches(repository: ArticleRepository, batch_size: int, template: str) -> List[str]:
        # Context Shuffling Algorithm
        indices = list(range(len(repository.articles)))
        deck = indices * 3
        random.shuffle(deck)
        
        unique_batches = []
        seen_combos = set()
        
        chunked = [deck[i:i + batch_size] for i in range(0, len(deck), batch_size)]
        
        prompts = []
        for chunk in chunked:
            # Dedup within batch
            clean_chunk = list(set(chunk))
            if tuple(sorted(clean_chunk)) in seen_combos: continue
            seen_combos.add(tuple(sorted(clean_chunk)))
            
            xml_block = "\n\n".join([repository.articles[idx].to_xml_context(str(i+1)) for i, idx in enumerate(clean_chunk)])
            prompts.append(template.format(articles_text=xml_block))
            
        return prompts

class HeuristicAgent:
    """An AI Persona that applies specific heuristic criteria to rank articles."""
    def __init__(self, name: str, system_prompt: str, model: str):
        self.name = name
        self.system_prompt = system_prompt
        self.model = model
        self.cost_incurred = 0.0

    def evaluate_batch(self, batch_prompts: List[str]) -> Dict[str, Dict]:
        print(f"⚖️  Agent [{self.name}] is deliberating...")
        api_key = os.getenv("GEMINI_API_KEY")
        client = GenerativeAIClient(api_key, self.model)
        
        results = {}
        
        for i, prompt in enumerate(batch_prompts):
            print(f"   ↳ Batch {i+1}/{len(batch_prompts)}...")
            try:
                raw_json = client.generate(prompt, self.system_prompt)
                # Cleaning JSON markdown if present
                clean_json = re.sub(r'```json|```', '', raw_json).strip()
                data = json.loads(clean_json)
                
                for item in data:
                    link = item.get('link')
                    if link:
                        results[link] = {
                            "judge": self.name,
                            "title": item.get('title'),
                            "score": item.get('score', 0),
                            "rationale": item.get('rationale', 'N/A')
                        }
            except Exception as e:
                print(f"      ⚠️ parsing error: {e}")

        self.cost_incurred += client.session_cost
        print(f"   ✅ Agent Finished. Cost: ${self.cost_incurred:.4f}")
        return results

class FilteringPipeline:
    """Orchestrates Stage 1: Reducing the noise using Heuristic Agents."""
    def __init__(self, target_count: int, agent_configs: List[Dict]):
        self.target_count = target_count
        self.agent_configs = agent_configs

    def execute(self, repository: ArticleRepository, default_model: str) -> Tuple[ArticleRepository, float]:
        print("\n--- 🏛️  Executing Filtering Pipeline ---")
        
        # 1. Prepare Data
        batches = ContextBatcher.create_batches(repository, 8, BASE_RANKING_PROMPT)
        
        # 2. Run Agents
        all_verdicts = {}
        total_cost = 0.0
        
        for config in self.agent_configs:
            agent = HeuristicAgent(config["name"], config["prompt"], default_model)
            verdicts = agent.evaluate_batch(batches)
            all_verdicts[config["name"]] = verdicts
            total_cost += agent.cost_incurred
            
        # 3. Aggregation & Weighted Scoring
        final_scores = {}
        # Union of all links seen
        all_links = set().union(*[v.keys() for v in all_verdicts.values()])
        
        debug_map = {}
        
        for link in all_links:
            weighted_sum = 0
            breakdown = {}
            for config in self.agent_configs:
                agent_name = config["name"]
                weight = config["weight"]
                raw_score = all_verdicts[agent_name].get(link, {}).get('score', 0)
                weighted_sum += (raw_score * weight)
                breakdown[agent_name] = raw_score
            
            norm_link = normalize_url(link)
            final_scores[norm_link] = weighted_sum
            debug_map[norm_link] = {"weighted": weighted_sum, "breakdown": breakdown}

        # 4. Selection
        sorted_links = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)
        top_normalized_links = [x[0] for x in sorted_links[:self.target_count]]
        
        winner_repo = ArticleRepository()
        
        print("\n🏆 Pipeline Selections:")
        for norm_link in top_normalized_links:
            # Find original article object
            for art in repository.articles:
                if normalize_url(art.link) == norm_link:
                    winner_repo.add(art)
                    score_data = debug_map.get(norm_link, {})
                    print(f"  {score_data.get('weighted',0):.1f} | {art.title[:60]}...")
                    break
                    
        return winner_repo, total_cost

class IntelligencePipeline:
    """
    The Core Engine.
    Orchestrates the flow from Raw Data -> Deep Analysis -> Consensus Voting -> Newsletter.
    """
    def __init__(self, db_url: Optional[str] = None):
        self.repo = ArticleRepository(db_url)
        self.summarized_articles: List[Article] = []
        self.total_cost = 0.0
        
        # Initialize Clients
        self.gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        self.anthropic_client = None
        if os.getenv("ANTHROPIC_API_KEY") and Anthropic:
            self.anthropic_client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), timeout=600.0)

    def _track_cost(self, model: str, input_chars: int, output_chars: int):
        in_tok = input_chars / 4
        out_tok = output_chars / 4
        rates = ModelRegistry.PRICING.get(model, {"input": 0, "output": 0})
        self.total_cost += (in_tok / 1e6 * rates["input"]) + (out_tok / 1e6 * rates["output"])

    def run_deep_analysis(self, repository: ArticleRepository):
        """Stage 1 of Daily Cycle: Scrape and Analyze full text."""
        print(f"\n🕵️  Analysis Phase: Processing {len(repository.articles)} articles...")
        stage_debug = []
        
        for idx, art in enumerate(repository.articles):
            print(f"   [{idx+1}/{len(repository.articles)}] Analyzing: {art.title[:50]}...")
            
            # Scrape
            try:
                downloaded = trafilatura.fetch_url(art.link)
                full_text = trafilatura.extract(downloaded) if downloaded else ""
            except Exception:
                full_text = ""
            
            # Fallback to summary if scrape fails
            if not full_text or len(full_text) < 300: 
                full_text = art.summary
                
            prompt = DAILY_SUMMARY_PROMPT_TEMPLATE.format(full_text=full_text[:25000])
            
            # AI Analysis
            try:
                # Direct client call for simplicity or wrap in GenerativeAIClient
                # Using gemini-3-pro for high quality analysis
                model = ModelRegistry.GEMINI_REASONING
                
                @retry_policy(description="Analysis Generation")
                def _call_api():
                    return self.gemini_client.models.generate_content(model=model, contents=prompt).text
                
                analysis = _call_api()
                self._track_cost(model, len(prompt), len(analysis))
                
                stage_debug.append({"title": art.title, "analysis": analysis})
            except Exception:
                analysis = f"Analysis failed. Original Summary: {art.summary}"

            # Hydrate Object & Persist
            art.metadata['deep_analysis'] = analysis
            self.summarized_articles.append(art)
            self.repo.upsert_analysis(art, analysis)
            
        if SHOW_FULL_JSON_OUTPUT: debug_dump("stage_analysis", stage_debug)
        print(f"   💰 Cumulative Cost: ${self.total_cost:.4f}")

    def run_consensus_voting(self):
        """Stage 2: The Ensemble Vote (Gemini + Claude)."""
        if not self.summarized_articles: return []
        
        print(f"\n🗳️  Consensus Phase: Board of Directors Voting...")
        
        # Prepare Candidate List
        candidates_text = ""
        for art in self.summarized_articles:
            snippet = art.metadata.get('deep_analysis', '')[:400].replace("\n", " ")
            candidates_text += f"- TITLE: {art.title}\n  LINK: {art.link}\n  SUMMARY: {snippet}\n\n"
            
        voting_prompt = DAILY_VOTING_PROMPT_TEMPLATE.format(candidates_text=candidates_text)
        votes = {i: 0 for i in range(len(self.summarized_articles))}
        
        # --- VOTER 1: GEMINI ---
        try:
            print(f"   🤖 Gemini ({ModelRegistry.GEMINI_REASONING}) is voting...")
            @retry_policy()
            def _vote_gemini():
                return self.gemini_client.models.generate_content(
                    model=ModelRegistry.GEMINI_REASONING, 
                    contents=voting_prompt, 
                    config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0)
                ).text
            
            resp_text = _vote_gemini()
            winners = json.loads(resp_text).get("winners", [])
            self._track_cost(ModelRegistry.GEMINI_REASONING, len(voting_prompt), len(resp_text))
            
            for item in winners:
                idx = fuzzy_match_article(item.get("title"), item.get("link"), self.summarized_articles)
                if idx != -1: votes[idx] += 1
        except Exception as e:
            print(f"      ⚠️ Gemini vote failed: {e}")

        # --- VOTER 2: CLAUDE ---
        if self.anthropic_client:
            try:
                print(f"   🎭 Claude ({ModelRegistry.CLAUDE_OPUS}) is voting...")
                @retry_policy()
                def _vote_claude():
                    return self.anthropic_client.messages.create(
                        model=ModelRegistry.CLAUDE_OPUS, 
                        max_tokens=1000, 
                        temperature=0.0, 
                        messages=[{"role": "user", "content": voting_prompt}]
                    ).content[0].text
                
                txt = _vote_claude()
                self._track_cost(ModelRegistry.CLAUDE_OPUS, len(voting_prompt), len(txt))
                
                # Extract JSON from Claude response
                match = re.search(r'\{.*\}', txt, re.DOTALL)
                if match:
                    winners = json.loads(match.group()).get("winners", [])
                    for item in winners:
                        idx = fuzzy_match_article(item.get("title"), item.get("link"), self.summarized_articles)
                        if idx != -1: votes[idx] += 1
            except Exception as e:
                print(f"      ⚠️ Claude vote failed: {e}")

        # --- TALLY ---
        ranked_indices = sorted(votes, key=votes.get, reverse=True)
        final_selection = []
        
        print("\n   🏆 Consensus Results:")
        for idx in ranked_indices:
            score = votes[idx]
            if score > 0:
                art = self.summarized_articles[idx]
                art.metadata['ensemble_score'] = score
                final_selection.append(art)
                
                # CRITICAL FIX: Persist score to DB for testing/debugging
                self.repo.upsert_analysis(art, art.metadata.get('deep_analysis'))
                
                stars = "★" * score
                print(f"      {stars} (Score {score}): {art.title[:50]}...")
                
        return final_selection

    def generate_newsletter(self) -> str:
        """Stage 3: Synthesis."""
        print("\n✍️  Synthesis Phase: Writing The Daily Briefing...")
        
        if not self.summarized_articles:
            print("   ⚠️ No articles available.")
            return ""

        # --- STEP A: ORGANIZE DATA (DEEP DIVE vs SECTOR WATCH) ---
        deep_dive_text = ""
        sector_watch_text = ""
        
        # Sort by score descending
        sorted_articles = sorted(
            self.summarized_articles, 
            key=lambda x: x.metadata.get('ensemble_score', 0), 
            reverse=True
        )

        for art in sorted_articles:
            score = art.metadata.get('ensemble_score', 0)
            entry = f"TITLE: {art.title}\nLINK: {art.link}\nSCORE: {score}\nANALYSIS: {art.metadata.get('deep_analysis')}\n---\n"
            
            # Logic: Score >= 2 is Deep Dive, else Sector Watch
            if score >= 2:
                deep_dive_text += entry
            else:
                sector_watch_text += entry

        # Fail-safe: Ensure deep dive isn't empty
        if not deep_dive_text and sorted_articles:
            print("   ⚠️ No high scores. Promoting top 3 to Deep Dive.")
            for art in sorted_articles[:3]:
                deep_dive_text += f"TITLE: {art.title}\nLINK: {art.link}\nANALYSIS: {art.metadata.get('deep_analysis')}\n---\n"

        # Bibliography list
        bib_text = "".join([f"- Title: {a.title}\n  URL: {a.link}\n\n" for a in sorted_articles])

        today_str = datetime.now().strftime("%B %d, %Y")
        sys_prompt = DAILY_NEWSLETTER_SYSTEM_PROMPT_TEMPLATE.format(date=today_str)

        @retry_policy()
        def _generate_body():
            print("   🤖 Generating Main Body...")
            return self.gemini_client.models.generate_content(
                model=ModelRegistry.GEMINI_REASONING,
                contents=DAILY_NEWSLETTER_PROMPT_TEMPLATE.format(
                    date=today_str, 
                    deep_dive_block=deep_dive_text, 
                    context_block=sector_watch_text
                ),
                config=types.GenerateContentConfig(
                    system_instruction=sys_prompt, 
                    temperature=0.7, 
                    max_output_tokens=40000
                )
            ).text

        @retry_policy()
        def _generate_bib():
            print("   📚 Generating Bibliography...")
            return self.gemini_client.models.generate_content(
                model=ModelRegistry.GEMINI_REASONING,
                contents=BIBLIOGRAPHY_PROMPT.format(articles_text=bib_text),
                config=types.GenerateContentConfig(temperature=0.3)
            ).text

        try:
            body = _generate_body().strip()
            bib = _generate_bib().strip()
            
            final_content = f"{body}\n\n---\n\n#References\n\n{bib}"
            self.repo.save_blog_entry(final_content)
            
            print(f"   ✅ Briefing generated ({len(final_content)} chars)")
            return final_content
        except Exception as e:
            print(f"   ❌ Synthesis Failed: {e}")
            return ""