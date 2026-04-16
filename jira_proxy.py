"""
JIRA Local Proxy Server
-----------------------
브라우저의 CORS 제한을 우회하기 위한 로컬 프록시.
HTML 파일에서 직접 Atlassian API 호출 시 CORS 오류가 발생하므로
이 서버를 통해 요청을 중계합니다.

실행: python jira_proxy.py
URL: http://localhost:8787
"""

import json
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

PORT = 8787
CONFIG_FILE = Path(__file__).parent / "jira_config.json"


def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_config(data: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class ProxyHandler(BaseHTTPRequestHandler):

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Authorization, Accept, Content-Type",
        )

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        # /health → 프록시 상태 확인
        if self.path == "/health":
            self._json(200, {"status": "ok", "port": PORT})
            return

        # /config → 저장된 JIRA 설정 반환 (token 제외)
        if self.path == "/config":
            cfg = load_config()
            self._json(200, {"domain": cfg.get("domain", ""), "email": cfg.get("email", ""), "hasToken": bool(cfg.get("token"))})
            return

        # /jira/<path> → Atlassian REST API 중계
        if self.path.startswith("/jira/"):
            self._proxy_jira(self.path[len("/jira"):])
            return

        self._json(404, {"error": "Not found"})

    def do_POST(self):
        # /config 저장
        if self.path == "/config":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
                cfg = load_config()
                if "domain" in data:
                    cfg["domain"] = data["domain"].strip()
                if "email" in data:
                    cfg["email"] = data["email"].strip()
                if "token" in data:
                    cfg["token"] = data["token"].strip()
                save_config(cfg)
                self._json(200, {"status": "saved"})
            except Exception as e:
                self._json(400, {"error": str(e)})
            return

        self._json(404, {"error": "Not found"})

    def _proxy_jira(self, jira_path: str):
        cfg = load_config()
        domain = cfg.get("domain", "").strip()
        email = cfg.get("email", "").strip()
        token = cfg.get("token", "").strip()

        if not domain or not email or not token:
            self._json(401, {"error": "JIRA 설정이 없습니다. /config POST로 저장하거나 HTML에서 JIRA 설정을 먼저 해주세요."})
            return

        import base64
        creds = base64.b64encode(f"{email}:{token}".encode()).decode()
        url = f"https://{domain}{jira_path}"

        print(f"[PROXY] → {url}")
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Basic {creds}",
                "Accept": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self._cors_headers()
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            body = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(body)
        except urllib.error.URLError as e:
            self._json(502, {"error": f"JIRA 연결 실패: {e.reason}"})
        except Exception as e:
            self._json(500, {"error": str(e)})

    def _json(self, status: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print(f"[PROXY] {format % args}")


if __name__ == "__main__":
    print(f"=" * 50)
    print(f"  JIRA Local Proxy  —  http://localhost:{PORT}")
    print(f"=" * 50)
    cfg = load_config()
    if cfg.get("domain"):
        print(f"  도메인 : {cfg['domain']}")
        print(f"  이메일 : {cfg['email']}")
        print(f"  토큰   : {'설정됨' if cfg.get('token') else '미설정'}")
    else:
        print("  ⚠ JIRA 설정 없음 — HTML에서 JIRA 설정 후 저장하세요.")
    print(f"=" * 50)
    server = HTTPServer(("localhost", PORT), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n프록시 종료")
