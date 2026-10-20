"""HTTP layer for the web console (DEVELOPMENT.md §26).

Only the standard library is used: the console must not add runtime
dependencies to a tool whose selling point is being installable anywhere.

Security model (this process can install third-party code, so it matters):

* binds to ``127.0.0.1`` unless the operator explicitly opts in;
* every request needs the per-session token (header or query parameter);
* the ``Host`` header must be loopback — this blocks DNS-rebinding attacks
  where a hostile page resolves its own name to 127.0.0.1 and then talks to
  the console from the browser;
* state-changing requests must be same-origin (``Origin`` check).
"""

from __future__ import annotations

import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from envdoctor.config import EnvDoctorConfig
from envdoctor.paths import WorkLayout
from envdoctor.web.app import Response, WebApp

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
TOKEN_HEADER = "X-Envdoctor-Token"
MAX_BODY_BYTES = 64 * 1024


def _hostname(value: str) -> str:
    """Extract the bare hostname from a ``Host`` header or an ``Origin`` URL.

    ``Host: 127.0.0.1:8766`` → ``127.0.0.1``；
    ``Origin: http://127.0.0.1:8766`` → ``127.0.0.1``（scheme 必须先剥掉，
    否则 ``split(":")`` 会把 ``http`` 当成主机名）。
    """
    text = value.strip().lower()
    if "://" in text:
        text = text.split("://", 1)[1]
    if text.startswith("["):
        return text[1:].split("]", 1)[0]
    return text.split(":", 1)[0]


class WebConsoleServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], app: WebApp, *, allow_remote: bool = False) -> None:
        super().__init__(address, WebConsoleHandler)
        self.app = app
        self.allow_remote = allow_remote


class WebConsoleHandler(BaseHTTPRequestHandler):
    server_version = "envdoctor-web"
    protocol_version = "HTTP/1.1"

    # -- plumbing --------------------------------------------------------------

    def log_message(self, format: str, *args) -> None:
        return  # keep the console quiet; the browser is the UI

    @property
    def console(self) -> WebConsoleServer:
        return self.server  # type: ignore[return-value]

    def _write(self, response: Response) -> None:
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (response.headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(response.body)

    def _reject(self, status: int, code: str, message: str) -> None:
        import json

        body = json.dumps({"error": code, "message": message}, ensure_ascii=False).encode("utf-8")
        self._write(Response(status=status, content_type="application/json; charset=utf-8", body=body))

    # -- security --------------------------------------------------------------

    def _host_ok(self) -> bool:
        if self.console.allow_remote:
            return True
        host = self.headers.get("Host", "")
        return _hostname(host) in LOOPBACK_HOSTS

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True  # not a browser-initiated cross-origin request
        host = self.headers.get("Host", "")
        return _hostname(origin) == _hostname(host) or _hostname(origin) in LOOPBACK_HOSTS

    def _token(self) -> str | None:
        header = self.headers.get(TOKEN_HEADER)
        if header:
            return header
        query = parse_qs(urlsplit(self.path).query)
        values = query.get("token") or []
        return values[0] if values else None

    # -- verbs -----------------------------------------------------------------

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        if not self._host_ok():
            self._reject(403, "HOST_NOT_ALLOWED", "只接受回环地址的 Host 头")
            return
        if method in {"POST", "DELETE"} and not self._origin_ok():
            self._reject(403, "ORIGIN_NOT_ALLOWED", "跨来源请求被拒绝")
            return
        path = urlsplit(self.path).path
        token = self._token()
        if method == "GET":
            self._write(self.console.app.get(path, token=token))
            return
        if method == "DELETE":
            self._write(self.console.app.delete(path, token=token))
            return
        body = self._read_body()
        self._write(self.console.app.post(path, body=body, token=token))

    def _read_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = raw.decode("utf-8")
        except UnicodeDecodeError:
            return {}
        import json

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}


def build_app(
    *,
    workdir: str | None = None,
    token: str | None = None,
    config: EnvDoctorConfig | None = None,
) -> tuple[WebApp, str]:
    """Create the console app and its access token."""
    resolved = Path(workdir).resolve() if workdir else Path.cwd() / ".envdoctor"
    work = WorkLayout(resolved)
    session_token = token or secrets.token_urlsafe(24)
    return WebApp(work=work, token=session_token, config=config), session_token


def run_console(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    workdir: str | None = None,
    config: EnvDoctorConfig | None = None,
    open_browser: bool = True,
    allow_remote: bool = False,
) -> int:
    """Run the console until interrupted. Returns a process exit code."""
    app, token = build_app(workdir=workdir, config=config)
    server = WebConsoleServer((host, port), app, allow_remote=allow_remote)
    actual_port = server.server_address[1]
    url = f"http://{host}:{actual_port}/?token={token}"
    print(f"envdoctor web console → {url}", flush=True)
    print(f"工作目录：{app.workdir}", flush=True)
    print("仅监听本机；按 Ctrl+C 停止。", flush=True)
    if allow_remote:
        print("⚠ 已允许非回环访问：任何能访问该端口的人都可以触发诊断（仍需令牌）。", flush=True)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
    return 0
