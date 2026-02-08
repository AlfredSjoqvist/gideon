import os
import re
import json
import time
import random
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from google import genai
from google.genai import types
from prompts2 import (
    BASE_RANKING_PROMPT,
    DAILY_NEWSLETTER_PROMPT_TEMPLATE,
    DAILY_SUMMARY_PROMPT_TEMPLATE,
    DAILY_VOTING_PROMPT_TEMPLATE
)
import trafilatura

try: from anthropic import Anthropic
except ImportError: Anthropic = None

# --- CONFIGURATION ---
DEBUG_MODE = True
DEBUG_FOLDER = "debug"
SHOW_FULL_JSON_OUTPUT = True

PRICING = {
    "gemini-3-pro-preview":        {"input": 2.00,  "output": 12.00},
    "gemini-3-flash-preview":      {"input": 0.50,  "output": 3.00},
    "gemini-2.0-flash":            {"input": 0.10,  "output": 0.40},
    "claude-opus-4-6":             {"input": 5.00,  "output": 25.00},
    "gemini-1.5-pro":              {"input": 3.50,  "output": 10.50}
}

# --- HELPERS ---
def _debug_dump(filename, data, description=""):
    if not DEBUG_MODE: return
    if not os.path.exists(DEBUG_FOLDER): os.makedirs(DEBUG_FOLDER)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    clean_name = re.sub(r'[^a-zA-Z0-9_-]', '', filename)
    filepath = os.path.join(DEBUG_FOLDER, f"{timestamp}_{clean_name}.json")
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump({"timestamp": timestamp, "desc": description, "data": data}, f, indent=2, default=str)

def api_retry(func, retries=5, delay=60, description="API Call"):
    """Retries a function call 5 times with 60s delay on failure."""
    for attempt in range(retries):
        try:
            return func()
        except Exception as e:
            print(f"      ⚠️ {description} failed (Attempt {attempt+1}/{retries}). Error: {e}")
            if attempt < retries - 1:
                print(f"      ⏳ Sleeping {delay}s before retry...")
                time.sleep(delay)
            else:
                print(f"      ❌ {description} PERMANENTLY FAILED.")
                raise e

def normalize_url(u):
    return u.lower().split('://')[-1].replace('www.', '').strip('/') if u else ""

def fuzzy_match_article(ret_title, ret_link, articles):
    best_idx = -1
    best_score = 0.0
    def clean(s): return re.sub(r'\W+', ' ', s or "").lower().strip()
    ret_t_clean = clean(ret_title)
    ret_l_clean = clean(ret_link)

    for i, art in enumerate(articles):
        score = 0.0
        art_t_clean = clean(art.title)
        art_l_clean = clean(art.link)
        if ret_link and ret_link == art.link: return i
        if ret_link and (ret_link in art.link or art.link in ret_link): score += 0.8
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
            self.articles = [Article(link=r.get('link'), title=r.get('title'), summary=r.get('summary')[:1500] if r.get('summary') else "", published=r.get('published'), source=r.get('source'), feed_label=r.get('feed_label'), metadata=r.get('metadata'), scraped_at=r.get('scraped_at')) for r in rows]
            cur.close()
            conn.close()
            print(f"✅ Loaded {len(self.articles)} articles.")
        except Exception as e:
            print(f"❌ Database Error: {e}")
    def add_article(self, article):
        self.articles.append(article)

# --- BATCHING ---
class BatchingAlgorithm:
    def shuffle(self, size): return list(range(size))

class ContextSort(BatchingAlgorithm):
    def shuffle(self, corpus_size, batch_size):
        deck = list(range(corpus_size)) * 3
        random.shuffle(deck)
        shuffled_order = []
        while deck:
            for i in range(len(deck)):
                candidate = deck[i]
                recent_items = shuffled_order[-batch_size:]
                if candidate not in recent_items:
                    shuffled_order.append(deck.pop(i))
                    break
            else:
                shuffled_order.append(deck.pop(0))
        return [shuffled_order[i:i + batch_size] for i in range(0, len(shuffled_order), batch_size)]

