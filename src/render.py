"""Jinja2 → docs/index.html + docs/archive/YYYY-MM-DD.html (KST 날짜).

편집 위계: 오늘의 요약 → 엄선 헤드라인(3~5) → 카테고리(중요도 3+ 카드 / 나머지 접힘)
→ 논문(상위 5 카드 / 나머지 접힘) → 요약 없음(접힘)."""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.models import CATEGORIES, Item, now_kst, today_kst

WEEKDAYS_KO = ["월", "화", "수", "목", "금", "토", "일"]
DOCS = Path("docs")
PAPERS_CARD_N = 5
HEADLINE_FALLBACK_N = 5


def render(items: list[Item], source_status: list[dict], llm_ok: bool, daily_summary: str = "") -> None:
    now = now_kst()
    date_str = f"{now:%Y-%m-%d} ({WEEKDAYS_KO[now.weekday()]})"
    date_slug = today_kst()

    # 헤드라인: 편집 패스 선정분 우선, 없으면 중요도 기반 폴백
    headlines = sorted([i for i in items if i.is_headline], key=lambda i: -i.importance)
    if not headlines:
        headlines = sorted(
            [i for i in items if i.importance >= 4], key=lambda i: (-i.importance, i.tier)
        )[:HEADLINE_FALLBACK_N]
    headline_keys = {i.key for i in headlines}

    # 카테고리 3단 위계: 중요도 4+ 카드 / 3 보이는 한줄 / 1~2 접힘 (헤드라인 중복 제외)
    sections = []
    for idx, cat in enumerate(CATEGORIES):
        cat_items = sorted(
            [i for i in items if i.category == cat and not i.is_paper and i.key not in headline_keys],
            key=lambda i: (-i.importance, i.tier),
        )
        if not cat_items:
            continue
        featured = [i for i in cat_items if i.importance >= 4]
        notable = [i for i in cat_items if i.importance == 3]
        rest = [i for i in cat_items if i.importance < 3]
        sections.append(
            {
                "name": cat,
                "anchor": f"cat-{idx}",
                "featured": featured,
                "notable": notable,
                "rest": rest,
                "count": len(cat_items),
            }
        )

    papers_all = sorted(
        [i for i in items if i.is_paper and i.key not in headline_keys],
        key=lambda i: -(i.metrics.get("upvotes") or 0),
    )
    papers_top = papers_all[:PAPERS_CARD_N]
    papers_rest = papers_all[PAPERS_CARD_N:]

    unranked = [i for i in items if not i.category and not i.is_paper and i.key not in headline_keys]

    toc = [{"anchor": s["anchor"], "label": s["name"], "count": s["count"]} for s in sections]
    if papers_all:
        toc.append({"anchor": "papers", "label": "논문", "count": len(papers_all)})

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
            daily_summary=daily_summary,
            headlines=headlines,
            sections=sections,
            papers_top=papers_top,
            papers_rest=papers_rest,
            unranked=unranked,
            toc=toc,
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
