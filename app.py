# app.py
import os
import json
import requests
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder='static')
CORS(app)

PORT         = int(os.getenv('FLASK_PORT', 5000))
PROMPTS_DIR  = Path('prompts')
PROJECTS_DIR = Path('projects')

# ── 정적 파일 (index.html) ──
@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


# ────────────────────────────────────────────
# /api/skill  — SKILL.md 읽기 / 쓰기
# ────────────────────────────────────────────
@app.route('/api/skill/<project_id>', methods=['GET'])
def get_skill(project_id):
    path = PROMPTS_DIR / f'{project_id}.md'
    if not path.exists():
        return jsonify({'content': ''}), 200
    return jsonify({'content': path.read_text(encoding='utf-8')}), 200


@app.route('/api/skill/<project_id>', methods=['POST'])
def save_skill(project_id):
    content = request.json.get('content', '')
    path = PROMPTS_DIR / f'{project_id}.md'
    path.write_text(content, encoding='utf-8')
    return jsonify({'ok': True}), 200


# ────────────────────────────────────────────
# /api/project  — 프로젝트 설정 읽기 / 쓰기 / 목록
# ────────────────────────────────────────────
@app.route('/api/project', methods=['GET'])
def list_projects():
    files = PROJECTS_DIR.glob('*.json')
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
    return jsonify(json.loads(path.read_text(encoding='utf-8'))), 200


@app.route('/api/project/<project_id>', methods=['POST'])
def save_project(project_id):
    data = request.json
    path = PROJECTS_DIR / f'{project_id}.json'
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


# ────────────────────────────────────────────
# /api/jira  — JIRA API 중계 (CORS 우회)
# ────────────────────────────────────────────
@app.route('/api/jira/<path:jira_path>', methods=['GET'])
def jira_proxy(jira_path):
    domain = request.args.get('domain', '')
    email  = request.args.get('email', '')
    token  = request.args.get('token', '')

    if not all([domain, email, token]):
        return jsonify({'error': 'domain, email, token 파라미터가 필요합니다.'}), 400

    url = f'https://{domain}/{jira_path}'
    params = {k: v for k, v in request.args.items() if k not in ('domain', 'email', 'token')}

    try:
        res = requests.get(
            url,
            params=params,
            auth=(email, token),
            headers={'Accept': 'application/json'},
            timeout=15,
            verify=False  # 사내 SSL 인증서 문제 대응
        )
        return jsonify(res.json()), res.status_code
    except requests.exceptions.RequestException as e:
        return jsonify({'error': str(e)}), 502


# ────────────────────────────────────────────
# /api/generate  — AI API 중계 (Claude / Gemini)
# ────────────────────────────────────────────
@app.route('/api/generate', methods=['POST'])
def generate():
    body     = request.json
    provider = body.get('provider', 'gemini')   # 'gemini' or 'claude'
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
    model = model or 'gemini-2.0-flash'
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
    # Gemini 형식 → Claude 형식 변환
    claude_messages = [
        {'role': m['role'].replace('model', 'assistant'),
         'content': m['parts'][0]['text']}
        for m in messages
    ]
    res  = requests.post(
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


# ────────────────────────────────────────────
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=True)