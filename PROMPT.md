# AI 프론티어 데일리 브리핑 파이프라인 — 구축 프롬프트

> 사용법: 아래 `---` 이후의 프롬프트 전체를 Claude Code에 붙여넣으세요.
> 이 폴더(news_finder)에서 실행하는 것을 전제로 작성되었습니다.
> 프롬프트 안의 소스 URL·API 사양은 별도 표기가 없는 한 **2026-07-06에 실제 호출로 검증**된 것입니다.

---

# 역할과 목표

너는 시니어 데이터 파이프라인 엔지니어다. 아래 명세에 따라 **"AI 프론티어 데일리 브리핑"** 자동화 시스템을 이 폴더에 처음부터 끝까지 구축하라.

**프로젝트 목적**: 나는 AI 업계 최전선의 소식(모델 릴리스, 연구 결과, 커뮤니티 논의, 연구자 포스트)을 매일 놓치지 않고 따라가려 한다. 유튜브 같은 2차 가공 매체로는 최전선 지식을 얻기 어렵다고 판단해, 공식 블로그·논문·커뮤니티를 직접 수집하고 한국어로 요약하는 나만의 브리핑 시스템을 만든다.

**최종 산출물 3가지**:
1. 매일 아침 자동 갱신되는 **브리핑 웹페이지** — 한국어 요약, 중요도순 정렬, 원문 링크, 날짜별 아카이브
2. 브리핑의 핵심 항목(중요도 3 이상, 최대 30건)이 **Notion 데이터베이스에 매일 누적 저장** — 검색·필터 가능한 영구 아카이브
3. 사람 개입 없이 도는 **일일 자동 스케줄** — 내 PC가 꺼져 있어도 동작해야 한다

# 확정된 기술 결정 (변경하지 말 것)

| 항목 | 결정 | 비고 |
|---|---|---|
| 언어 | Python 3.12 | 단순함, feedparser 생태계 |
| 수집 | feedparser + httpx | RSS/Atom + JSON API |
| 요약/랭킹 | Anthropic API `claude-haiku-4-5`, JSON schema 구조화 출력 | 일 100~300건 처리에 월 $5~10 수준. 모델명은 config에서 교체 가능하게 |
| 웹페이지 | Jinja2 → 정적 HTML, GitHub Pages(main 브랜치 `/docs` 폴더 서빙) | 서버 없음, 폰에서도 접근 가능 |
| 아카이브 | Notion REST API 직접 호출 (SDK 불필요) | 아래 "Notion 연동 사양" 준수 |
| 스케줄 | GitHub Actions cron `0 20 * * *` (UTC 20:00 = KST 05:00) + `workflow_dispatch` | PC 독립적. 5~30분 지연은 허용 (아침 브리핑엔 무관) |
| 날짜/시간 | **모든 날짜 계산은 Asia/Seoul 기준** (`zoneinfo.ZoneInfo("Asia/Seoul")`) | 크론이 UTC로 돌므로 UTC 날짜를 쓰면 브리핑이 매일 하루 전 날짜로 찍힌다. 아카이브 파일명·페이지 헤더·Notion 날짜·seen.json 전부 KST |
| 상태 저장 | `data/seen.json` (URL 해시 + 최초 수집 시각, 30일 지난 항목 purge) — 레포에 커밋 | 재수집 방지 |
| 비밀키 | 로컬 `.env` (git 제외) + GitHub Actions Secrets | `ANTHROPIC_API_KEY`, `NOTION_TOKEN`, `NOTION_DATA_SOURCE_ID` |

주의: GitHub Pages 무료 플랜은 레포와 페이지가 **공개**된다. 브리핑은 민감정보가 아니므로 공개를 기본으로 하되, README에 "비공개가 필요하면 Cloudflare Pages + Cloudflare Access로 이전" 옵션을 한 단락 기록해 둘 것.

# 아키텍처 (파이프라인 순서)

```
수집(모든 소스) → 정규화 + 최신성 필터 → 중복 제거 → LLM 중요도 랭킹 + 한국어 요약
→ HTML 렌더 (docs/index.html + docs/archive/YYYY-MM-DD.html)
→ Notion DB 저장 → git commit & push (Actions에서 실행 시) → Pages 자동 배포
```

