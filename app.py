# app.py
import os
import json
import shutil
import subprocess
import requests
import pytz
from pathlib import Path
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

load_dotenv()

app = Flask(__name__, static_folder='static')
CORS(app)

PORT         = int(os.getenv('FLASK_PORT', 5000))
PROMPTS_DIR  = Path('prompts')
PROJECTS_DIR = Path('projects')
QUEUE_DIR    = Path('queue')
PROMPTS_DIR.mkdir(exist_ok=True)
PROJECTS_DIR.mkdir(exist_ok=True)
QUEUE_DIR.mkdir(exist_ok=True)

QUEUE_FILE = QUEUE_DIR / 'pending.json'
KST        = pytz.timezone('Asia/Seoul')


# ════════════════════════════════════════════
# 큐 모듈 — queue/pending.json 읽기/쓰기
# ════════════════════════════════════════════

def load_queue() -> dict:
    """큐 파일 로드. 없으면 빈 구조 반환."""
    if not QUEUE_FILE.exists():
        return {'pending': [], 'processed': []}
    try:
        return json.loads(QUEUE_FILE.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, IOError):
        return {'pending': [], 'processed': []}


def save_queue(data: dict):
    """큐 파일 저장."""
    QUEUE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )


def add_to_queue(issue_data: dict) -> bool:
    """
    이슈를 큐에 추가.
    이미 pending 에 같은 issue_key 가 있으면 중복 무시 → False 반환.
    """
    queue = load_queue()
    existing_keys = [item['issue_key'] for item in queue['pending']]
    if issue_data['issue_key'] in existing_keys:
        return False
    queue['pending'].append(issue_data)
    save_queue(queue)
    return True


# ════════════════════════════════════════════
# Google Sheets 연동
# ════════════════════════════════════════════

def get_sheets_client():
    """
    Google Sheets 클라이언트 생성.
    - Render.com 환경: GOOGLE_SHEETS_CREDENTIALS_JSON 환경변수 (JSON 문자열)
    - 로컬 환경: GOOGLE_SHEETS_CREDENTIALS 파일 경로
    gspread 미설치 또는 인증 정보 없으면 None 반환.
    """
    try:
        import gspread
    except ImportError:
        print('[Sheets] gspread 미설치. pip install gspread 필요.')
        return None

    creds_json = os.getenv('GOOGLE_SHEETS_CREDENTIALS_JSON')
    if creds_json:
        # Render.com 환경: 환경변수에서 JSON 직접 로드
        try:
            creds_dict = json.loads(creds_json)
            return gspread.service_account_from_dict(creds_dict)
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


def parse_tc_to_rows(item: dict, tc_result: dict) -> list:
    """
    AI 생성 TC dict 를 Sheets 행 목록으로 변환.
    projects/{project_key}.json 의 tc_types.columns 참조.

    Returns: [[col1, col2, ...]] 형태의 2차원 배열
    """
    project_key = item.get('project_key', '')
    proj_path   = PROJECTS_DIR / f'{project_key}.json'

    # 프로젝트 설정 로드 (없으면 기본 컬럼 사용)
    if proj_path.exists():
        try:
            proj = json.loads(proj_path.read_text(encoding='utf-8'))
        except Exception:
            proj = {}
    else:
        proj = {}

    # 첫 번째 TC 유형의 컬럼 기준으로 변환
    tc_types = proj.get('tc_types', [])
    columns  = tc_types[0]['columns'] if tc_types else [
        {'key': '관리번호'}, {'key': '요약'}, {'key': '분류'},
        {'key': 'Precondition'}, {'key': '수행절차'},
        {'key': '기대결과'}, {'key': '테스트결과'}, {'key': '비고'}
    ]

    tc = tc_result.get('tc', {})

    # AI 응답 키 → 컬럼 키 매핑
    key_map = {
        '관리번호':     tc.get('관리번호', ''),
        '요약':         tc.get('요약', ''),
        '분류':         tc.get('분류', ''),
        'Precondition': tc.get('Precondition', ''),
        '수행절차':     tc.get('수행절차', ''),
        '기대결과':     tc.get('기대결과', ''),
        '테스트결과':   tc.get('테스트결과', ''),
        '비고':         tc.get('비고', ''),
        # E2E / 기본기능 전용
        '상태':         '',
        '시나리오목적': tc.get('요약', ''),
        '테스트계정':   tc.get('테스트계정', ''),
        '자동화':       '',
        'TC ID':        tc.get('관리번호', ''),
        '기능/요구명세ID': '',
        'Category1':    tc.get('분류', ''),
        'Category2':    '',
        'Category3':    '',
        'Title':        tc.get('요약', ''),
        'Test Step':    tc.get('수행절차', ''),
        'Expected Result': tc.get('기대결과', ''),
    }

    row = [key_map.get(col['key'], '') for col in columns]
    return [row]


