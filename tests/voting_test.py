import os
import json
import re
import time
from dotenv import load_dotenv
from google import genai
from google.genai import types
from openai import OpenAI
from anthropic import Anthropic
from prompts2 import DAILY_VOTING_PROMPT

load_dotenv()

# --- CONSTANTS & PRICING ---
CLAUDE_RANK = "claude-opus-4-6"
GEMINI_RANK = "gemini-3-pro-preview"
GPT_RANK = "gpt-5.2"

PRICING = {
    "gemini-3-pro-preview": {"input": 2.00, "output": 12.00},
    "claude-opus-4-6":      {"input": 5.00, "output": 25.00},
}

# --- HARD CODED DATA ---
RAW_DATA_STRING = """
{
  "data": [
    {"title": "How I scraped 5.3 million jobs (including 5,335 data science jobs)", "link": "https://www.reddit.com/r/datascience/comments/1qy7a89/how_i_scraped_53_million_jobs_including_5335_data/", "output": "**The Facts:** The author developed HiringCafe... **Utility:** production-grade LLM-native ETL pipeline..."},
    {"title": "[D][Showcase] MCP-powered Autonomous AI Research Engineer (Claude Desktop, Code Execution)", "link": "https://www.reddit.com/r/MachineLearning/comments/1qyjqlw/dshowcase_mcppowered_autonomous_ai_research/", "output": "**The Facts:** ai-research-agent-mcp released... **Utility:** productivity multiplier for scoping phases..."},
    {"title": "AI is my peacekeeper, saving my sanity in step-parenting.", "link": "https://www.reddit.com/r/artificial/comments/1qyf5ag/ai_is_my_peacekeeper_saving_my_sanity_in/", "output": "**The Facts:** Deleted Reddit post... **Utility:** emotional mediation frontier..."},
    {"title": "30 Agentic AI Interview Questions and Answers: From Beginner to Advanced", "link": "https://www.analyticsvidhya.com/blog/2026/02/agentic-ai-interview-questions-and-answers/", "output": "**The Facts:** AI industry pivoting to loop-based reasoning... **Utility:** validates system architecture skills over prompt engineering..."},
    {"title": "Battle of the chatbots: Anthropic and OpenAI go head-to-head over ads in their AI products", "link": "https://www.theguardian.com/technology/2026/feb/07/ai-chatbots-anthropic-openai-claude-chatgpt", "output": "**The Facts:** Anthropic vs OpenAI on ad-monetization... **Utility:** Impacts data privacy defaults for enterprise..."},
    {"title": "Why has Elon Musk merged his rocket company with his AI startup?", "link": "https://www.theguardian.com/technology/2026/feb/07/why-has-elon-musk-merged-his-rocket-company-with-his-ai-startup", "output": "**The Facts:** SpaceX and xAI merger... **Utility:** orbital compute vision bypasses terrestrial energy limits..."},
    {"title": "J.D. Vance is Booed at the Winter Olympics as a New Poll Shows How Europe Has Turned Against U.S.", "link": "https://time.com/7372884/jd-vance-booed-olympics-europe/", "output": "**The Facts:** Diplomatic collapse in Milan... **Utility:** End of build-once-deploy-global; regional isolation required..."},
    {"title": "Iran Threatens Missile Attacks, Hoping Trump Sees Strength Not Weakness", "link": "https://www.wsj.com/world/middle-east/iran-threatens-missile-attacks-hoping-trump-sees-strength-not-weakness-997c6ab9", "output": "**The Facts:** Geopolitical stalemate... **Utility:** DoD funding surge for computer vision/drone swarms..."},
    {"title": "The U.S. Navy’s New Insurance Policy for War With China Is an Australian Base", "link": "https://www.wsj.com/world/oceania/the-u-s-navys-new-insurance-policy-for-war-with-china-is-an-australian-base-764af616", "output": "**The Facts:** Submarine deployments to Australia... **Utility:** Maritime autonomy and acoustic Edge AI..."},
    {"title": "New York lawmakers propose a three-year pause on new data centers", "link": "https://techcrunch.com/2026/02/07/new-york-lawmakers-propose-a-three-year-pause-on-new-data-centers/", "output": "**The Facts:** NY data center moratorium proposed... **Utility:** Transition from silicon bottlenecks to grid bottlenecks..."},
    {"title": "India has changed its startup rules for deep tech", "link": "https://techcrunch.com/2026/02/07/india-has-changed-its-startup-rules-for-deep-tech/", "output": "**The Facts:** 20-year startup status in India... **Utility:** patient capital for capital-intensive AI infrastructure..."},
    {"title": "Coding agents have replaced every framework I used", "link": "https://blog.alaindichiappari.dev/p/software-engineering-is-back", "output": "**The Facts:** Automated programming makes frameworks obsolete... **Utility:** design agent environments for low-level interaction..."},
    {"title": "Show HN: LocalGPT – A local-first AI assistant in Rust with persistent memory", "link": "https://github.com/localgpt-app/localgpt", "output": "**The Facts:** Standalone Rust binary... **Utility:** lightweight RAG stack without Python dependency..."},
    {"title": "Hjalmarsson: Därför säger jag nej till uranbrytning i vår kommun", "link": "https://news.google.com/rss/articles/...", "output": "**The Facts:** Reversal on uranium mining ban... **Utility:** compute resource budget impact in Nordic regions..."},
    {"title": "Planer på jättelika batterifabriker i Europa skrotas", "link": "https://news.google.com/rss/articles/...", "output": "**The Facts:** Gigafactories cancelled... **Utility:** brownfield optimization opportunity... "}
  ]
}
"""