- **최신성 필터**: 게시 시각 기준 **최근 36시간 이내**(config에서 조정 가능) 아이템만 통과시켜라. RSS 피드는 보통 수 주치 항목을 담고 있어 이 필터가 없으면 첫 실행 때 과거 기사 수백 건이 브리핑에 쏟아진다. 게시 시각이 없는 피드는 최초 발견 시각을 기준으로 삼는다.
- **중복 제거**: URL 정규화(utm_ 등 트래킹 파라미터 제거) + 제목 유사도. seen.json에 있는 항목은 제외.

# 데이터 소스 (2026-07-06 검증 완료)

소스 목록은 `config/sources.yaml`로 관리해 내가 나중에 직접 추가/삭제할 수 있게 하라. 소스별 수집기는 타입(rss / api)별 공용 구현을 쓰되, 아래 소스별 특이사항을 반드시 반영하라.

**공통 규칙**:
- 모든 HTTP 요청에 고유하고 설명적인 User-Agent를 붙일 것 (예: `news-finder/0.1 (personal daily AI digest; contact: <내 이메일>)`). 제네릭 UA(python-requests 등)는 Reddit·Cloudflare에서 403/429를 받는다. 단, 소스별 특이사항(xAI의 RSS리더 UA 등)이 이 규칙보다 우선한다.
- 파이프라인 자체가 하루 1회 실행이므로 **소스별 폴링 주기 기능은 만들지 말 것**.

## Tier 1 — 공식 랩/기업 (must-have)

| 소스 | URL | 특이사항 |
|---|---|---|
| OpenAI News | `https://openai.com/news/rss.xml` | Cloudflare 뒤에 있음, 정상 UA 필수 |
| Google DeepMind | `https://deepmind.google/blog/rss.xml` | 문제없음 |
| Google AI (The Keyword) | `https://blog.google/technology/ai/rss/` | 제품 중심 |
| Anthropic News | `https://raw.githubusercontent.com/alan-turing-institute/ai-rss-feeds/main/feeds/anthropic-news.xml` | **Anthropic은 자체 RSS 없음.** Turing Institute가 GitHub Actions로 재생성하는 제3자 피드(검증 시 3일 이내 최신). 같은 경로의 `anthropic-research.xml`(연구 피드)도 sources.yaml에 `enabled: false`로 등록. **폴백 스크레이퍼는 구현하지 말 것** — 피드가 죽으면 다른 소스처럼 '실패 소스'로 보고만 하라 |
| Hugging Face Blog | `https://huggingface.co/blog/feed.xml` | 볼륨 높음(커뮤니티 글 포함) |

## Tier 1 — 커뮤니티/논문 (must-have)

| 소스 | URL | 특이사항 |
|---|---|---|
| Hacker News | Algolia API: `https://hn.algolia.com/api/v1/search?tags=front_page&query=<kw>` | JSON, 무인증. **두 쿼리 세트를 모두 실행 후 `story_id`로 통합 dedupe**: ① 키워드(AI, LLM, Claude, GPT, Gemini)별 front_page 조회, ② `search_by_date?tags=story&query=AI&numericFilters=points>100,created_at_i>어제` 고득점 최신글. points·댓글 수를 아이템에 보존 |
| Reddit | `https://old.reddit.com/r/<sub>/top/.rss?t=day` | **`www.reddit.com/*.json` 무인증 접근은 403** — 반드시 old.reddit.com의 `.rss` 사용, 설명적 UA 필수, 초당 1요청 이하. 대상: r/LocalLLaMA, r/MachineLearning, r/singularity, r/ClaudeAI, r/OpenAI |
| arXiv | `https://export.arxiv.org/api/query?search_query=cat:cs.CL+OR+cat:cs.LG+OR+cat:cs.AI&sortBy=submittedDate&sortOrder=descending&max_results=100` | **API를 기본으로 사용** (RSS `rss.arxiv.org`는 당일 공지분만 담고 주말·공휴일엔 비어 있음). 요청 간격 3초 이상 |
| HF Daily Papers | `https://huggingface.co/api/daily_papers?limit=50` | 무인증 JSON. `title, summary, upvotes, ai_summary, githubStars` 포함 — 업보트 기반 큐레이션이라 논문 신호로 최상. upvotes를 아이템에 보존. 비공식 엔드포인트임을 주석에 남길 것 |

