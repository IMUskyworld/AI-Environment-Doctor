"""Optional LLM interpretation layer (DEVELOPMENT.md §14) — off by default.

The LLM never overrides verdicts: it receives a redacted digest of the evidence
chain and returns extra prose. Anything sensitive (home path, user, host, env
vars, file contents) is stripped before the request leaves the machine.
"""

from __future__ import annotations

import os
import re
import socket
from pathlib import Path

import httpx

from envdoctor.config import EnvDoctorConfig, llm_api_key, llm_ready
from envdoctor.domain.models import ExplainResult, Report

LLM_TIMEOUT_S = 60.0

SYSTEM_PROMPT = (
    "你是 envdoctor 的环境诊断解释助手。用户会给你一份环境诊断证据链（判定、实测检查点、机器画像）。"
    "请用中文回答三点：1) 一句话总体结论；2) 最可能的原因（只引用给定证据）；3) 修复优先级（先做什么）。"
    "不要发明证据之外的事实；证据不足就直接说证据不足。控制在 300 字以内。"
)


def redact(text: str, *, home: str | None = None, user: str | None = None, host: str | None = None) -> str:
    """Replace home path, user name and host name; case-insensitive."""
    home_value = home or str(Path.home())
    user_value = user if user is not None else (os.environ.get("USERNAME") or os.environ.get("USER") or "")
    host_value = host if host is not None else socket.gethostname()
    patterns: list[tuple[str, str]] = []
    for variant in {home_value, home_value.replace("\\", "/")}:
        if variant:
            patterns.append((re.escape(variant), "<home>"))
    if user_value:
        patterns.append((re.escape(user_value), "<user>"))
    if host_value:
        patterns.append((re.escape(host_value), "<host>"))
    result = text
    for pattern, replacement in patterns:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return result


def build_digest(report: Report) -> str:
    lines: list[str] = []
    lines.append(f"总体结论：{report.overall.value}（{report.overall_reason}）")
    lines.append(f"目标：{report.target.kind.value} {report.target.original}")
    lines.append("")
    lines.append("判定：")
    for verdict in report.verdicts:
        lines.append(f"- [{verdict.state.value}] {verdict.title}：{verdict.summary}")
    if report.checkpoints:
        lines.append("")
        lines.append("实测检查点：")
        for checkpoint in report.checkpoints:
            note = checkpoint.skip_reason or checkpoint.detail or ""
            lines.append(f"- [{checkpoint.state.value}] {checkpoint.title}：{note}")
    probe = report.probe
    lines.append("")
    lines.append("机器：")
    lines.append(
        f"- OS {probe.os.name} {probe.os.release}；Python {probe.python.version}；uv {probe.tooling.uv or '无'}"
    )
    if probe.gpu.present:
        names = ", ".join(device.name for device in probe.gpu.devices) or "未知"
        lines.append(
            f"- GPU {names}；驱动 {probe.gpu.driver_version or '未知'}；CUDA ≤ {probe.gpu.driver_max_cuda or '未知'}"
        )
    else:
        lines.append(f"- GPU 未检测到（{probe.gpu.reason or '原因未知'}）")
    lines.append("")
    lines.append("项目：")
    lines.append(
        f"- Python 要求 {report.scan.python.requires or '未声明'}；"
        f"ML 栈 {', '.join(item.name for item in report.scan.ml_stack) or '无'}；"
        f"CUDA 诉求 {report.scan.cuda.requested_runtime or '未声明'}；"
        f"GPU 需求强度 {report.scan.cuda.gpu_required_strength.value}"
    )
    return redact("\n".join(lines))


def explain_report(
    report: Report,
    config: EnvDoctorConfig,
    *,
    transport: httpx.BaseTransport | None = None,
    api_key: str | None = None,
    timeout_s: float = LLM_TIMEOUT_S,
) -> ExplainResult:
    ready, reason = llm_ready(config)
    if not ready:
        return ExplainResult(model=config.llm.model, error=reason)
    key = api_key or llm_api_key()
    if not key:  # pragma: no cover - llm_ready already guards this
        return ExplainResult(model=config.llm.model, error="缺少 API key")
    assert config.llm.base_url is not None
    assert config.llm.model is not None
    url = f"{config.llm.base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": config.llm.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_digest(report)},
        ],
        "temperature": 0.2,
        "max_tokens": 700,
    }
    try:
        with httpx.Client(timeout=timeout_s, transport=transport) as client:
            response = client.post(url, json=payload, headers={"Authorization": f"Bearer {key}"})
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        return ExplainResult(model=config.llm.model, error=f"LLM 请求失败：{type(exc).__name__}: {exc}")
    except ValueError as exc:
        return ExplainResult(model=config.llm.model, error=f"LLM 响应不是合法 JSON：{exc}")
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ExplainResult(model=config.llm.model, error="LLM 响应结构不符合 OpenAI 兼容格式")
    if not isinstance(text, str) or not text.strip():
        return ExplainResult(model=config.llm.model, error="LLM 返回空内容")
    return ExplainResult(model=config.llm.model, text=text.strip())
