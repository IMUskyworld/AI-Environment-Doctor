"""Web console tests (DEVELOPMENT.md §26).

Two layers:

* unit — routing/authorization/consent logic exercised without a socket;
* integration — a real server on an ephemeral port, driven over HTTP.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import httpx
import pytest

from envdoctor.web.app import WebApp
from envdoctor.web.jobs import JobManager
from envdoctor.web.server import WebConsoleServer, _hostname, build_app

TOKEN = "test-token-123"


# --- helpers ------------------------------------------------------------------


def _app(tmp_path: Path) -> WebApp:
    app, _token = build_app(workdir=str(tmp_path / ".envdoctor"), token=TOKEN)
    return app


# --- hostname parsing ---------------------------------------------------------


def test_hostname_strips_scheme_and_port() -> None:
    assert _hostname("127.0.0.1:8766") == "127.0.0.1"
    assert _hostname("http://127.0.0.1:8766") == "127.0.0.1"
    assert _hostname("https://example.com") == "example.com"
    assert _hostname("localhost") == "localhost"
    assert _hostname("[::1]:8766") == "::1"


# --- authorization -------------------------------------------------------------


def test_authorized_requires_exact_token(tmp_path: Path) -> None:
    app = _app(tmp_path)
    assert app.authorized(TOKEN) is True
    assert app.authorized("wrong") is False
    assert app.authorized(None) is False
    assert app.authorized("") is False


def test_routes_reject_missing_token(tmp_path: Path) -> None:
    app = _app(tmp_path)
    for response in (
        app.get("/", token=None),
        app.get("/api/machine", token=None),
        app.get("/api/runs", token=None),
        app.post("/api/jobs", body={"kind": "analyze", "target": "."}, token=None),
        app.delete("/api/runs/x", token=None),
    ):
        assert response.status == 403
        assert b"UNAUTHORIZED" in response.body


def test_index_page_embeds_the_token(tmp_path: Path) -> None:
    app = _app(tmp_path)
    response = app.get("/", token=TOKEN)
    assert response.status == 200
    body = response.body.decode("utf-8")
    assert TOKEN in body
    assert "__ENVDOCTOR_TOKEN__" not in body
    assert "http://" not in body.replace("http://www.w3.org", "")  # 自包含：无外部资源


# --- job creation rules --------------------------------------------------------


def test_analyze_requires_a_target(tmp_path: Path) -> None:
    app = _app(tmp_path)
    response = app.post("/api/jobs", body={"kind": "analyze"}, token=TOKEN)
    assert response.status == 400
    assert b"TARGET_REQUIRED" in response.body


def test_verify_requires_consent(tmp_path: Path) -> None:
    """知情同意：没有显式同意就不许触发安装。"""
    app = _app(tmp_path)
    response = app.post("/api/jobs", body={"kind": "verify", "run_id": "whatever"}, token=TOKEN)
    assert response.status == 400
    assert b"CONSENT_REQUIRED" in response.body


def test_verify_requires_existing_run(tmp_path: Path) -> None:
    app = _app(tmp_path)
    response = app.post(
        "/api/jobs",
        body={"kind": "verify", "run_id": "does-not-exist", "consent": True},
        token=TOKEN,
    )
    assert response.status == 404
    assert b"RUN_NOT_FOUND" in response.body


def test_unknown_job_kind_is_rejected(tmp_path: Path) -> None:
    app = _app(tmp_path)
    response = app.post("/api/jobs", body={"kind": "nonsense"}, token=TOKEN)
    assert response.status == 400
    assert b"UNKNOWN_JOB_KIND" in response.body


# --- job manager ---------------------------------------------------------------


def test_job_manager_runs_and_reports(tmp_path: Path) -> None:
    manager = JobManager()
    job = manager.submit("demo", lambda item: {"ok": True, "seen": item.kind})
    deadline = time.time() + 5
    while time.time() < deadline:
        current = manager.get(job.id)
        assert current is not None
        if current.state in {"done", "failed"}:
            break
        time.sleep(0.05)
    assert current.state == "done"
    assert current.result == {"ok": True, "seen": "demo"}


def test_job_manager_captures_failures(tmp_path: Path) -> None:
    def _boom(_job):
        raise RuntimeError("kaboom")

    manager = JobManager()
    job = manager.submit("demo", _boom)
    deadline = time.time() + 5
    while time.time() < deadline:
        current = manager.get(job.id)
        if current is not None and current.state in {"done", "failed"}:
            break
        time.sleep(0.05)
    assert current.state == "failed"
    assert "kaboom" in (current.error or "")


# --- real HTTP server ----------------------------------------------------------


@pytest.fixture
def live_server(tmp_path: Path):
    app = _app(tmp_path)
    server = WebConsoleServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    yield f"http://{host}:{port}", app
    server.shutdown()
    server.server_close()


def test_http_requires_token(live_server) -> None:
    base, _app_obj = live_server
    with httpx.Client(timeout=10.0) as client:
        assert client.get(f"{base}/").status_code == 403
        assert client.get(f"{base}/api/runs", headers={"X-Envdoctor-Token": "x"}).status_code == 403
        assert client.get(f"{base}/", headers={"X-Envdoctor-Token": TOKEN}).status_code == 200
        assert client.get(f"{base}/api/machine", headers={"X-Envdoctor-Token": TOKEN}).status_code == 200


def test_http_rejects_non_loopback_host_header(live_server) -> None:
    """DNS rebinding 防护：Host 头不是回环地址就拒绝。"""
    base, _app_obj = live_server
    with httpx.Client(timeout=10.0) as client:
        response = client.get(
            f"{base}/api/machine",
            headers={"X-Envdoctor-Token": TOKEN, "Host": "evil.example.com"},
        )
    assert response.status_code == 403
    assert b"HOST_NOT_ALLOWED" in response.content


def test_http_rejects_cross_origin_post(live_server) -> None:
    base, _app_obj = live_server
    with httpx.Client(timeout=10.0) as client:
        response = client.post(
            f"{base}/api/jobs",
            json={"kind": "analyze", "target": "."},
            headers={"X-Envdoctor-Token": TOKEN, "Origin": "http://evil.example.com"},
        )
    assert response.status_code == 403
    assert b"ORIGIN_NOT_ALLOWED" in response.content


def test_http_accepts_same_origin_post(live_server, projects_dir: Path, tmp_path: Path) -> None:
    """同源 POST 必须被放行（回归：Origin 解析曾把 scheme 当成主机名）。"""
    base, _app_obj = live_server
    headers = {"X-Envdoctor-Token": TOKEN, "Origin": base}
    with httpx.Client(timeout=60.0) as client:
        response = client.post(
            f"{base}/api/jobs",
            json={"kind": "analyze", "target": str(projects_dir / "tiny-ml")},
            headers=headers,
        )
        assert response.status_code == 202
        job_id = response.json()["id"]

        deadline = time.time() + 60
        payload: dict = {}
        while time.time() < deadline:
            payload = client.get(f"{base}/api/jobs/{job_id}", headers={"X-Envdoctor-Token": TOKEN}).json()
            if payload["state"] in {"done", "failed"}:
                break
            time.sleep(0.1)
        assert payload["state"] == "done", payload.get("error")
        run_id = payload["result"]["run_id"]

        report = client.get(f"{base}/api/runs/{run_id}", headers={"X-Envdoctor-Token": TOKEN})
        assert report.status_code == 200
        body = report.json()
        assert body["overall"] in {"runs", "cannot_run", "unknown"}
        assert body["verdicts"]

        html = client.get(f"{base}/api/runs/{run_id}/report.html", headers={"X-Envdoctor-Token": TOKEN})
        assert html.status_code == 200
        assert b"envdoctor" in html.content

        listing = client.get(f"{base}/api/runs", headers={"X-Envdoctor-Token": TOKEN}).json()
        assert any(item["run_id"] == run_id for item in listing)

        deleted = client.delete(f"{base}/api/runs/{run_id}", headers={"X-Envdoctor-Token": TOKEN})
        assert deleted.status_code == 200
        assert json.loads(deleted.content)["deleted"] == run_id
        assert client.get(f"{base}/api/runs/{run_id}", headers={"X-Envdoctor-Token": TOKEN}).status_code == 404