## Tier 1 — 고신호 뉴스레터/블로그 (must-have)

| 소스 | URL | 특이사항 |
|---|---|---|
| Simon Willison | `https://simonwillison.net/atom/everything/` | 실무자 프론티어 신호로 최상급 |
| Interconnects (Nathan Lambert) | `https://www.interconnects.ai/feed` | Substack |
| Import AI (Jack Clark) | `https://importai.substack.com/feed` | 주간 발행이므로 없는 날이 많음 — 정상 |
| Latent Space | `https://www.latent.space/feed` | 포스트+팟캐스트 |
| smol.ai AI News | `https://news.smol.ai/rss.xml` | **X/트위터·Discord·Reddit을 LLM으로 요약한 일간 다이제스트 — X 직접 접근 없이 바이럴 포스트 신호를 얻는 핵심 소스.** 전용 fetcher 구현: 다이제스트 본문을 헤딩(토픽) 단위로 분해해 **토픽별 개별 아이템**으로 변환하라(각 아이템 링크는 해당 이슈 URL). 분해에 실패하면 본문 첫 8,000자를 단일 아이템으로 LLM에 입력(전역 500자 규칙의 예외). 이 처리를 빠뜨리면 X 신호가 '한 줄짜리 링크'로 소멸한다 |

## Tier 2 — nice-to-have (sources.yaml에 `enabled: false` 기본값으로 등록)

Google Research(`https://research.google/blog/rss/`), Mistral(Turing Institute 생성 피드 `.../feeds/mistral-news.xml`), Meta Engineering(`https://engineering.fb.com/feed/`, AI 카테고리 필터), xAI(`https://openrss.org/x.ai/news`, RSS리더 UA 필요), Microsoft Research(`https://www.microsoft.com/en-us/research/feed/`), Lobsters(`https://lobste.rs/t/ai.rss`), LessWrong(`https://www.lesswrong.com/feed.xml`, karma 필터), TechCrunch AI(`https://techcrunch.com/category/artificial-intelligence/feed/`), Zvi(`https://thezvi.substack.com/feed`), Qwen 공식 블로그(qwenlm.github.io의 RSS — **미검증**, 구현 시 피드 URL 확인; 중국계 프론티어 랩 신호 보강), 주요 오픈소스 릴리스(GitHub Releases Atom: `https://github.com/vllm-project/vllm/releases.atom`, `https://github.com/ggml-org/llama.cpp/releases.atom` — 표준 GitHub 기능이라 안정적)

**Tier 2 구현 범위**: M1에서는 sources.yaml 등록 + 공용 RSS 수집기로 동작하는 것까지만. 소스별 커스텀 로직(Meta 카테고리 필터, LessWrong karma 필터 등)은 M5 이후로 미룬다.

## 쓰지 말 것

- `rsshub.app` 공개 인스턴스 (많은 IP에서 403 — 무인 파이프라인에서 신뢰 불가)
- `blogs.microsoft.com/ai/feed/` (410 Gone)
- Nitter, 계정 쿠키 기반 RSSHub 트위터 라우트 (2026년 현재 사실상 사망 / 계정 정지 위험)

# X(트위터) 소스 전략

2026년 2월부로 X API 무료 티어는 폐지되었고 신규 개발자는 종량제($0.005/포스트 읽기)만 가능하다 — 계정 50개 일일 폴링에 월 $30~80. 따라서:

