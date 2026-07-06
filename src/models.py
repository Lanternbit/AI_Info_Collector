"""공용 데이터 모델과 유틸리티."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

CATEGORIES = ["모델 릴리스", "연구·논문", "도구·오픈소스", "업계 동향", "정책·안전", "커뮤니티 화제"]

_TRACKING_PARAM = re.compile(r"^(utm_.*|fbclid|gclid|mc_cid|mc_eid|ref|ref_src|source|cmpid)$", re.I)


def now_kst() -> datetime:
    return datetime.now(KST)


def today_kst() -> str:
    return now_kst().strftime("%Y-%m-%d")


def parse_iso_utc(value: str | None) -> datetime | None:
    """ISO 문자열 → tz-aware datetime. 오프셋이 없으면 UTC로 간주 (naive 비교 크래시 방지)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style"}

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self._parts)).strip()


def strip_html(html: str) -> str:
    if not html:
        return ""
    p = _TextExtractor()
    try:
        p.feed(html)
        p.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html).strip()
    return p.text()


def normalize_url(url: str) -> str:
    """중복 판정용 URL 정규화. 트래킹 파라미터 제거, 호스트 소문자화, 트레일링 슬래시 제거.

    프래그먼트(#...)는 유지한다 — smol.ai 토픽 아이템이 앵커로 구분되기 때문."""
    try:
        parts = urlsplit(url.strip())
        query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not _TRACKING_PARAM.match(k)])
        path = parts.path.rstrip("/") or "/"
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, parts.fragment))
    except Exception:
        return url.strip()


def url_key(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()[:16]


@dataclass
class Item:
    title: str
    url: str
    source: str
    tier: int = 2
    published: datetime | None = None  # tz-aware
    body: str = ""
    metrics: dict = field(default_factory=dict)  # points/comments/upvotes 등
    body_limit: int = 500  # LLM 입력 시 본문 절단 길이 (smol.ai 폴백만 8000)
    is_paper: bool = False  # HF Daily Papers → 논문 섹션 전용
    # LLM 랭킹 결과
    title_ko: str = ""
    importance: int = 0
    category: str = ""
    summary_ko: str = ""
    why_ko: str = ""

    @property
    def key(self) -> str:
        return url_key(self.url)

    @property
    def display_title(self) -> str:
        return self.title_ko or self.title
