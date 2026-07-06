"""소스 타입별 수집기 레지스트리."""
from src.fetchers import arxiv, bluesky, hackernews, hf_papers, reddit, rss, smol_ai

FETCHERS = {
    "rss": rss.fetch,
    "hackernews": hackernews.fetch,
    "reddit": reddit.fetch,
    "arxiv": arxiv.fetch,
    "hf_papers": hf_papers.fetch,
    "smol_ai": smol_ai.fetch,
    "bluesky": bluesky.fetch,
}
