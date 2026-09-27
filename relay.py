# -*- coding: utf-8 -*-
"""PX Studio 로컬 릴레이 — 무제한 패스(v1 lane=infinite)와 로컬 이미지 업로드용.

PixAI v1 API는 pixai.art 외 출처의 브라우저 호출을 CORS로 막는다. v2(유료 생성)는 브라우저에서 바로 되므로
이 릴레이는 선택 사항이다. 127.0.0.1에서만 받아 api.pixai.art 의 /v1/task·/v1/media/upload 로 그대로 넘긴다.
API 키는 저장하지 않는다(브라우저가 보낸 Authorization 헤더를 그대로 전달).

  python relay.py            # 기본 127.0.0.1:8765
  python relay.py --port 9000
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = "https://api.pixai.art"
ALLOW = ("/v1/task", "/v1/media/upload")


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "authorization,content-type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        # https로 올린 페이지에서 localhost를 부를 때 Chrome의 사설망 접근 사전 요청을 통과시킨다.
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, b'{"ok":true,"relay":"px-studio"}')
        self._proxy("GET")

    def do_POST(self):
        self._proxy("POST")

    def _proxy(self, method):
        # 경계까지 맞춘다: /v1/taskXYZ 같은 이웃 경로는 넘기지 않는다.
        if not any(self.path == p or self.path.startswith(p + "/") or self.path.startswith(p + "?") for p in ALLOW):
            return self._send(403, b'{"message":"relay: path not allowed"}')
        auth = self.headers.get("Authorization")
        if not auth:
            return self._send(401, b'{"message":"relay: missing Authorization"}')
        n = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(n) if n else None
        req = urllib.request.Request(UPSTREAM + self.path, data=data, method=method, headers={
            "Authorization": auth,
            "Content-Type": self.headers.get("Content-Type", "application/json"),
            "User-Agent": "PXStudio-Relay/1",
        })
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                body, code, ctype = r.read(), r.status, r.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            body, code, ctype = e.read(), e.code, e.headers.get("Content-Type", "application/json")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            body, code, ctype = json.dumps({"message": f"relay upstream error: {e}"}).encode(), 502, "application/json"
        self._send(code, body, ctype)

    def log_message(self, fmt, *args):
        code = str(args[1]) if len(args) > 1 else ""
        # 상태 폴링(GET /v1/task/{id} 200)은 수십 줄이 쌓이므로 생략하고 생성·업로드·오류만 남긴다.
        if self.command == "GET" and self.path.startswith("/v1/task/") and code == "200":
            return
        sys.stdout.write("%s %s %s\n" % (self.command, self.path.split("?")[0], code))
        sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser(description="PX Studio 로컬 릴레이")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    # 콘솔 코드페이지와 무관하게 읽히도록 안내 문구는 ASCII로 둔다.
    print(f"PX Studio relay running: http://127.0.0.1:{a.port}  (Ctrl+C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