def save_to_sheets(item: dict, tc_result: dict):
    """
    생성된 TC 를 Google Sheets 에 저장.
    시트가 없으면 자동 생성 후 헤더 추가.
    """
    gc = get_sheets_client()
    if not gc:
        print('[Sheets] 클라이언트 없음 — 저장 건너뜀')
        return

    sheet_id = os.getenv('GOOGLE_SHEETS_ID', '')
    if not sheet_id:
        print('[Sheets] GOOGLE_SHEETS_ID 환경변수 없음 — 저장 건너뜀')
        return

    try:
        import gspread
        sh           = gc.open_by_key(sheet_id)
        project_key  = item.get('project_key', 'default')

        # 워크시트 찾기 (없으면 생성)
        try:
            ws = sh.worksheet(project_key)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=project_key, rows=1000, cols=20)
            # 헤더 행 추가 — 프로젝트 설정의 컬럼 키 사용
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

        # TC 행 변환 후 추가
        rows     = parse_tc_to_rows(item, tc_result)
        next_row = len(ws.get_all_values()) + 1
        ws.update(rows, f'A{next_row}')
        print(f'[Sheets] {item["issue_key"]} → {len(rows)}건 저장 완료')

    except Exception as e:
        print(f'[Sheets] 저장 실패: {e}')
        raise


# ════════════════════════════════════════════
# AI TC 생성 — 스케줄러용 (서버 사이드 Key 사용)
# ════════════════════════════════════════════

def generate_tc_for_issue(item: dict) -> dict:
    """
    큐 항목 하나에 대해 TC 를 생성하고 파싱된 dict 반환.
    Claude Code CLI (claude --print) 를 subprocess 로 호출.
    API Key 불필요 — Claude.ai 구독으로 동작.
    """
    project_key = item.get('project_key', '')
    proj_path   = PROJECTS_DIR / f'{project_key}.json'

    if not proj_path.exists():
        raise Exception(f'프로젝트 설정 없음: {project_key}')

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

    # claude 실행 파일 경로 — Windows: claude.cmd, Linux/Mac: claude
    claude_bin = shutil.which('claude') or 'claude'

    # Windows 에서 .cmd 파일은 다중행 인수를 잘라냄 → 임시 파일 + PowerShell 파이프로 우회
    import tempfile
    tmp = tempfile.NamedTemporaryFile(
        mode='w', encoding='utf-8', suffix='.txt', delete=False
    )
    try:
        tmp.write(prompt)
        tmp.close()

        if os.name == 'nt':
            # PowerShell: 파일을 읽어 claude --print 에 파이프
            ps_cmd = f'Get-Content -Path "{tmp.name}" -Raw | & "{claude_bin}" --print'
            result = subprocess.run(
                ['powershell', '-NoProfile', '-NonInteractive', '-Command', ps_cmd],
                capture_output=True,
                text=True,
                encoding='utf-8',
                cwd=str(Path(__file__).parent),
                timeout=180
            )
        else:
            # Linux/Mac: stdin 직접 파이프
            with open(tmp.name, 'r', encoding='utf-8') as stdin_f:
                result = subprocess.run(
                    [claude_bin, '--print'],
                    stdin=stdin_f,
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    cwd=str(Path(__file__).parent),
                    timeout=180
                )
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    if result.returncode != 0:
        raise Exception(f'claude CLI 오류: {result.stderr[:500]}')

    return _parse_tc_result(result.stdout)


