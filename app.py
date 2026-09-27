# -*- coding: utf-8 -*-
"""PX Studio 데스크톱 — index.html을 Edge WebView2 창으로 띄우는 exe 본체.

브라우저판과 같은 화면을 쓰고, 네트워크와 파일 저장만 이 프로세스가 맡는다.
  - 127.0.0.1 임의 포트에 로컬 서버를 연다: 화면(index.html) 제공 · PixAI API 중계(/api/v1·/api/v2) · 파일 쓰기(/native/write)
  - 중계는 파이썬에서 나가므로 CORS 제한이 없다 → 무제한 패스(v1 lane=infinite)와 이미지 업로드가 별도 릴레이 없이 된다
  - 로컬 서버는 실행마다 새 토큰을 요구한다(다른 프로그램·웹페이지가 이 포트를 두드려도 키를 못 쓰게)
  - 폴더·저장 대화상자는 pywebview js_api(pick_folder·save_file·open_folder)
  - 데이터(설정·갤러리·키·포트)는 exe 옆 PXStudio_data 폴더에 둔다. 쓸 수 없는 자리면 %APPDATA%\\PXStudio
  - API 키는 「저장」을 누르면 그 폴더의 key.bin 에 DPAPI로 암호화해 두고 실행 때 자동으로 불러온다(get_key·save_key)

빌드:  .venv\\Scripts\\pyinstaller PXStudio.spec   (결과: dist\\PXStudio.exe)
개발 실행: .venv\\Scripts\\python app.py
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import secrets
import socket
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import webview

APP_NAME = "PX Studio"
UPSTREAM = "https://api.pixai.art"
# /graphql: 잔액(me.quotaAmount)·LoRA 학습. PixAI가 브라우저 출처에 CORS를 막아 둬서 창 앱만 이 중계로 부른다.
# v2 조회(가격표·모델 기능·팔레트·스타일 키·프롬프트 추출)와 편집 태스크(인페인트·손얼굴 보정)도 같은 키로 중계한다.
ALLOW = ("/v1/task", "/v1/media/upload", "/v2/image/create", "/v2/task", "/graphql",
         "/v2/task-price", "/v2/generation-model", "/v2/color-palettes/presets", "/v2/tag/suggest-prompt",
         "/v2/gen-task/inpaint")
TOKEN = secrets.token_urlsafe(24)
BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
LEGACY_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "PXStudio")


def _pick_data_dir():
    """데이터는 exe 옆 PXStudio_data 폴더에 둔다(폴더째 옮기면 설정·갤러리가 따라간다 · 성현님 요청 2026-09-27).
    그 자리에 쓸 수 없으면(예: Program Files) 예전 위치 %APPDATA%\\PXStudio 를 쓴다. 반환: (경로, 휴대용 여부)"""
    exe_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    d = os.path.join(exe_dir, "PXStudio_data")
    try:
        os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, ".write_test")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return d, True
    except OSError:
        return LEGACY_DIR, False


DATA_DIR, PORTABLE = _pick_data_dir()


def _migrate_legacy():
    """휴대용 폴더가 처음이면 예전 %APPDATA%\\PXStudio 데이터를 한 번 복사해 온다(원본은 남긴다).
    port.txt까지 가져와야 같은 포트(=같은 저장소 출처)로 떠서 갤러리·설정이 보인다.
    프로필은 임시 폴더로 통째 복사한 뒤 성공했을 때만 제자리로 옮긴다. 실패하면 False —
    그 실행은 예전 폴더를 그대로 쓰고(휴대용 폴더에 빈 프로필을 만들지 않음) 다음 실행에서 다시 시도한다."""
    import shutil
    if not PORTABLE or os.path.exists(os.path.join(DATA_DIR, "webview")) or not os.path.isdir(LEGACY_DIR):
        return True
    src = os.path.join(LEGACY_DIR, "webview")
    part = os.path.join(DATA_DIR, "webview.migrating")
    # 키·포트도 임시 이름으로 받아 두고 프로필이 제자리에 들어간 뒤에만 확정한다.
    # (실패한 시도의 키가 남으면, 그 실행에서 AppData 쪽 키를 바꿔도 다음 이전 때 옛 키가 이긴다)
    # 프로필(webview) 확정이 맨 마지막 단계 = 「이전 완료」 표시. 그 전에 실패하면 이번 시도에서 만든 것을 전부 되돌려
    # 다음 실행이 처음부터 다시 시도하게 한다.
    temps, done = [], []
    try:
        if os.path.isdir(src):
            if os.path.exists(part):
                shutil.rmtree(part)
            shutil.copytree(src, part)   # 예전 창이 켜져 있어 잠긴 파일이 있으면 여기서 실패
        for name in ("key.bin", "port.txt"):
            s, d = os.path.join(LEGACY_DIR, name), os.path.join(DATA_DIR, name)
            if os.path.isfile(s) and not os.path.exists(d):
                shutil.copy2(s, d + ".migrating")
                temps.append((d + ".migrating", d))
        for t, d in temps:
            os.replace(t, d)
            done.append(d)
        if os.path.isdir(part):
            os.replace(part, os.path.join(DATA_DIR, "webview"))
        return True
    except (OSError, shutil.Error):
        shutil.rmtree(part, ignore_errors=True)
        for f in [t for t, _ in temps] + done:
            try:
                os.remove(f)
            except OSError:
                pass
        return False


def _use_data_dir(d, portable):
    global DATA_DIR, PORTABLE, KEY_FILE, PORT_FILE
    DATA_DIR, PORTABLE = d, portable
    KEY_FILE = os.path.join(d, "key.bin")
    PORT_FILE = os.path.join(d, "port.txt")


KEY_FILE = os.path.join(DATA_DIR, "key.bin")


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.c_void_p)]


def _dpapi(data, protect):
    """윈도우 DPAPI(현재 사용자 범위). 키 파일을 다른 계정·다른 PC에서 복호화할 수 없다."""
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in, blob_out = _Blob(len(data), ctypes.cast(buf, ctypes.c_void_p)), _Blob()
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(_Blob)]
    desc = ctypes.c_wchar_p("PXStudio") if protect else None
    if not fn(ctypes.byref(blob_in), desc, None, None, None, 0x1, ctypes.byref(blob_out)):   # 0x1 = UI 없음
        raise ctypes.WinError()
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def load_key():
    """저장된 키. 파일이 없으면 "" · 읽기·복호화 실패면 None(화면이 「못 읽음」으로 다뤄 빈칸 저장으로 파일을 지우지 않게)."""
    if not os.path.exists(KEY_FILE):
        return ""
    try:
        with open(KEY_FILE, "rb") as f:
            return _dpapi(f.read(), False).decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def save_key(key):
    os.makedirs(DATA_DIR, exist_ok=True)
    key = (key or "").strip()
    if not key:
        if os.path.exists(KEY_FILE):
            os.remove(KEY_FILE)
        return
    with open(KEY_FILE + ".part", "wb") as f:
        f.write(_dpapi(key.encode("utf-8"), True))
    os.replace(KEY_FILE + ".part", KEY_FILE)


def _allowed(path):
    return any(path == p or path.startswith(p + "/") or path.startswith(p + "?") for p in ALLOW)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else None

    def _token_ok(self):
        return secrets.compare_digest(self.headers.get("X-PXS-Token", ""), TOKEN)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            with open(os.path.join(BASE_DIR, "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if path.startswith("/api/"):
            return self._proxy("GET")
        self._json(404, {"message": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        if path.startswith("/api/"):
            return self._proxy("POST")
        if path == "/native/write":
            return self._write()
        self._json(404, {"message": "not found"})

    def _proxy(self, method):
        data = self._body() if method == "POST" else None   # 요청 본문은 항상 소비해 연결을 깨끗이 둔다
        if not self._token_ok():
            return self._json(403, {"message": "bad token"})
        up = self.path[len("/api"):]
        if not _allowed(up):
            return self._json(403, {"message": "path not allowed"})
        auth = self.headers.get("Authorization")
        if not auth:
            return self._json(401, {"message": "API 키가 없습니다"})
        req = urllib.request.Request(UPSTREAM + up, data=data, method=method, headers={
            "Authorization": auth,
            "Content-Type": self.headers.get("Content-Type", "application/json"),
            "User-Agent": "PXStudio-Desktop/1",
        })
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                body, code, ctype = r.read(), r.status, r.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            body, code, ctype = e.read(), e.code, e.headers.get("Content-Type", "application/json")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            body, code, ctype = json.dumps({"message": f"네트워크 오류: {e}"}, ensure_ascii=False).encode("utf-8"), 502, "application/json"
        self._send(code, body, ctype)

    def _write(self):
        data = self._body() or b""
        if not self._token_ok():
            return self._json(403, {"message": "bad token"})
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        path = (q.get("path") or [""])[0]
        if not path or not os.path.isabs(path):
            return self._json(400, {"message": "절대 경로가 필요합니다"})
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".part"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except OSError as e:
            return self._json(500, {"message": f"저장 실패: {e}"})
        self._json(200, {"path": path, "bytes": len(data)})

    def log_message(self, fmt, *args):
        pass   # 창 앱이라 콘솔이 없다


class Api:
    """JS에서 window.pywebview.api.*로 부르는 네이티브 대화상자."""

    def __init__(self):
        self._window = None   # 밑줄: pywebview가 js_api를 훑을 때 창 객체 안으로 재귀하지 않게

    def _dialog(self, kind, **kw):
        ft = getattr(webview, "FileDialog", None)
        const = getattr(ft, kind, None) if ft else getattr(webview, {"FOLDER": "FOLDER_DIALOG", "SAVE": "SAVE_DIALOG"}[kind])
        res = self._window.create_file_dialog(const, **kw)
        if not res:
            return None
        return res if isinstance(res, str) else res[0]

    def get_key(self):
        return load_key()

    def save_key(self, key):
        try:
            save_key(key)
            return True
        except OSError:
            return False

    def pick_folder(self):
        return self._dialog("FOLDER")

    def save_file(self, name):
        return self._dialog("SAVE", save_filename=name)

    def open_folder(self, path):
        if path and os.path.isdir(path):
            os.startfile(path)
            return True
        return False


PORT_FILE = os.path.join(DATA_DIR, "port.txt")
PREFERRED_PORT = 37851


class Server(ThreadingHTTPServer):
    # 윈도우의 SO_REUSEADDR는 이미 쓰는 포트를 다른 소켓이 가로채게 허용한다 → 끄고 독점 바인드
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def bind_server():
    """창 안의 저장소(localStorage·IndexedDB)는 출처(포트 포함)별로 갈린다.
    포트가 실행마다 바뀌면 갤러리·프로젝트·설정이 매번 빈 채로 뜨므로, 처음 잡은 포트를 저장해 계속 쓴다.
    반환: (서버, 저장소를 이어 쓰는지 여부)"""
    try:
        with open(PORT_FILE, encoding="utf-8") as f:
            saved = int(f.read().strip())
    except (OSError, ValueError):
        saved = 0
    for port in (saved or PREFERRED_PORT, 0):
        try:
            srv = Server(("127.0.0.1", port), Handler)
        except OSError:
            continue
        if not saved:
            with open(PORT_FILE, "w", encoding="utf-8") as f:
                f.write(str(srv.server_address[1]))
            return srv, True
        return srv, srv.server_address[1] == saved
    raise OSError("로컬 서버 포트를 열 수 없습니다")


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    # 포트 파일을 읽기 전에(bind_server) 가져와야 예전 저장소 출처가 이어진다. 이전 실패면 이번엔 예전 폴더로 뜬다
    if not _migrate_legacy():
        _use_data_dir(LEGACY_DIR, False)
    srv, shared = bind_server()
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    dbg = os.environ.get("PXS_DEBUG_FILE")   # 개발 검증용: 이 환경변수를 줄 때만 포트·토큰을 파일로 남긴다
    if dbg:
        with open(dbg, "w", encoding="utf-8") as f:
            json.dump({"port": port, "token": TOKEN}, f)

    api = Api()
    # 저장한 포트를 못 잡았으면(보통 이미 창이 하나 떠 있을 때) 화면에 알린다
    win = webview.create_window(APP_NAME, f"http://127.0.0.1:{port}/?app={TOKEN}" + ("" if shared else "&alt=1"), js_api=api,
                                width=1440, height=920, min_size=(960, 640))
    api._window = win
    # private_mode=False + storage_path: 설정·프로젝트(localStorage)와 갤러리(IndexedDB)가 실행 사이에 남는다
    webview.start(private_mode=False, storage_path=os.path.join(DATA_DIR, "webview"))
    srv.shutdown()


if __name__ == "__main__":
    main()
