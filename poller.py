# poller.py
# Render.com 큐를 주기적으로 폴링하여 로컬에서 TC 생성 후 Google Sheets에 저장
# 실행: python poller.py

import os
import json
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

import requests
import pytz
from dotenv import load_dotenv

load_dotenv()

# ════════════════════════════════════════════
# 설정
# ════════════════════════════════════════════

RENDER_URL    = os.getenv('RENDER_URL', 'https://ai-qa-generator.onrender.com')
POLL_INTERVAL = int(os.getenv('POLL_INTERVAL', 300))  # 폴링 간격 (초), 기본 5분
PROMPTS_DIR   = Path('prompts')
PROJECTS_DIR  = Path('projects')
KST           = pytz.timezone('Asia/Seoul')


# ════════════════════════════════════════════
# Claude CLI 호출
# ════════════════════════════════════════════

def _call_claude_cli(prompt: str) -> str:
    """
    Claude Code CLI (claude --print) 를 subprocess 로 호출.
    Windows: PowerShell 파이프 방식 (cmd.exe 다중행 인수 잘림 방지)
    """
    claude_bin = shutil.which('claude') or 'claude'

    # 프롬프트를 임시 파일에 저장
    tmp = tempfile.NamedTemporaryFile(
        mode='w', encoding='utf-8', suffix='.txt', delete=False
    )
    try:
        tmp.write(prompt)
        tmp.close()

        if os.name == 'nt':
            # Windows: PowerShell로 파일 내용을 claude --print 에 파이프
            # --dangerously-skip-permissions: MCP 도구 권한 프롬프트 없이 실행
            # .claude/settings.json 에서 mcpServers 비활성화로 MCP 도구 차단
            ps_cmd = f'Get-Content -Path "{tmp.name}" -Raw | & "{claude_bin}" --print --dangerously-skip-permissions'
            result = subprocess.run(
                ['powershell', '-NoProfile', '-NonInteractive', '-Command', ps_cmd],
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=180
            )
        else:
            # Linux/Mac: stdin 직접 파이프
            with open(tmp.name, 'r', encoding='utf-8') as stdin_f:
                result = subprocess.run(
                    [claude_bin, '--print', '--dangerously-skip-permissions'],
                    stdin=stdin_f,
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    timeout=180
                )
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    if result.returncode != 0:
        raise Exception(f'claude CLI 오류: {result.stderr[:500]}')

    return result.stdout


# ════════════════════════════════════════════
# TC 생성
# ════════════════════════════════════════════