def _parse_tc_result(raw: str) -> dict:
    """AI 응답 텍스트를 TC dict 로 파싱."""
    text = raw.replace('```json', '').replace('```', '').strip()
    s, e = text.find('{'), text.rfind('}')
    if s >= 0 and e >= 0:
        text = text[s:e + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 파싱 실패 시 기본 구조 반환
        return {
            'tc': {
                '관리번호': '', '요약': '[파싱 오류]',
                '분류': '', 'Precondition': '',
                '수행절차': '', '기대결과': '',
                '테스트결과': '', '비고': f'원본:\n{raw[:500]}'
            },
            'script': None, 'applied_ts': [], 'summary': '파싱 오류'
        }


# ════════════════════════════════════════════
# 스케줄러 — 매일 09:00 KST 큐 일괄 처리
# ════════════════════════════════════════════

def process_queue():
    """
    매일 09:00 KST 에 실행.
    pending 항목을 순서대로 처리:
      1. AI TC 생성
      2. Google Sheets 저장
      3. pending → processed 이동
    """
    queue   = load_queue()
    pending = queue.get('pending', [])

    if not pending:
        print('[Scheduler] 처리할 항목 없음')
        return

    print(f'[Scheduler] {len(pending)}건 처리 시작 — {datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")}')

    for item in list(pending):  # 복사본으로 순회 (순회 중 수정 방지)
        issue_key = item.get('issue_key', '?')
        try:
            print(f'  → {issue_key} TC 생성 중...')
            tc_result = generate_tc_for_issue(item)

            print(f'  → {issue_key} Sheets 저장 중...')
            save_to_sheets(item, tc_result)

            # processed 로 이동
            item['processed_at'] = datetime.now(KST).isoformat()
            item['status']       = 'completed'
            item['result']       = 'success'
            queue['pending'].remove(item)
            queue['processed'].append(item)
            print(f'  ✓ {issue_key} 완료')

        except Exception as e:
            print(f'  ✗ {issue_key} 실패: {e}')
            item['status'] = 'failed'
            item['result'] = str(e)

    save_queue(queue)
    print(f'[Scheduler] 처리 완료 — {datetime.now(KST).strftime("%H:%M:%S")}')


# 스케줄러 인스턴스 생성 및 시작
scheduler = BackgroundScheduler(timezone=KST)
scheduler.add_job(
    process_queue,
    CronTrigger(hour=9, minute=0, timezone=KST),
    id='daily_queue_process',
    replace_existing=True
)
scheduler.start()
print('[Scheduler] 시작됨 - 매일 09:00 KST 실행 예정')


# ════════════════════════════════════════════
# 기존 엔드포인트 (수정 없음)
# ════════════════════════════════════════════

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


@app.route('/api/skill/<project_id>', methods=['GET'])
def get_skill(project_id):
    path = PROMPTS_DIR / f'{project_id}.md'
    if not path.exists():
        return jsonify({'content': ''}), 200
    return jsonify({'content': path.read_text(encoding='utf-8')}), 200


@app.route('/api/skill/<project_id>', methods=['POST'])
def save_skill(project_id):
    content = request.json.get('content', '')
    path    = PROMPTS_DIR / f'{project_id}.md'
    path.write_text(content, encoding='utf-8')
    return jsonify({'ok': True}), 200


@app.route('/api/project', methods=['GET'])
def list_projects():
    files    = PROJECTS_DIR.glob('*.json')
    projects = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding='utf-8'))
            projects.append({'id': f.stem, 'name': data.get('name', f.stem)})
        except Exception:
            pass
    return jsonify(projects), 200


@app.route('/api/project/<project_id>', methods=['GET'])
def get_project(project_id):
    path = PROJECTS_DIR / f'{project_id}.json'
    if not path.exists():
        return jsonify({'error': '프로젝트를 찾을 수 없습니다.'}), 404
    data = json.loads(path.read_text(encoding='utf-8'))
    # 보안: API Key 는 프론트엔드로 반환하지 않음
    data.pop('api_key', None)
    return jsonify(data), 200


@app.route('/api/project/<project_id>', methods=['POST'])
def save_project(project_id):
    data      = request.json
    path      = PROJECTS_DIR / f'{project_id}.json'
    # 기존 파일의 api_key 유지 (프론트엔드에서 전송하지 않으므로 덮어쓰지 않음)
    if path.exists():
        existing = json.loads(path.read_text(encoding='utf-8'))
        if 'api_key' in existing and 'api_key' not in data:
            data['api_key']    = existing['api_key']
            data['ai_provider'] = existing.get('ai_provider', data.get('ai_provider', 'gemini'))
            data['ai_model']   = existing.get('ai_model', data.get('ai_model', ''))
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return jsonify({'ok': True}), 200


