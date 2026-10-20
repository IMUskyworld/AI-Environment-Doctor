from __future__ import annotations

import io
import json
import re
from pathlib import Path

from rich.console import Console

from envdoctor.domain.enums import CheckpointState
from envdoctor.domain.models import Checkpoint
from envdoctor.report.console import render_report
from envdoctor.report.writer import write_html_report, write_json_report
from support import make_report

EXTERNAL_REF_RE = re.compile(r'(?:src|href)\s*=\s*"https?://')


def test_json_report_roundtrip(tmp_path: Path) -> None:
    report = make_report()
    path = tmp_path / "report.json"
    write_json_report(report, path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0"
    assert payload["tool"]["name"] == "envdoctor"
    assert payload["overall"] in {"runs", "cannot_run", "unknown"}
    assert payload["verdicts"], "报告必须包含判定"
    assert payload["probe"]["os"]["name"]


def test_json_report_keeps_chinese_readable(tmp_path: Path) -> None:
    report = make_report()
    path = tmp_path / "report.json"
    write_json_report(report, path)
    raw = path.read_text(encoding="utf-8")
    assert "判定" in raw or "项目" in raw  # ensure_ascii=False


def test_html_report_is_self_contained(tmp_path: Path) -> None:
    checkpoints = [Checkpoint(id="venv", title="创建临时环境（uv venv）", state=CheckpointState.PASS, detail="ok")]
    report = make_report(checkpoints=checkpoints, verify_performed=True)
    path = tmp_path / "report.html"
    write_html_report(report, path)
    html = path.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert EXTERNAL_REF_RE.search(html) is None, "HTML 必须自包含（无外部资源引用）"
    assert "创建临时环境" in html
    assert report.overall.value.upper() in html


def test_html_escapes_hostile_content(tmp_path: Path) -> None:
    report = make_report()
    report.overall_reason = "<script>alert(1)</script>"
    path = tmp_path / "report.html"
    write_html_report(report, path)
    html = path.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_console_render_smoke() -> None:
    checkpoints = [
        Checkpoint(id="venv", title="创建临时环境", state=CheckpointState.PASS, seconds=0.5, detail="ok"),
        Checkpoint(id="install", title="安装依赖", state=CheckpointState.SKIP, skip_reason="安装源不可达"),
    ]
    report = make_report(checkpoints=checkpoints, verify_performed=True)
    buffer = io.StringIO()
    console = Console(file=buffer, width=140)
    render_report(console, report, artifacts={"run 目录": "X:/runs/1"}, verify_note="非交互终端")
    text = buffer.getvalue()
    assert "判定明细" in text
    assert "真实验证检查点" in text
    assert "安装源不可达" in text
    assert "非交互终端" in text
    assert "X:/runs/1" in text
