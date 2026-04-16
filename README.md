# AI QA Code Generator

JIRA 이슈를 입력하면 AI가 테스트 케이스(TC)와 Playwright 자동화 스크립트를 생성하는 웹 서비스입니다.

## 도입 배경

1인 QA 체제에서 정기 배포마다 반복되는 TC 작성 공수를 줄이기 위해 개발했습니다.

| 구분 | 기존 | 개선 후 |
|------|------|---------|
| TC 작성 공수 | 5일 | 2시간 |
| 전체 사이클 (검수 포함) | 5일 | 4일 |
| 단축률 | — | 작성 공수 기준 90% 단축 |

> AI가 생성한 TC를 베이스로 QA 엔지니어가 실 테스트 수행 중 엣지케이스 추가 및 수정하는 방식으로 운영합니다.

## 주요 기능

- **JIRA 연동** — 이슈 번호 입력 시 제목/본문/코멘트 자동 파싱
- **AI TC 생성** — Claude / Gemini 듀얼 프로바이더 지원
- **Playwright 스크립트 생성** — TC 기반 자동화 스크립트 동시 생성
- **TSV 복사** — Google Sheets 바로 붙여넣기 가능한 포맷 출력
- **프로젝트별 설정** — SKILL.md 기반 도메인 규칙 분리, 다른 프로젝트에도 적용 가능
- **TC 유형 커스터마이징** — 정기배포 / 기본기능 / E2E 등 컬럼 구조 GUI로 편집
- **수정 채팅** — 생성 후 추가 요청으로 TC/스크립트 즉시 수정

## 기술 스택

| 구분 | 기술 |
|------|------|
| Frontend | HTML / CSS / Vanilla JS |
| Backend | Python / Flask |
| AI | Claude API (Anthropic) / Gemini API (Google) |
| 이슈 트래커 | JIRA REST API v3 |
| 배포 | Render.com |

## 시스템 아키텍처
브라우저 (HTML/JS)
↓ fetch
Flask 서버 (Python)
├── /api/generate   → Claude / Gemini API 호출
├── /api/jira       → JIRA API 중계 (CORS 해결)
├── /api/skill      → SKILL.md 읽기/쓰기
└── /api/project    → 프로젝트 설정 관리

## 실행 방법

**1. 패키지 설치**
```bash
pip install -r requirements.txt
```

**2. 환경변수 설정**
```bash
# .env 파일 생성
FLASK_PORT=5000
FLASK_ENV=development
```

**3. 프로젝트 설정**
```bash
# projects/ 폴더에 프로젝트 설정 JSON 추가
# prompts/ 폴더에 SKILL.md 추가
# example 파일 참고
```

**4. 서버 실행**
```bash
python app.py
```

**5. 브라우저 접속**
http://localhost:5000

## 사용 방법

1. **AI 설정** — 우측 상단에서 Claude 또는 Gemini API Key 입력
2. **JIRA 설정** — JIRA 도메인 / 이메일 / API 토큰 입력
3. **프로젝트 선택** — 사이드바에서 프로젝트 선택
4. **TC 유형 선택** — 정기배포 / 기본기능 / E2E 중 선택
5. **JIRA 이슈 입력** — 이슈 번호 또는 URL 붙여넣기 후 불러오기
6. **생성하기** — TC + Playwright 스크립트 자동 생성
7. **TSV 복사** — Google Sheets에 바로 붙여넣기

## 프로젝트 설정 커스터마이징

`프로젝트 설정` 탭에서 GUI로 편집 가능합니다.

- 프로젝트별 JIRA 도메인 설정
- 분류 카테고리 추가/삭제
- TC 유형별 컬럼 추가/삭제/순서 변경
- SKILL.md 업로드로 도메인 규칙 반영

## License

MIT