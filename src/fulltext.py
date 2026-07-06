"""카드 아이템용 원문 본문 추출 — 링크 페이지에서 주요 텍스트만 (댓글·내비게이션 제외).

의존성 없이 표준 HTMLParser 휴리스틱 사용: <article> 내부의 문단을 우선하고,
없으면 페이지 전체의 문단(p/li/헤딩)을 모은다. script/style/nav/footer 등은 제외.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

import httpx

MAX_CHARS = 8000
MIN_BLOCK_CHARS = 30
# 본문 추출이 무의미한 곳 (토론 페이지·SNS — 본문이 아니라 스레드)
SKIP_HOSTS = ("news.ycombinator.com", "reddit.com", "bsky.app", "x.com", "twitter.com", "youtube.com")


class _MainTextParser(HTMLParser):
    _SKIP = {"script", "style", "nav", "header", "footer", "aside", "form", "noscript", "svg", "iframe", "figure", "button"}
    _TEXT = {"p", "li", "h2", "h3", "blockquote", "pre"}

    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self._article = 0
        self._text_depth = 0
        self._buf: list[str] = []
        self.blocks: list[tuple[bool, str]] = []  # (article 내부 여부, 텍스트)

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        elif tag == "article":
            self._article += 1
        elif tag in self._TEXT and not self._skip:
            self._text_depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag == "article":
            self._article = max(0, self._article - 1)
        elif tag in self._TEXT and self._text_depth:
            self._text_depth -= 1
            if self._text_depth == 0:
                text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
                self._buf = []
                if len(text) >= MIN_BLOCK_CHARS:
                    self.blocks.append((self._article > 0, text))

    def handle_data(self, data):
        if self._text_depth and not self._skip:
            self._buf.append(data)


def extract_main_text(html: str) -> str:
    parser = _MainTextParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return ""
    article_blocks = [t for (in_article, t) in parser.blocks if in_article]
    if len(" ".join(article_blocks)) >= 500:
        chosen = article_blocks
    else:
        chosen = [t for (_, t) in parser.blocks]
    return "\n\n".join(chosen)[:MAX_CHARS]


def fetch_fulltext(url: str, user_agent: str) -> str:
    """실패는 빈 문자열 — 본문 추출은 부가 기능이라 파이프라인을 막으면 안 된다."""
    if not url.startswith("http") or any(host in url for host in SKIP_HOSTS):
        return ""
    try:
        resp = httpx.get(url, headers={"User-Agent": user_agent}, timeout=15, follow_redirects=True)
        if resp.status_code != 200 or "html" not in resp.headers.get("content-type", ""):
            return ""
        return extract_main_text(resp.text)
    except Exception:
        return ""