- **Phase 1 (기본 구현)**: X 직접 접근 없이 간다. ① 위의 smol.ai 전용 fetcher가 바이럴 AI 포스트 신호를 담당. ② **Bluesky 공식 무료 API** 수집기를 구현하라(M5): `https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor=<handle>&filter=posts_no_replies` (무인증, 무료, ToS 문제 없음). **리포스트는 제외**하고 최근 36시간 이내 원본 포스트만 수집. 핸들 목록은 `config/sources.yaml`의 `bluesky_handles` 리스트로 관리.
- **Phase 2 (선택, 지금은 구현하지 말 것)**: X 네이티브 콘텐츠가 정말 아쉬우면 twitterapi.io(~$0.15/1천 건, 단 X ToS 위반 서비스라 언제든 사라질 수 있음) 또는 공식 종량제 API를 붙인다. 이를 대비해 **수집기는 소스 타입별 인터페이스로 추상화**해 나중에 X 수집기만 꽂으면 되게 설계하라.

# LLM 랭킹·요약 사양

- **입력**: 아이템당 `id, 제목, 출처, 게시 시각, 참여 지표(HN points·댓글 수, Reddit 업보트, HF upvotes — 있는 경우), 본문 첫 500자`(smol.ai 예외는 위 참조).
- **호출 방식**: 아이템 100건 단위로 분할 호출. `max_tokens`는 32000 이상으로 지정하고 **스트리밍 API 사용**(대형 max_tokens 비스트리밍 호출은 SDK가 타임아웃 가드로 거부한다). 응답의 `stop_reason == "max_tokens"`이면 해당 배치를 절반으로 재분할해 재시도.
- **구조화 출력** (`output_config`의 `json_schema`로 강제): 아이템별 `{id, title_ko, importance(1~5 정수), category, summary_ko, why_it_matters_ko}`
- 카테고리: `모델 릴리스` / `연구·논문` / `도구·오픈소스` / `업계 동향` / `정책·안전` / `커뮤니티 화제`
- **편집 기준(시스템 프롬프트에 명시)**: 최전선 우선 — 새 모델·새 능력·새 연구 결과 > 자금 조달·인사·가십. 같은 주제 중복 보도는 대표 1건에만 최고점. 참여 지표가 높으면 중요도 판단에 반영. `title_ko`는 자연스러운 한국어 번역 제목, `summary_ko`는 2~3문장, `why_it_matters_ko`는 "이게 왜 중요한가" 1문장. **본문이 없는 아이템(HN/Reddit 링크 포스트 등)은 제목과 지표에서 알 수 있는 사실만 쓰고 내용을 추측하지 말 것.**
- importance 4 이상 → 웹페이지 "오늘의 헤드라인" 섹션.

# 웹페이지 요구사항

- `docs/index.html`(최신) + `docs/archive/YYYY-MM-DD.html`(매일 복사본, KST 날짜), index에서 아카이브 목록 링크
- 외부 CDN 의존 없는 단일 파일 (인라인 CSS, 시스템 폰트), 모바일 우선 반응형, `prefers-color-scheme` 다크모드
- 구성: 날짜 헤더(KST) → 오늘의 헤드라인(importance≥4) → 카테고리별 섹션(중요도순) → 논문 섹션(HF Daily Papers 상위) → footer에 소스 수집 상태("18/20 소스 성공, 실패: X, Y")
- 각 아이템: `title_ko`(원문 링크) + 한국어 요약 + why-it-matters + 출처 배지 + 중요도 표시

# Notion 연동 사양 (2026년 API 기준 — 정확히 따를 것)

- 인증: `Authorization: Bearer <ntn_...토큰>`, 헤더 `Notion-Version: 2025-09-03`으로 고정
- **2025-09-03부터 데이터베이스는 "data source"의 컨테이너다.** 페이지 생성 시 parent는 `{"type": "data_source_id", "data_source_id": "..."}` — `database_id` parent는 쓰지 말 것. `data_source_id`는 `GET /v1/databases/{database_id}` 응답의 `data_sources` 배열에서 얻는다 (README에 이 확인 절차를 기록).
- **스키마 부트스트랩 (M3 첫 단계)**: 내가 만들어 둔 DB는 빈 껍데기다. 파이프라인 설정 스크립트(또는 M3 작업 중 일회성 코드)가 `PATCH /v1/data_sources/{data_source_id}`로 아래 속성들을 자동 생성·검증한 뒤 진행하라 — 사용자가 손으로 속성을 만들게 하지 말 것.
- 속성 스키마: `제목`(title, 한국어 title_ko), `원제`(rich_text), `날짜`(date, KST), `출처`(select), `카테고리`(select), `중요도`(number), `요약`(rich_text — 2,000자 제한 주의), `왜 중요한가`(rich_text), `URL`(url)
- **멱등성**: 저장 전 `POST /v1/data_sources/{id}/query`로 URL 속성이 같은 페이지가 이미 있는지 확인하고 있으면 건너뛰어라. 재실행 시 중복 페이지가 쌓이면 안 된다.
- 레이트 리밋: 평균 초당 3요청 — 순차 저장이면 충분. 429는 `Retry-After` 초를 준수해 재시도, 500/502/503은 지수 백오프로 1~2회 재시도.
- 저장 대상: 그날 importance 3 이상을 **중요도 내림차순으로 최대 30건** (동률이면 Tier 1 소스 우선).