class BatchDeck:
    def __init__(self, corpus, batch_size, algorithm, base_prompt_template):
        self.deck = []
        batches_of_indices = algorithm.shuffle(len(corpus.articles), batch_size)
        for batch_idx, indices in enumerate(batches_of_indices):
            articles_xml = "\n\n".join([corpus.articles[idx].to_xml(str(i+1)) for i, idx in enumerate(indices)])
            final_prompt = base_prompt_template.format(articles_text=articles_xml)
            self.deck.append(final_prompt)

# --- AI COMPONENTS ---
class GeminiRunner:
    def __init__(self, api_key, model_name):
        self.client = genai.Client(api_key=api_key)
        self.model = model_name
        self.churn_cost = 0.0

    def run(self, prompt, system_instruction, temperature=0.2):
        config = types.GenerateContentConfig(system_instruction=system_instruction, response_mime_type="application/json", temperature=temperature)
        
        # Wrapped call for retry
        def _call():
            response = self.client.models.generate_content(model=self.model, contents=prompt, config=config)
            if response.usage_metadata:
                in_tok = response.usage_metadata.prompt_token_count
                out_tok = response.usage_metadata.candidates_token_count
                rates = PRICING.get(self.model, {"input": 0, "output": 0})
                cost = (in_tok / 1e6 * rates["input"]) + (out_tok / 1e6 * rates["output"])
                self.churn_cost += cost
            return response.text

        # Using api_retry
        return api_retry(_call, description=f"Gemini {self.model} Run")

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
        final_results = {}
        for i, batch_prompt in enumerate(batch_deck.deck):
            print(f"   ↳ Batch {i+1}/{len(batch_deck.deck)}...")
            success = False
            attempts = 0
            while attempts < 3 and not success:
                try:
                    # runner.run handles its own 5x retry for API errors
                    # This loop handles logical/parsing errors
                    raw_text = runner.run(batch_prompt, self.system_prompt)
                    if not raw_text: raise Exception("Empty response")
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
        return final_results

class Stage1Trial:
    def __init__(self, winners_count, judge_configs):
        self.winners_count = winners_count
        self.judge_configs = judge_configs

    def convene(self, corpus, ai_model):
        print("\n--- 🏛️  Convening Stage 1 Trial ---")
        batching_algo = ContextSort()
        deck = BatchDeck(corpus, 8, batching_algo, BASE_RANKING_PROMPT)
        all_verdicts = {}
        total_trial_cost = 0.0
        for config in self.judge_configs:
            judge = Stage1Judge(config["prompt"], ai_model, config["name"])
            verdict = judge.verdict(deck)
            all_verdicts[config["name"]] = verdict
            total_trial_cost += judge.bill
        print(f"💰 Trial Complete. Total Cost: ${total_trial_cost:.4f}")
        final_scores = {}
        all_links = set().union(*[v.keys() for v in all_verdicts.values()])
        detailed_debug = {}
        for link in all_links:
            weighted_sum = 0
            breakdown = {}
            for config in self.judge_configs:
                name = config["name"]
                weight = config["weight"]
                raw_score = all_verdicts[name].get(link, {}).get('score', 0)
                weighted_sum += (raw_score * weight)
                breakdown[name] = raw_score
            norm_link = normalize_url(link)
            final_scores[norm_link] = weighted_sum
            detailed_debug[norm_link] = {"weighted": weighted_sum, "breakdown": breakdown, "orig_link": link}
        sorted_candidates = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)
        top_links = [item[0] for item in sorted_candidates[:self.winners_count]]
        winner_corpus = Corpus()
        winner_json = []
        print("\n🏆 Top Selections:")
        for norm_link in top_links:
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
                    break
        return winner_corpus, winner_json, total_trial_cost

CLAUDE_RANK = "claude-opus-4-6"
GEMINI_RANK = "gemini-3-pro-preview"
MODEL_SUMMARY = "gemini-3-pro-preview"