@app.route('/api/project/<project_id>', methods=['DELETE'])
def delete_project(project_id):
    path = PROJECTS_DIR / f'{project_id}.json'
    if path.exists():
        path.unlink()
    skill_path = PROMPTS_DIR / f'{project_id}.md'
    if skill_path.exists():
        skill_path.unlink()
    return jsonify({'ok': True}), 200


@app.route('/api/jira/<path:jira_path>', methods=['GET'])
def jira_proxy(jira_path):
    domain = request.args.get('domain', '')
    email  = request.args.get('email', '')
    token  = request.args.get('token', '')

    if not all([domain, email, token]):
        return jsonify({'error': 'domain, email, token 파라미터가 필요합니다.'}), 400

    url    = f'https://{domain}/{jira_path}'
    params = {k: v for k, v in request.args.items() if k not in ('domain', 'email', 'token')}

    try:
        res = requests.get(
            url, params=params,
            auth=(email, token),
            headers={'Accept': 'application/json'},
            timeout=15,
            verify=False
        )
        return jsonify(res.json()), res.status_code
    except requests.exceptions.RequestException as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/generate', methods=['POST'])
def generate():
    body     = request.json
    provider = body.get('provider', 'gemini')
    api_key  = body.get('api_key', '')
    messages = body.get('messages', [])
    system   = body.get('system', '')
    model    = body.get('model', '')

    if not api_key:
        return jsonify({'error': 'API Key가 없습니다.'}), 400

    try:
        if provider == 'claude':
            result = _call_claude(api_key, model, system, messages)
        else:
            result = _call_gemini(api_key, model, system, messages)
        return jsonify({'text': result}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 502


def _call_gemini(api_key, model, system, messages):
    model = model or 'gemini-2.5-flash'
    url   = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}'
    body  = {
        'system_instruction': {'parts': [{'text': system}]},
        'contents': messages,
        'generationConfig': {'temperature': 0.2, 'maxOutputTokens': 16384}
    }
    res  = requests.post(url, json=body, timeout=60)
    data = res.json()
    if not res.ok:
        raise Exception(data.get('error', {}).get('message', f'Gemini 오류 ({res.status_code})'))
    return data['candidates'][0]['content']['parts'][0]['text'].strip()


def _call_claude(api_key, model, system, messages):
    model = model or 'claude-sonnet-4-20250514'
    claude_messages = [
        {'role': m['role'].replace('model', 'assistant'),
         'content': m['parts'][0]['text']}
        for m in messages
    ]
    res = requests.post(
        'https://api.anthropic.com/v1/messages',
        headers={
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
            'content-type': 'application/json'
        },
        json={
            'model': model,
            'max_tokens': 16000,
            'system': system,
            'messages': claude_messages,
            'temperature': 0.2
        },
        timeout=60
    )
    data = res.json()
    if not res.ok:
        raise Exception(data.get('error', {}).get('message', f'Claude 오류 ({res.status_code})'))
    return data['content'][0]['text'].strip()


# ════════════════════════════════════════════
# Phase 2 신규 엔드포인트
# ════════════════════════════════════════════

@app.route('/api/webhook', methods=['POST'])
def webhook():
    """
    JIRA Automation 에서 보내는 Webhook 수신.
    상태가 '테스트 진행'으로 변경되거나 담당자가 변경된 경우 큐에 저장.
    """
    try:
        body = request.get_json(force=True)
    except Exception:
        return jsonify({'error': '잘못된 JSON'}), 400

    if not body:
        return jsonify({'error': 'body 없음'}), 400

    # changelog 에서 트리거 조건 확인
    # - 상태가 '테스트 진행'으로 변경된 경우
    # - 또는 담당자 변경된 경우 (하위 호환)
    changelog = body.get('changelog', {})
    items     = changelog.get('items', [])

    # JIRA Automation 에서 이미 트리거 조건("테스트 진행" 상태 변경)을 적용하므로
    # Flask 에서는 별도 조건 검증 없이 issue key 유무만 확인

    # 이슈 정보 추출
    issue  = body.get('issue', {})
    fields = issue.get('fields', {})

    issue_key   = issue.get('key', '')
    summary     = fields.get('summary', '')
    description = fields.get('description', '') or ''
    project_key = fields.get('project', {}).get('key', '').lower()

    if not issue_key:
        return jsonify({'error': 'issue key 없음'}), 400

    # description 이 ADF(dict) 형식인 경우 텍스트만 추출
    if isinstance(description, dict):
        description = _extract_text_from_adf(description)

    issue_data = {
        'issue_key':   issue_key,
        'summary':     summary,
        'description': description[:2000],  # 토큰 절약: 2000자 제한
        'project_key': project_key or 'default',
        'queued_at':   datetime.now(KST).isoformat(),
        'status':      'pending'
    }

    added = add_to_queue(issue_data)
    if not added:
        return jsonify({'status': 'duplicate', 'issue_key': issue_key}), 200

    print(f'[Webhook] {issue_key} 큐 추가됨 — {summary[:50]}')
    return jsonify({'status': 'queued', 'issue_key': issue_key}), 200


