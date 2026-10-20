from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import httpx

from envdoctor.config import EnvDoctorConfig, llm_ready
from envdoctor.explain.llm import build_digest, explain_report, redact
from support import make_report


def _configured() -> EnvDoctorConfig:
    config = EnvDoctorConfig()
    config.llm.base_url = "https://llm.example/v1"
    config.llm.model = "qwen-test"
    return config


def test_redact_replaces_home_user_host(monkeypatch) -> None:
    monkeypatch.setenv("USERNAME", "secretuser")
    home = str(Path.home())
    text = f"路径 {home}\\proj 用户 secretuser 主机 {socket.gethostname()} 结束"
    out = redact(text, user="secretuser", host=socket.gethostname())
    assert "<home>" in out
    assert home not in out
    assert "secretuser" not in out
    assert "<user>" in out
    assert "<host>" in out


def test_build_digest_contains_evidence_and_is_redacted(monkeypatch) -> None:
    monkeypatch.setenv("USERNAME", "secretuser")
    report = make_report()
    report.target.path = str(Path.home()) + "\\projects\\demo"
    digest = build_digest(report)
    assert report.overall.value in digest
    assert "判定" in digest
    assert "机器" in digest
    assert str(Path.home()) not in digest


def test_llm_ready_reports_missing_pieces(monkeypatch) -> None:
    monkeypatch.delenv("ENVDOCTOR_LLM_API_KEY", raising=False)
    ready, reason = llm_ready(EnvDoctorConfig())
    assert ready is False
    assert "base_url" in reason or "端点" in reason


def test_explain_report_with_mock_transport(monkeypatch) -> None:
    monkeypatch.setenv("ENVDOCTOR_LLM_API_KEY", "test-key")
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "结论：可以运行，注意驱动版本。"}}]})

    result = explain_report(make_report(), _configured(), transport=httpx.MockTransport(handler))
    assert result.text == "结论：可以运行，注意驱动版本。"
    assert result.error is None
    assert str(seen["url"]).endswith("/v1/chat/completions")
    assert seen["auth"] == "Bearer test-key"
    payload = seen["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "qwen-test"
    messages = payload["messages"]
    assert "envdoctor" in messages[0]["content"]


def test_explain_report_not_configured(monkeypatch) -> None:
    monkeypatch.delenv("ENVDOCTOR_LLM_API_KEY", raising=False)
    result = explain_report(make_report(), EnvDoctorConfig())
    assert result.error is not None
    assert "未配置" in result.error


def test_explain_report_http_error_degrades(monkeypatch) -> None:
    monkeypatch.setenv("ENVDOCTOR_LLM_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    result = explain_report(make_report(), _configured(), transport=httpx.MockTransport(handler))
    assert result.text is None
    assert result.error is not None
    assert "LLM 请求失败" in result.error


def test_explain_report_bad_shape_degrades(monkeypatch) -> None:
    monkeypatch.setenv("ENVDOCTOR_LLM_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    result = explain_report(make_report(), _configured(), transport=httpx.MockTransport(handler))
    assert result.error is not None
    assert "结构" in result.error


def test_no_key_in_payload_or_artifacts(monkeypatch) -> None:
    monkeypatch.setenv("ENVDOCTOR_LLM_API_KEY", "super-secret-key")
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    result = explain_report(make_report(), _configured(), transport=httpx.MockTransport(handler))
    assert result.text == "ok"
    assert "super-secret-key" not in captured["body"]


def test_env_var_username_helper_fallback(monkeypatch) -> None:
    monkeypatch.setenv("USERNAME", "u1")
    out = redact("hello u1", user=None, home=os.path.join("C:\\", "nonexistent-home-xyz"), host="nohost-xyz")
    assert out == "hello <user>"
