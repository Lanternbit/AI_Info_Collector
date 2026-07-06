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

1. **Anthropic API 키** — [console.anthropic.com](https://console.anthropic.com) (Haiku 기준 월 $5~10 예상)
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

- `NOTION_DATABASE_ID`(DB URL의 32자리 hex)만 넣으면 `data_source_id`는 자동 조회된다.
  DB URL 예: `notion.so/사용자명/<DB_ID>?v=...` — `<DB_ID>` 부분.
- **로컬에서 실제 실행(비 dry-run) 전 반드시 `git pull`** — Actions가 커밋한 seen.json과 어긋나면 Notion에 중복 저장될 수 있다.

## GitHub 배포 (M4)

1. GitHub에 public 레포 생성 후 push
2. 레포 Settings → Pages → Source: `Deploy from a branch`, Branch: `main` / `/docs`
3. Settings → Secrets and variables → Actions에 등록:
   `ANTHROPIC_API_KEY`, `NOTION_TOKEN`, `NOTION_DATA_SOURCE_ID` (또는 파이프라인 로그에 출력되는 data_source_id)
4. Actions 탭 → daily-briefing → `Run workflow`로 수동 트리거해 e2e 확인
5. 실행 로그 footer에서 소스별 성공/실패 확인 — 러너(데이터센터 IP)에서만 실패하는 소스(특히 Reddit)는
   Reddit OAuth script app 폴백을 붙이거나 `enabled: false` 처리

## 운영

- 소스 추가/삭제: `config/sources.yaml` 수정 (Tier 2 소스는 `enabled: true`로 켜기)
- 브리핑 비공개가 필요하면: GitHub Pages(무료 플랜은 public) 대신 Cloudflare Pages + Cloudflare Access(무료 50인)로 이전하면 인증 걸린 비공개 URL을 만들 수 있다.
- Actions 실패 시 GitHub가 이메일 알림을 보낸다 (기본 설정 유지).

## 결정 기록

- Python 3.14.2 사용 (명세는 3.12 — 로컬에 3.14만 설치돼 있어 상위 호환 사용; Actions 러너는 3.12)
- gh CLI 미설치 — 레포 생성은 수동 안내 방식
- Reddit 무인증 `.rss` 레이트 리밋이 예상보다 빡빡함 → 서브레딧 간 6초 간격 + 429 Retry-After 존중으로 대응. 하루 1회 실행에서는 대부분 성공; 러너에서 지속 실패 시 OAuth 폴백 예정
- Bluesky 핸들 14개는 2026-07-06 실제 API 호출로 활성(최근 30일 내 원본 포스트) 검증 후 등록 — M5 게이트 선통과
- smol.ai 다이제스트는 h1~h3 헤딩 파서로 토픽 분해 (분해 실패 시 8,000자 단일 아이템 폴백)
- 소스별 폴링 주기 기능·Anthropic 폴백 스크레이퍼는 명세에 따라 구현하지 않음
- 날짜는 전부 Asia/Seoul 기준 (`zoneinfo` + `tzdata` 패키지로 Windows 호환)