def _extract_text_from_adf(node, depth=0) -> str:
    """JIRA ADF(Atlassian Document Format) → 평문 텍스트 변환."""
    if depth > 10:
        return ''
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ''
    node_type = node.get('type', '')
    text      = node.get('text', '')
    children  = node.get('content', [])
    result    = text + ''.join(_extract_text_from_adf(c, depth + 1) for c in children)
    if node_type in ('paragraph', 'heading', 'listItem'):
        result += '\n'
    return result


@app.route('/api/webhook/queue', methods=['GET'])
def get_queue():
    """현재 큐 상태 조회 (디버깅용)."""
    queue = load_queue()
    return jsonify({
        'pending_count':   len(queue.get('pending', [])),
        'processed_count': len(queue.get('processed', [])),
        'pending':         queue.get('pending', []),
        'processed':       queue.get('processed', [])[-10:]  # 최근 10건만
    }), 200


@app.route('/api/process-queue', methods=['POST'])
def manual_process():
    """
    수동 큐 처리 트리거.
    스케줄러가 슬립으로 미실행 시 대안.
    """
    try:
        process_queue()
        queue = load_queue()
        return jsonify({
            'status':          'ok',
            'pending_count':   len(queue.get('pending', [])),
            'processed_count': len(queue.get('processed', []))
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/webhook/queue/complete', methods=['POST'])
def complete_queue_item():
    """
    로컬 폴러가 TC 생성 완료 후 큐 항목을 processed 로 이동.
    Body: {"issue_key": "LIMS-xxxx", "result": "success" | "failed", "error": "..."}
    """
    body      = request.get_json(force=True) or {}
    issue_key = body.get('issue_key', '')
    result    = body.get('result', 'success')
    error_msg = body.get('error', '')

    if not issue_key:
        return jsonify({'error': 'issue_key 없음'}), 400

    queue = load_queue()
    item  = next((i for i in queue['pending'] if i['issue_key'] == issue_key), None)

    if not item:
        return jsonify({'error': '큐에 해당 이슈 없음'}), 404

    item['processed_at'] = datetime.now(KST).isoformat()
    item['status']       = 'completed'
    item['result']       = result
    if error_msg:
        item['error'] = error_msg

    queue['pending'].remove(item)
    queue['processed'].append(item)
    save_queue(queue)

    print(f'[Queue] {issue_key} 처리 완료 표시 — {result}')
    return jsonify({'ok': True}), 200


@app.route('/api/project/<project_id>/apikey', methods=['POST'])
def save_project_apikey(project_id):
    """
    프로젝트별 AI API Key 저장 (별도 엔드포인트로 분리).
    GET /api/project/{id} 에서는 반환하지 않으므로 보안 유지.
    """
    path = PROJECTS_DIR / f'{project_id}.json'
    if not path.exists():
        return jsonify({'error': '프로젝트를 찾을 수 없습니다.'}), 404

    body     = request.json
    api_key  = body.get('api_key', '').strip()
    provider = body.get('ai_provider', 'gemini')
    model    = body.get('ai_model', '')

    if not api_key:
        return jsonify({'error': 'api_key 없음'}), 400

    data = json.loads(path.read_text(encoding='utf-8'))
    data['api_key']    = api_key
    data['ai_provider'] = provider
    data['ai_model']   = model
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    return jsonify({'ok': True}), 200


# ════════════════════════════════════════════
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
