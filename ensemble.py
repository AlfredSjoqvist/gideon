import psycopg2
import random
import os
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor

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

    def shuffle(self, size):
        return list(range(size))

class ContextSort(BatchingAlgorithm):

    def __init__(self, batch_size):
        self.batch_size = batch_size

    def shuffle(self, corpus_size):
        deck = list(range(corpus_size)) * 3
        random.shuffle(deck)
        shuffled_order = []
        while deck:
            for i in range(len(deck)):
                candidate = deck[i]
                recent_links = [item for item in shuffled_order[-self.batch_size:]]
                if candidate not in recent_links:
                    shuffled_order.append(deck.pop(i))
                    break
            else:
                shuffled_order.append(deck.pop(0))
        return [shuffled_order[i:i + self.batch_size] for i in range(0, len(shuffled_order), self.batch_size)]


class BatchDeck:
    def __init__(self, corpus, algorithm):
        self.deck = algorithm.shuffle(len(corpus.articles))
    

DB_URL = os.getenv("DATABASE_URL")

corpus = Corpus()

query = """
        SELECT link, title, summary, metadata
        FROM articles 
        WHERE source ILIKE 'Inoreader%' 
          AND feed_label = 'Reddit AI'
          AND published >= now() - interval '24 hours'
    """

corpus.run_query(DB_URL, query)

print(corpus.articles)

for article in corpus.articles:
    print(article.XML_repr() + "\n\n\n")

