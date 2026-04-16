# AI QA Code Generator

## 프로젝트 개요
- JIRA 티켓 → TC 생성 + Playwright 스크립트 자동 생성
- 이직/포트폴리오 목적 사이드 프로젝트
- Phase 1: Claude.ai 프롬프트 품질 검증 (완료)
- Phase 2: Claude API 기반 자동화 파이프라인 구축

## 기술 스택
- Python
- Claude API (주 프로바이더) / Gemini API (보조)
- JIRA REST API (polaqube.atlassian.net)
- Google Sheets (gspread, 서비스 계정 인증)

## 목표 워크플로우
JIRA 이슈 번호 입력
→ JIRA API로 티켓 내용 fetch
→ TC 생성 (qa-tc-writer 스킬 규칙 적용)
→ Google Sheets RTM에 TC 자동 기입
→ Playwright 스크립트 생성
→ 파일 저장

## 핵심 규칙
- Windows PowerShell 환경 (&&, || 연산자 사용 불가)
- 모든 문서는 마크다운(.md) 형식
- TC 형식: 8컬럼 구조 (관리번호/요약/분류/Precondition/수행절차/기대결과/테스트결과/비고)
- Playwright 스크립트: allure 태그 4종 필수