# 운영 품질 요구사항

1. **소스 하나가 죽어도 전체는 죽지 않는다**: 소스별 try/except, 타임아웃 20초, 1회 재시도. 실패 소스는 로그와 웹페이지 footer에 보고.
2. **빈 피드는 정상 상황**: arXiv는 주말·공휴일에 0건이다. 0건 소스는 실패가 아니라 "0건 성공"으로 처리.
3. **dry-run 모드**: `python pipeline.py --dry-run` → Notion 저장과 git 작업 없이 수집~HTML 생성까지만(해당 시점에 구현된 단계까지) 실행, 결과를 콘솔 요약 + 로컬 HTML로 확인.
4. **실행 리포트**: 실행 끝에 "수집 N건 → 최신성/중복 필터 후 M건 → 헤드라인 K건 → Notion 저장 J건"을 로그로 출력.
5. LLM 호출 실패 시 1회 재시도, 그래도 실패하면 요약 없이 원제목만으로 페이지를 생성하고 실패를 명시 (브리핑이 아예 안 나오는 것이 최악).
6. Actions 실패 시 GitHub가 이메일 알림을 보내도록 기본 설정 유지.
7. README 운영 방법에 "로컬에서 실제 실행(비 dry-run) 전 반드시 `git pull`" 한 줄을 기록 (Actions가 커밋한 seen.json과 어긋나면 Notion 중복이 생긴다).

# 레포 구조 (제안 — 합리적 범위에서 조정 가능)

```
news_finder/
├── pipeline.py                 # 엔트리포인트 (--dry-run 지원)
├── src/
│   ├── fetchers/               # rss.py, hackernews.py, reddit.py, arxiv.py, hf_papers.py, smol_ai.py, bluesky.py
│   ├── dedupe.py               # URL 정규화 + 제목 유사도 + 최신성 필터
│   ├── rank.py                 # Claude 호출 (구조화 출력, 배치 분할)
│   ├── render.py               # Jinja2 → docs/
│   └── notion_client.py        # 직접 REST 호출 (부트스트랩 + 멱등 저장)
├── config/sources.yaml         # 소스 목록·enabled 플래그·bluesky_handles·최신성 시간창
├── templates/briefing.html.j2
├── data/seen.json
├── docs/                       # GitHub Pages 루트
├── .github/workflows/daily.yml
├── .env.example
├── requirements.txt
└── README.md                   # 설치·사전준비·운영 방법·결정 기록
```

# GitHub Actions 워크플로 필수 사양 (M4)

`daily.yml`에 다음을 반드시 포함하라 — 하나라도 빠지면 push가 실패한다:

```yaml
permissions:
  contents: write          # 기본 GITHUB_TOKEN은 read-only라 이게 없으면 push가 403
concurrency:
  group: daily
  cancel-in-progress: false
```

- `actions/checkout` 후 `git config user.name "github-actions[bot]"`, `git config user.email "41898282+github-actions[bot]@users.noreply.github.com"` 설정 (러너에는 git identity가 없다)
- push 직전 `git pull --rebase origin main` (실패 시 1회 재시도) — cron과 수동 실행이 겹칠 때 non-fast-forward 거부 방지
- 커밋 대상은 `docs/`와 `data/seen.json`만
- main `/docs` 브랜치 서빙은 GITHUB_TOKEN push로도 Pages 재배포가 트리거되므로 별도 배포 스텝 불필요