class DailyTrial:
    def __init__(self, db_url=None):
        self.db_url = db_url
        self.summarized_articles = []
        self.total_cost = 0.0
        self.gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        # 10 minute timeout for Opus
        self.anthropic_client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), timeout=600.0) if os.getenv("ANTHROPIC_API_KEY") and Anthropic else None

    def _track_cost(self, model, input_chars, output_chars):
        in_tok = input_chars / 4
        out_tok = output_chars / 4
        rates = PRICING.get(model, {"input": 0, "output": 0})
        cost = (in_tok / 1e6 * rates["input"]) + (out_tok / 1e6 * rates["output"])
        self.total_cost += cost
        return cost

    def run_stage_1_analysis(self, corpus):
        print(f"\n🕵️  DailyTrial Stage 1: Deep Analysis on {len(corpus.articles)} articles...")
        stage1_debug = []
        for idx, art in enumerate(corpus.articles):
            print(f"   [{idx+1}/{len(corpus.articles)}] Analyzing: {art.title[:50]}...")
            try:
                downloaded = trafilatura.fetch_url(art.link)
                full_text = trafilatura.extract(downloaded) if downloaded else ""
            except Exception as e:
                print(f"      ⚠️ Scrape failed: {e}")
                full_text = ""
            if not full_text or len(full_text) < 300: full_text = art.summary
            prompt = DAILY_SUMMARY_PROMPT_TEMPLATE.format(full_text=full_text[:25000])
            
            # Wrapped call
            def _analyze():
                resp = self.gemini_client.models.generate_content(model=MODEL_SUMMARY, contents=prompt)
                return resp.text

            try:
                analysis = api_retry(_analyze, description="Stage 1 Analysis")
                cost = self._track_cost(MODEL_SUMMARY, len(prompt), len(analysis))
                stage1_debug.append({"title": art.title, "analysis": analysis, "cost": cost})
            except Exception:
                analysis = f"Analysis failed after retries. Original Summary: {art.summary}"

            art.metadata['deep_analysis'] = analysis
            self.summarized_articles.append(art)
            self._save_to_db(art, analysis)
        if SHOW_FULL_JSON_OUTPUT: _debug_dump("daily_stage1_analysis", stage1_debug)
        print(f"   💰 Cumulative Cost: ${self.total_cost:.4f}")
        return self.summarized_articles

    def run_stage_2_ensemble(self):
        if not self.summarized_articles: return []
        print(f"\n🗳️  DailyTrial Stage 2: The Board of Directors (Gemini & Claude)...")
        candidates_text = ""
        for i, art in enumerate(self.summarized_articles):
            analysis_snippet = art.metadata.get('deep_analysis', '')[:400].replace("\n", " ")
            candidates_text += f"- TITLE: {art.title}\n  LINK: {art.link}\n  SUMMARY: {analysis_snippet}\n\n"
        voting_prompt = DAILY_VOTING_PROMPT_TEMPLATE.format(candidates_text=candidates_text)
        votes = {i: 0 for i in range(len(self.summarized_articles))}
        debug_votes = {"gemini": [], "claude": []}
        
        # Gemini Vote Wrapped
        def _vote_gemini():
            print(f"   🤖 Gemini ({GEMINI_RANK}) is voting...")
            resp = self.gemini_client.models.generate_content(model=GEMINI_RANK, contents=voting_prompt, config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0))
            return resp.text

        try:
            resp_text = api_retry(_vote_gemini, description="Gemini Vote")
            data = json.loads(resp_text)
            winners = data.get("winners", [])
            debug_votes["gemini"] = winners
            self._track_cost(GEMINI_RANK, len(voting_prompt), len(resp_text))
            for item in winners:
                idx = fuzzy_match_article(item.get("title"), item.get("link"), self.summarized_articles)
                if idx != -1: votes[idx] += 1
                else: print(f"      ⚠️ Gemini hallucinated: {item.get('title')[:30]}...")
        except Exception as e: print(f"      ⚠️ Gemini vote failed completely: {e}")

        # Claude Vote Wrapped
        if self.anthropic_client:
            def _vote_claude():
                print(f"   🎭 Claude ({CLAUDE_RANK}) is voting...")
                resp = self.anthropic_client.messages.create(model=CLAUDE_RANK, max_tokens=1000, temperature=0.0, messages=[{"role": "user", "content": voting_prompt}])
                return resp.content[0].text
            try:
                txt = api_retry(_vote_claude, description="Claude Vote")
                self._track_cost(CLAUDE_RANK, len(voting_prompt), len(txt))
                match = re.search(r'\{.*\}', txt, re.DOTALL)
                if match:
                    data = json.loads(match.group())
                    winners = data.get("winners", [])
                    debug_votes["claude"] = winners
                    for item in winners:
                        idx = fuzzy_match_article(item.get("title"), item.get("link"), self.summarized_articles)
                        if idx != -1: votes[idx] += 1
                        else: print(f"      ⚠️ Claude hallucinated: {item.get('title')[:30]}...")
            except Exception as e: print(f"      ⚠️ Claude vote failed completely: {e}")

        if SHOW_FULL_JSON_OUTPUT: _debug_dump("daily_stage2_votes", debug_votes)
        ranked_indices = sorted(votes, key=votes.get, reverse=True)
        final_selection = []
        print("\n   🏆 Ensemble Results:")
        for idx in ranked_indices:
            score = votes[idx]
            if score > 0:
                art = self.summarized_articles[idx]
                art.metadata['ensemble_score'] = score
                final_selection.append(art)
                stars = "★" * score
                print(f"      {stars} (Score {score}): {art.title[:50]}...")
        print(f"   💰 Cumulative Cost: ${self.total_cost:.4f}")
        return final_selection

    def run_stage_3_newsletter(self):
        print("\n✍️  DailyTrial Stage 3: Writing The Daily Briefing (Claude Opus)...")
        if not self.anthropic_client: return ""
        context_block = ""
        for art in self.summarized_articles:
            score = art.metadata.get('ensemble_score', 0)
            importance = "HIGH PRIORITY" if score >= 2 else ("Medium Priority" if score == 1 else "Reference")
            context_block += f"[{importance}] TITLE: {art.title}\nLINK: {art.link}\nSUMMARY: {art.metadata.get('deep_analysis')}\n---\n"
        prompt = DAILY_NEWSLETTER_PROMPT_TEMPLATE.format(context_block=context_block)
        
        # Wrapped
        def _write_newsletter():
            resp = self.anthropic_client.messages.create(model=CLAUDE_RANK, max_tokens=4000, messages=[{"role": "user", "content": prompt}])
            return resp.content[0].text
            
        try:
            text_output = api_retry(_write_newsletter, description="Claude Opus Newsletter")
            self._track_cost(CLAUDE_RANK, len(prompt), len(text_output))
            self._save_blog_entry(text_output)
            if SHOW_FULL_JSON_OUTPUT: _debug_dump("daily_stage3_briefing", {"prompt": prompt, "result": text_output, "total_cost": self.total_cost})
            return text_output
        except Exception as e:
            print(f"   ❌ Newsletter Generation Failed: {e}")
            return ""

    def _save_to_db(self, article, rationale):
        if not self.db_url: return
        try:
            conn = psycopg2.connect(self.db_url)
            cur = conn.cursor()
            cur.execute('''CREATE TABLE IF NOT EXISTS important (link TEXT PRIMARY KEY, title TEXT, summary TEXT, published TIMESTAMP, source TEXT, feed_label TEXT, metadata JSONB, chosen_at TIMESTAMP, rationale TEXT);''')
            cur.execute("""INSERT INTO important (link, title, summary, published, source, feed_label, metadata, chosen_at, rationale) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), %s) ON CONFLICT (link) DO UPDATE SET rationale = EXCLUDED.rationale, metadata = EXCLUDED.metadata, chosen_at = NOW()""", (article.link, article.title, article.summary, article.published, article.source, article.feed_label, Json(article.metadata), rationale))
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e: print(f"      ❌ DB Save Error: {e}")

    def _save_blog_entry(self, content):
        if not self.db_url: return
        try:
            conn = psycopg2.connect(self.db_url)
            cur = conn.cursor()
            cur.execute('''CREATE TABLE IF NOT EXISTS blog_entries (entry_date DATE PRIMARY KEY, content TEXT, created_at TIMESTAMP DEFAULT NOW());''')
            today = datetime.now().date()
            cur.execute("""INSERT INTO blog_entries (entry_date, content, created_at) VALUES (%s, %s, NOW()) ON CONFLICT (entry_date) DO UPDATE SET content = EXCLUDED.content, created_at = NOW()""", (today, content))
            conn.commit()
            cur.close()
            conn.close()
            print(f"   ✅ Blog entry saved to DB for date: {today}")
        except Exception as e: print(f"      ❌ Blog DB Save Error: {e}")