class VoteTest:
    def __init__(self):
        self.total_cost = 0.0
        self.gemini = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        self.claude = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), timeout=30.0)
        
        self.articles = json.loads(RAW_DATA_STRING)["data"]

    def track(self, model, prompt, response):
        in_tok, out_tok = len(prompt)/4, len(response)/4
        rates = PRICING.get(model, {"input":0, "output":0})
        cost = (in_tok/1e6 * rates["input"]) + (out_tok/1e6 * rates["output"])
        self.total_cost += cost
        return cost

    def fuzzy_match_article(self, ret_title, ret_link):
        """
        Attempts to map a returned (potentially hallucinated) title/link 
        back to an index in self.articles using string similarity.
        """
        best_idx = -1
        best_score = 0.0
        
        # Normalize inputs
        def clean(s): return re.sub(r'\W+', ' ', s or "").lower().strip()
        
        ret_t_clean = clean(ret_title)
        ret_l_clean = clean(ret_link)

        for i, art in enumerate(self.articles):
            score = 0.0
            art_t_clean = clean(art['title'])
            art_l_clean = clean(art['link'])

            # 1. Exact Link Match (Strongest Signal)
            if ret_link and ret_link == art['link']:
                return i # Immediate return
            
            # 2. Substring Link Match
            if ret_link and (ret_link in art['link'] or art['link'] in ret_link):
                score += 0.8
            
            # 3. Title Token Overlap (Jaccard Index)
            t1_tokens = set(ret_t_clean.split())
            t2_tokens = set(art_t_clean.split())
            if t1_tokens and t2_tokens:
                intersection = len(t1_tokens & t2_tokens)
                union = len(t1_tokens | t2_tokens)
                jaccard = intersection / union
                score += jaccard
            
            if score > best_score:
                best_score = score
                best_idx = i
        
        # Threshold to avoid false positives (e.g. matching empty strings)
        if best_score > 0.3: 
            return best_idx
        return -1

    def run(self):
        # Build context for the prompt
        context = ""
        for i, a in enumerate(self.articles):
            # We don't give the ID to the model anymore to force it to use titles
            context += f"- TITLE: {a['title']}\n  LINK: {a['link']}\n  SUMMARY: {a['output'][:200]}\n\n"

        prompt = DAILY_VOTING_PROMPT.format(candidates_text=context)
        votes = {i: 0 for i in range(len(self.articles))}

        # --- 1. GEMINI ---
        print(f"\n🤖 Gemini ({GEMINI_RANK})")
        try:
            print("   ↳ Sending request...")
            res = self.gemini.models.generate_content(
                model=GEMINI_RANK, 
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0
                )
            ).text
            
            print(f"   ↳ Raw JSON: {res[:100]}... [Truncated]")
            data = json.loads(res)
            
            for item in data.get("winners", []):
                idx = self.fuzzy_match_article(item.get("title"), item.get("link"))
                if idx != -1:
                    votes[idx] += 1
                else:
                    print(f"   ⚠️ Could not match: {item.get('title')[:30]}...")
            
            self.track(GEMINI_RANK, prompt, res)
        except Exception as e:
            print(f"   ❌ Error: {e}")

        # --- 2. CLAUDE ---
        print(f"\n🎭 Claude ({CLAUDE_RANK})")
        try:
            print("   ↳ Sending request (Max 30s)...")
            res = self.claude.messages.create(
                model=CLAUDE_RANK, 
                max_tokens=1000, 
                temperature=0.0,
                messages=[{"role":"user","content":prompt}]
            ).content[0].text
            
            print(f"   ↳ Raw Response: {res[:100]}... [Truncated]")
            
            # Extract JSON block using Regex
            match = re.search(r'\{.*\}', res, re.DOTALL)
            if match:
                data = json.loads(match.group())
                for item in data.get("winners", []):
                    idx = self.fuzzy_match_article(item.get("title"), item.get("link"))
                    if idx != -1:
                        votes[idx] += 1
                    else:
                        print(f"   ⚠️ Could not match: {item.get('title')[:30]}...")
            else:
                print("   ❌ No JSON found in response.")
            
            self.track(CLAUDE_RANK, prompt, res)
        except Exception as e:
            print(f"   ❌ Error: {e}")

        # --- FINAL RESULTS ---
        ranked = sorted(votes.items(), key=lambda x: x[1], reverse=True)
        
        print("\n🏆 THE BOARD HAS SPOKEN:")
        print("-" * 30)
        for aid, score in ranked:
            if score > 0:
                label = "UNANIMOUS" if score == 2 else "SPLIT"
                print(f"[{score} Pts - {label}] {self.articles[aid]['title']}")
        
        print("-" * 30)
        print(f"💰 TOTAL TEST COST: ${self.total_cost:.4f}")

if __name__ == "__main__":
    tester = VoteTest()
    tester.run()