"""Jinja2 → docs/index.html + docs/archive/YYYY-MM-DD.html (KST 날짜)."""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.models import CATEGORIES, Item, now_kst, today_kst

WEEKDAYS_KO = ["월", "화", "수", "목", "금", "토", "일"]
DOCS = Path("docs")
PAPERS_TOP_N = 10


def render(items: list[Item], source_status: list[dict], llm_ok: bool) -> None:
    now = now_kst()
    date_str = f"{now:%Y-%m-%d} ({WEEKDAYS_KO[now.weekday()]})"
    date_slug = today_kst()

    headlines = sorted(
        [i for i in items if i.importance >= 4], key=lambda i: (-i.importance, i.tier)
    )
    papers = sorted(
        [i for i in items if i.is_paper], key=lambda i: -(i.metrics.get("upvotes") or 0)
    )[:PAPERS_TOP_N]
    categories = []
    for cat in CATEGORIES:
        cat_items = sorted(
            [i for i in items if i.category == cat and not i.is_paper],
            key=lambda i: (-i.importance, i.tier),
        )
        if cat_items:
            categories.append((cat, cat_items))
    unranked = [i for i in items if not i.category and not i.is_paper]  # LLM 실패 폴백

    archive_dir = DOCS / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_dates = sorted(
        {p.stem for p in archive_dir.glob("????-??-??.html")} | {date_slug}, reverse=True
    )

    env = Environment(
        loader=FileSystemLoader("templates"), autoescape=select_autoescape(["html", "j2"])
    )
    template = env.get_template("briefing.html.j2")

    def _render(prefix: str) -> str:
        return template.render(
            date_str=date_str,
            generated_at=f"{now:%Y-%m-%d %H:%M} KST",
            headlines=headlines,
            categories=categories,
            papers=papers,
            unranked=unranked,
            llm_ok=llm_ok,
            source_status=source_status,
            archive_dates=archive_dates,
            prefix=prefix,  # index → "archive/", 아카이브 페이지 → ""
            home_href="index.html" if prefix else "../index.html",
            total=len(items),
        )

    (DOCS / "index.html").write_text(_render("archive/"), encoding="utf-8")
    (archive_dir / f"{date_slug}.html").write_text(_render(""), encoding="utf-8")

    # 전체 아카이브 목록 페이지 (헤더 네비게이션은 최근 14일만 보여주므로)
    archive_index = env.get_template("archive_index.html.j2")
    (archive_dir / "index.html").write_text(
        archive_index.render(archive_dates=archive_dates), encoding="utf-8"
    )
