# AI 프론티어 데일리 브리핑 (news_finder)

AI 업계 최전선 소식을 매일 자동 수집해 **한국어 브리핑 웹페이지**로 만들고 **Notion 데이터베이스에 아카이빙**하는 파이프라인.

```
수집(19개 소스) → 최신성 필터(36h) → 중복 제거 → Claude 중요도 랭킹·한국어 요약
→ HTML 렌더(docs/) → Notion 저장 → git push → GitHub Pages 배포
```

- 매일 **KST 05:00** GitHub Actions에서 자동 실행 (PC 꺼져 있어도 동작)
- 소스: OpenAI·DeepMind·Google·Anthropic·HF 공식 채널, HN·Reddit·arXiv·HF Daily Papers, 고신호 뉴스레터(Simon Willison, Interconnects, smol.ai 등), Bluesky(검증된 활성 계정 14개)
- X(트위터) 신호는 smol.ai AI News 다이제스트(토픽 분해) + Bluesky로 대체 (X API 무료 티어는 2026-02 폐지)

## 사전 준비

1. **Gemini API 키 (무료)** — [aistudio.google.com](https://aistudio.google.com)에서 발급, 카드 등록 불필요.
   무료 티어 한도(하루 1,500 요청)는 이 파이프라인(하루 1~2회 호출)에 충분하다.
   ※ 유료 Anthropic으로 전환하려면 `config/sources.yaml`에서 `llm_provider: anthropic`,
   `model: claude-haiku-4-5`로 바꾸고 `ANTHROPIC_API_KEY`를 넣으면 된다 (월 $1~3 수준).
2. **Notion 통합** — [notion.so/profile/integrations](https://www.notion.so/profile/integrations)에서 internal integration 생성 → `ntn_` 토큰 복사 → 아카이브용 **빈 데이터베이스(전체 페이지)** 생성 → DB 페이지 ⋯ 메뉴 → **Connections에서 통합 연결** (빠뜨리면 모든 호출이 404). 속성은 만들 필요 없음 — 파이프라인이 자동 생성.
3. **GitHub 레포** (Actions 배포 시)

## 로컬 실행

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env   # 열어서 키 입력
.\.venv\Scripts\python.exe pipeline.py --dry-run   # Notion 저장·상태 갱신 없이 테스트
.\.venv\Scripts\python.exe pipeline.py             # 실제 실행
```

- `NOTION_DATABASE_ID`(DB URL의 32자리 hex)만 넣으면 `data_source_id`는 자동 조회되고 로그에 출력된다.
  DB URL 예: `notion.so/사용자명/<DB_ID>?v=...` — `<DB_ID>` 부분.
- `data_source_id` 수동 확인 (2025-09-03 버전부터 데이터베이스는 data source의 컨테이너):

  ```bash
  curl -H "Authorization: Bearer $NOTION_TOKEN" -H "Notion-Version: 2025-09-03" \
    https://api.notion.com/v1/databases/<DB_ID>
  # 응답의 data_sources[0].id 가 NOTION_DATA_SOURCE_ID
  ```
- **로컬에서 실제 실행(비 dry-run) 전 반드시 `git pull`** — Actions가 커밋한 seen.json과 어긋나면 Notion에 중복 저장될 수 있다.

## GitHub 배포 (M4)

1. GitHub에 public 레포 생성 후 push
2. 레포 Settings → Pages → Source: `Deploy from a branch`, Branch: `main` / `/docs`
3. Settings → Secrets and variables → Actions에 등록:
   `GEMINI_API_KEY`, `NOTION_TOKEN`, `NOTION_DATA_SOURCE_ID` (파이프라인 로그에 출력되는 값)
   — anthropic 전환 시에만 `ANTHROPIC_API_KEY` 추가
4. Actions 탭 → daily-briefing → `Run workflow`로 수동 트리거해 e2e 확인
5. 실행 로그 footer에서 소스별 성공/실패 확인 — 러너(데이터센터 IP)에서만 실패하는 소스(특히 Reddit)는
   Reddit OAuth script app 폴백을 붙이거나 `enabled: false` 처리

## 운영

- 소스 추가/삭제: `config/sources.yaml` 수정 (Tier 2 소스는 `enabled: true`로 켜기)
- 브리핑 비공개가 필요하면: GitHub Pages(무료 플랜은 public) 대신 Cloudflare Pages + Cloudflare Access(무료 50인)로 이전하면 인증 걸린 비공개 URL을 만들 수 있다.
- Actions 실패 시 GitHub가 이메일 알림을 보낸다 (기본 설정 유지).

## 결정 기록

- 2026-07-07 M2 게이트에서 `gemini-3-flash-preview`가 피크 시간대 지속 503(과부하)을 보여
  기본 모델을 안정판 `gemini-2.5-flash`로 확정 (폴백: `gemini-2.5-flash-lite`).
  2.5 계열은 thinking 토큰이 출력 한도를 잠식해 절단을 유발하므로 `thinkingBudget: 0`으로 비활성화.
  preview가 안정화되면 config의 model만 바꿔 복귀 가능.
- 2026-07-07 사용자 결정으로 LLM 백엔드를 **Gemini 무료 티어**로 전환 (운영 비용 $0).
  이 용도(요약·랭킹)에서 벤치마크상 Haiku 4.5와 동급 이상. config 두 줄로 anthropic 복귀 가능.
  Gemini는 google-genai SDK 대신 REST 직접 호출 — 로컬 파이썬(3.14t 32bit)에서 SDK 의존성
  (cryptography) 빌드가 불가능했고, Notion과 같은 방식이라 일관적. 무료 티어 입력은 구글
  학습에 사용될 수 있음(공개 뉴스라 무해). preview 모델이 내려가면 `gemini-2.5-flash`로 교체.

- Python 3.14.2 사용 (명세는 3.12 — 로컬에 3.14만 설치돼 있어 상위 호환 사용; Actions 러너는 3.12)
- gh CLI 미설치 — 레포 생성은 수동 안내 방식
- Reddit 무인증 `.rss` 레이트 리밋이 예상보다 빡빡함 → 서브레딧 간 6초 간격 + 429 Retry-After 존중으로 대응. 하루 1회 실행에서는 대부분 성공; 러너에서 지속 실패 시 OAuth 폴백 예정
- Bluesky 핸들 14개는 2026-07-06 실제 API 호출로 활성(최근 30일 내 원본 포스트) 검증 후 등록 — M5 게이트 선통과
- smol.ai 다이제스트는 h1~h3 헤딩 파서로 토픽 분해 (분해 실패 시 8,000자 단일 아이템 폴백)
- 소스별 폴링 주기 기능·Anthropic 폴백 스크레이퍼는 명세에 따라 구현하지 않음
- 날짜는 전부 Asia/Seoul 기준 (`zoneinfo` + `tzdata` 패키지로 Windows 호환)
- 같은 날 재실행 대비 `data/briefing_snapshot.json`에 당일 렌더 결과를 저장하고 병합 — cron 후 수동 재실행해도 그날 브리핑이 빈 페이지로 덮어써지지 않음
- 코드 리뷰(3렌즈 적대적 검증)에서 발견된 15건 수정 완료: 구조화 출력 스키마 제약(additionalProperties, min/max), feedparser UTC 변환(calendar.timegm), naive datetime 방어, seen.json last_seen purge, Notion 스키마 타입 검증 등