# 마일스톤 — 순서대로, 각 단계를 실제 실행해 검증한 뒤 다음으로

**M0. 프로젝트 초기화**: `git init`, `.gitignore` 생성(`.env`, `__pycache__/`, `.venv/` 제외 — `data/seen.json`과 `docs/`는 커밋 대상), venv + requirements.txt.

**M1. 수집기 + dry-run**: sources.yaml의 must-have 소스 전부 + smol.ai 전용 fetcher 구현. 검증 게이트: `--dry-run` 실행 시 실제로 10개 이상 소스에서 아이템이 수집되고, 최신성 필터·중복 제거·실행 리포트가 동작할 것.

**M2. LLM 랭킹·요약 + HTML**: 시작 전에 `.env`의 `ANTHROPIC_API_KEY` 존재를 확인하고 없으면 나에게 요청하라. **목(mock) 호출로 게이트를 대체하지 말 것.** 검증 게이트: 실제 API 호출로 한국어 브리핑 HTML이 생성되고 브라우저에서 열어 확인 가능할 것.

**M3. Notion 연동**: 시작 전에 나에게 `NOTION_TOKEN`과 database URL을 요청하라(아래 사전 준비 참조). 첫 단계로 스키마 부트스트랩 실행. 검증 게이트: 실제 Notion DB에 테스트 아이템이 정확한 속성으로 저장되고, 같은 아이템 재실행 시 중복 생성되지 않을 것.

**M4. GitHub Actions + Pages**: 위 "워크플로 필수 사양"대로 daily.yml 작성. **gh CLI 인증이 확인되면 레포 생성·push·Pages 설정(main `/docs`)을 대행하되, 레포 이름과 public 공개 여부는 실행 전에 나에게 확인받아라.** Actions Secrets 3종은 내가 직접 등록해야 하므로 절차를 안내하고 등록 완료를 확인한 뒤 `workflow_dispatch`로 e2e를 트리거하라. 검증 게이트: e2e 성공 + **실행 로그에서 소스별 성공/실패를 확인해 러너(데이터센터 IP)에서만 실패하는 소스를 식별할 것.** 로컬에서는 되던 Reddit·OpenAI 등이 러너에서 403이면: Reddit은 공식 OAuth script app 자격증명(무료, reddit.com/prefs/apps) 폴백을 구현하거나(이 경우 Reddit 자격증명을 Actions Secrets에 추가 등록) 해당 소스를 `enabled: false` 처리하고 README 결정 기록에 남겨라.

**M5. Bluesky 수집기 + Tier 2 소스별 로직**: AI 연구자·랩 계정 10개 정도를 조사해 `bluesky_handles` 초기값으로 채워라. 검증 게이트: **각 핸들에 대해 `getAuthorFeed` 실제 호출이 200을 반환하고 최근 30일 내 원본 포스트가 1건 이상 있을 것** — 실패 핸들은 제외하고 README에 기록 (다수 연구자가 계정만 있고 비활성이다).

각 마일스톤 완료 시 git commit. 이 문서에 없는 세부 결정은 질문하지 말고 합리적 기본값을 선택한 뒤 README의 "결정 기록" 섹션에 한 줄씩 남겨라.

# 내가 미리 준비해야 하는 것 (시작할 때 체크리스트로 안내하라)

1. **Anthropic API 키** — console.anthropic.com에서 발급 (예상 비용: Haiku 기준 월 $5~10)
2. **Notion 통합** — notion.so/profile/integrations에서 internal integration 생성 → `ntn_` 토큰 복사 → 아카이브용 **빈 데이터베이스(전체 페이지)를 하나 만들고** 해당 DB 페이지의 ⋯ → **Connections에서 통합을 연결** (이걸 빠뜨리면 모든 호출이 404). 속성은 만들 필요 없음 — 파이프라인이 자동 생성한다
3. **GitHub 계정** (M4 시점에 레포 생성 — 이름·공개 여부는 그때 확인받되 기본은 public)
