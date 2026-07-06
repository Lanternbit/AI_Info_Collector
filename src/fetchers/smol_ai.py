"""smol.ai AI News — X/트위터·Discord·Reddit을 요약한 일간 다이제스트.

X 직접 접근 없이 바이럴 포스트 신호를 얻는 핵심 소스. 다이제스트 본문을 헤딩(토픽)
단위로 분해해 토픽별 개별 아이템으로 변환한다. 분해 실패 시 본문 첫 8,000자를
단일 아이템으로 입력(전역 500자 규칙의 예외)."""
from __future__ import annotations

import re
from html.parser import HTMLParser

import feedparser
import httpx

from src.fetchers.rss import extract_body, parse_entry_date
from src.models import Item, strip_html
from src.net import get_with_retry

MAX_TOPICS = 12
MIN_SECTION_CHARS = 200
FALLBACK_BODY_LIMIT = 8000


class _SectionSplitter(HTMLParser):
    """h1~h3 헤딩 기준으로 (헤딩, 본문 텍스트) 섹션을 수집."""

    HEADINGS = {"h1", "h2", "h3"}

    def __init__(self) -> None:
        super().__init__()
        self.sections: list[tuple[str, str]] = []
        self._current_heading: str | None = None
        self._heading_parts: list[str] = []
        self._body_parts: list[str] = []
        self._in_heading = False

    def _flush(self) -> None:
        if self._current_heading is not None:
            body = re.sub(r"\s+", " ", " ".join(self._body_parts)).strip()
            self.sections.append((self._current_heading, body))
        self._body_parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.HEADINGS:
            self._flush()
            self._in_heading = True
            self._heading_parts = []

    def handle_endtag(self, tag):
        if tag in self.HEADINGS and self._in_heading:
            self._in_heading = False
            heading = re.sub(r"\s+", " ", " ".join(self._heading_parts)).strip()
            self._current_heading = heading or None

    def handle_data(self, data):
        if self._in_heading:
            self._heading_parts.append(data)
        elif self._current_heading is not None:
            self._body_parts.append(data)

    def result(self) -> list[tuple[str, str]]:
        self._flush()
        return self.sections


def split_topics(html: str) -> list[tuple[str, str]]:
    parser = _SectionSplitter()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return []
    return [(h, b) for h, b in parser.result() if len(b) >= MIN_SECTION_CHARS]


def fetch(source: dict, client: httpx.Client, cfg: dict) -> list[Item]:
    resp = get_with_retry(client, source["url"])
    parsed = feedparser.parse(resp.content)
    items: list[Item] = []
    for entry in parsed.entries[:3]:  # 일간 발행 — 최신 며칠치만 (최신성 필터가 마저 거른다)
        link = entry.get("link", "")
        if not link:
            continue
        published = parse_entry_date(entry)
        html = extract_body(entry)
        topics = split_topics(html)
        if len(topics) >= 2:
            for idx, (heading, body) in enumerate(topics[:MAX_TOPICS]):
                items.append(
                    Item(
                        title=f"{heading} — AI News 다이제스트",
                        url=f"{link}#topic-{idx}",
                        source=source["name"],
                        tier=source.get("tier", 1),
                        published=published,
                        body=body[:2000],
                    )
                )
        else:
            items.append(
                Item(
                    title=(entry.get("title") or "(제목 없음)").strip(),
                    url=link,
                    source=source["name"],
                    tier=source.get("tier", 1),
                    published=published,
                    body=strip_html(html)[:FALLBACK_BODY_LIMIT],
                    body_limit=FALLBACK_BODY_LIMIT,
                )
            )
    return items