def _parse_tc_result(raw: str) -> dict:
    """AI 응답 텍스트를 TC dict 로 파싱."""
    text = raw.replace('```json', '').replace('```', '').strip()
    s, e = text.find('{'), text.rfind('}')
    if s >= 0 and e >= 0:
        text = text[s:e + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {
            'tc': {
                '관리번호': '', '요약': '[파싱 오류]',
                '분류': '', 'Precondition': '',
                '수행절차': '', '기대결과': '',
                '테스트결과': '', '비고': f'원본:\n{raw[:500]}'
            },
            'script': None, 'applied_ts': [], 'summary': '파싱 오류'
        }


def generate_tc(item: dict) -> dict:
    """큐 항목에 대해 TC 생성 후 파싱된 dict 반환."""
    project_key = item.get('project_key', '')

    # SKILL.md 로드 — 프롬프트에 포함하여 규칙 전달
    skill_path    = PROMPTS_DIR / f'{project_key}.md'
    skill_content = skill_path.read_text(encoding='utf-8') if skill_path.exists() else ''

    # 프롬프트 구성 — JSON 출력 명시 요청
    prompt = (
        f"{skill_content}\n\n"
        f"---\n\n"
        f"JIRA 이슈 번호: {item['issue_key']}\n"
        f"기능 설명: {item['summary']}\n"
        f"{('상세 내용: ' + item['description'] + chr(10)) if item.get('description') else ''}"
        f"\n정보가 부족하더라도 주어진 요약과 이슈 번호만으로 TC를 작성해줘. 추가 정보를 요청하거나 확인을 묻지 말고 즉시 아래 JSON 형식으로만 출력해줘. 마크다운 코드블록 없이 JSON만.\n\n"
        f'{{"tc": {{"관리번호": "", "요약": "", "분류": "", "Precondition": "", '
        f'"수행절차": "", "기대결과": "", "테스트결과": "", "비고": ""}}, '
        f'"applied_ts": [], "summary": ""}}'
    )

    raw = _call_claude_cli(prompt)
    return _parse_tc_result(raw)


# ════════════════════════════════════════════
# Google Sheets 저장
# ════════════════════════════════════════════

def _get_sheets_client():
    """Google Sheets 클라이언트 생성."""
    try:
        import gspread
    except ImportError:
        print('[Sheets] gspread 미설치')
        return None

    creds_json = os.getenv('GOOGLE_SHEETS_CREDENTIALS_JSON')
    if creds_json:
        try:
            return gspread.service_account_from_dict(json.loads(creds_json))
        except Exception as e:
            print(f'[Sheets] 환경변수 인증 실패: {e}')
            return None

    creds_file = os.getenv('GOOGLE_SHEETS_CREDENTIALS', 'credentials/service_account.json')
    if not Path(creds_file).exists():
        print(f'[Sheets] 인증 파일 없음: {creds_file}')
        return None

    try:
        return gspread.service_account(filename=creds_file)
    except Exception as e:
        print(f'[Sheets] 파일 인증 실패: {e}')
        return None


def _parse_tc_to_rows(item: dict, tc_result: dict) -> list:
    """TC dict 를 Sheets 행 목록으로 변환."""
    project_key = item.get('project_key', '')
    proj_path   = PROJECTS_DIR / f'{project_key}.json'

    if proj_path.exists():
        try:
            proj = json.loads(proj_path.read_text(encoding='utf-8'))
        except Exception:
            proj = {}
    else:
        proj = {}

    tc_types = proj.get('tc_types', [])
    columns  = tc_types[0]['columns'] if tc_types else [
        {'key': '관리번호'}, {'key': '요약'}, {'key': '분류'},
        {'key': 'Precondition'}, {'key': '수행절차'},
        {'key': '기대결과'}, {'key': '테스트결과'}, {'key': '비고'}
    ]

    tc      = tc_result.get('tc', {})
    key_map = {
        '관리번호':     tc.get('관리번호', ''),
        '요약':         tc.get('요약', ''),
        '분류':         tc.get('분류', ''),
        'Precondition': tc.get('Precondition', ''),
        '수행절차':     tc.get('수행절차', ''),
        '기대결과':     tc.get('기대결과', ''),
        '테스트결과':   tc.get('테스트결과', ''),
        '비고':         tc.get('비고', ''),
    }

    return [[key_map.get(col['key'], '') for col in columns]]


def save_to_sheets(item: dict, tc_result: dict):
    """생성된 TC 를 Google Sheets 에 저장."""
    gc = _get_sheets_client()
    if not gc:
        print('[Sheets] 클라이언트 없음 — 저장 건너뜀')
        return

    sheet_id = os.getenv('GOOGLE_SHEETS_ID', '')
    if not sheet_id:
        print('[Sheets] GOOGLE_SHEETS_ID 없음 — 저장 건너뜀')
        return

    try:
        import gspread
        sh          = gc.open_by_key(sheet_id)
        project_key = item.get('project_key', 'default')

        try:
            ws = sh.worksheet(project_key)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=project_key, rows=1000, cols=20)
            proj_path = PROJECTS_DIR / f'{project_key}.json'
            if proj_path.exists():
                proj     = json.loads(proj_path.read_text(encoding='utf-8'))
                tc_types = proj.get('tc_types', [])
                columns  = tc_types[0]['columns'] if tc_types else []
                headers  = [col['key'] for col in columns]
            else:
                headers = ['관리번호', '요약', '분류', 'Precondition',
                           '수행절차', '기대결과', '테스트결과', '비고']
            ws.update([headers], 'A1')

        rows     = _parse_tc_to_rows(item, tc_result)
        next_row = len(ws.get_all_values()) + 1
        ws.update(rows, f'A{next_row}')
        print(f'[Sheets] {item["issue_key"]} → {len(rows)}건 저장 완료')

    except Exception as e:
        print(f'[Sheets] 저장 실패: {e}')
        raise


# ════════════════════════════════════════════
# Render.com 큐 조회 / 완료 신호
# ════════════════════════════════════════════

def fetch_pending() -> list:
    """Render.com 에서 pending 항목 조회."""
    try:
        r = requests.get(f'{RENDER_URL}/api/webhook/queue', timeout=30)
        r.raise_for_status()
        return r.json().get('pending', [])
    except Exception as e:
        print(f'[Poller] 큐 조회 실패: {e}')
        return []


def mark_complete(issue_key: str, result: str = 'success', error: str = ''):
    """Render.com 에 처리 완료 신호 전송."""
    try:
        body = {'issue_key': issue_key, 'result': result}
        if error:
            body['error'] = error
        requests.post(
            f'{RENDER_URL}/api/webhook/queue/complete',
            json=body,
            timeout=30
        )
    except Exception as e:
        print(f'[Poller] 완료 신호 전송 실패: {e}')


# ════════════════════════════════════════════
# 메인 폴링 루프
# ════════════════════════════════════════════

def process_pending():
    """pending 항목 전체 처리."""
    pending = fetch_pending()

    if not pending:
        print(f'[Poller] 처리할 항목 없음 — {datetime.now(KST).strftime("%H:%M:%S")}')
        return

    print(f'[Poller] {len(pending)}건 처리 시작 — {datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")}')

    for item in pending:
        issue_key = item.get('issue_key', '?')
        try:
            print(f'  → {issue_key} TC 생성 중...')
            tc_result = generate_tc(item)

            print(f'  → {issue_key} Sheets 저장 중...')
            save_to_sheets(item, tc_result)

            mark_complete(issue_key, result='success')
            print(f'  완료: {issue_key}')

        except Exception as e:
            print(f'  실패: {issue_key} — {e}')
            mark_complete(issue_key, result='failed', error=str(e))

    print(f'[Poller] 처리 완료 — {datetime.now(KST).strftime("%H:%M:%S")}')


if __name__ == '__main__':
    print(f'[Poller] 시작 — {RENDER_URL} 폴링 간격: {POLL_INTERVAL}초')
    while True:
        process_pending()
        time.sleep(POLL_INTERVAL)
