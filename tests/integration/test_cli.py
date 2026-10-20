"""CLI end-to-end through the dev shim: exit codes, artifacts, honesty rules."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEV = ROOT / "scripts" / "envdoctor_dev.py"
EXTERNAL_REF_RE = re.compile(r'(?:src|href)\s*=\s*"https?://')


def run_cli(*args: str, cwd: Path) -> tuple[int, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.run(
        [sys.executable, str(DEV), *args],
        cwd=str(cwd),
        capture_output=True,
        env=env,
        stdin=subprocess.DEVNULL,
    )
    output = proc.stdout.decode("utf-8", errors="replace") + proc.stderr.decode("utf-8", errors="replace")
    return proc.returncode, output


def test_cli_version(tmp_path: Path) -> None:
    code, output = run_cli("version", cwd=tmp_path)
    assert code == 0
    assert "envdoctor" in output


def test_cli_scan_writes_json(projects_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "scan.json"
    code, _ = run_cli(
        "scan",
        str(projects_dir / "torch-cu121"),
        "--json",
        str(out),
        "--workdir",
        str(tmp_path / ".envdoctor"),
        cwd=tmp_path,
    )
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["cuda"]["requested_runtime"] == "12.1"
    assert payload["target"]["kind"] == "local"


def test_cli_probe_writes_machine_profile(tmp_path: Path) -> None:
    out = tmp_path / "probe.json"
    code, output = run_cli("probe", "--json", str(out), "--workdir", str(tmp_path / ".envdoctor"), cwd=tmp_path)
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["python"]["version"]
    assert isinstance(payload["gpu"]["present"], bool)
    if not payload["gpu"]["present"]:
        assert payload["gpu"]["reason"]  # §22-1: 未检测到也要有原因
    assert "GPU" in output


def test_cli_analyze_verdicts_and_exit_zero(projects_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "verdicts.json"
    code, _ = run_cli(
        "analyze",
        str(projects_dir / "torch-cu121"),
        "--json",
        str(out),
        "--workdir",
        str(tmp_path / ".envdoctor"),
        cwd=tmp_path,
    )
    assert code == 0
    verdicts = json.loads(out.read_text(encoding="utf-8"))
    assert {verdict["id"] for verdict in verdicts} >= {"python_available", "gpu_presence"}
    for verdict in verdicts:
        assert verdict["state"] in {"ok", "warn", "blocked", "unknown", "not_applicable"}
        assert verdict["summary"]


def test_cli_doctor_blocked_maps_to_cannot_run(projects_dir: Path, tmp_path: Path) -> None:
    """Machine-independent contract: blocked ⇔ cannot_run, else unknown (§22-3/§22-5)."""
    report_path = tmp_path / "report.json"
    code, _ = run_cli(
        "doctor",
        str(projects_dir / "torch-cu121"),
        "--no-verify",
        "--json",
        str(report_path),
        "--workdir",
        str(tmp_path / ".envdoctor"),
        "-q",
        cwd=tmp_path,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    blocked = any(verdict["state"] == "blocked" for verdict in report["verdicts"])
    if blocked:
        assert code == 1
        assert report["overall"] == "cannot_run"
    else:
        assert code == 2
        assert report["overall"] == "unknown"


def test_cli_doctor_no_verify_never_claims_runs(projects_dir: Path, tmp_path: Path) -> None:
    code, _ = run_cli(
        "doctor",
        str(projects_dir / "clean-uv"),
        "--no-verify",
        "--workdir",
        str(tmp_path / ".envdoctor"),
        "-q",
        cwd=tmp_path,
    )
    assert code == 2  # §22-5: 静态-only 不算 runs


def test_cli_doctor_non_interactive_gate_skip(projects_dir: Path, tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    code, _ = run_cli(
        "doctor",
        str(projects_dir / "tiny-ml"),
        "--json",
        str(report_path),
        "--workdir",
        str(tmp_path / ".envdoctor"),
        "-q",
        cwd=tmp_path,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    blocked = any(verdict["state"] == "blocked" for verdict in report["verdicts"])
    if blocked:
        assert code == 1
    else:
        assert code == 2  # §22-7: 无 TTY 且无 --yes → 跳过验证并显式说明
        assert report["verify_performed"] is False
        assert "非交互终端" in report["overall_reason"] or "跳过" in report["overall_reason"]


def test_cli_doctor_artifacts_and_self_contained_html(projects_dir: Path, tmp_path: Path) -> None:
    workdir = tmp_path / ".envdoctor"
    code, _ = run_cli("doctor", str(projects_dir / "clean-uv"), "--no-verify", "--workdir", str(workdir), cwd=tmp_path)
    assert code == 2
    runs = list((workdir / "runs").iterdir())
    assert len(runs) == 1
    run_dir = runs[0]
    for name in ("scan.json", "probe.json", "verdicts.json", "report.json", "report.html"):
        assert (run_dir / name).is_file(), f"缺少产物 {name}"
    html = (run_dir / "report.html").read_text(encoding="utf-8")
    assert EXTERNAL_REF_RE.search(html) is None  # §22-11


def test_cli_scan_missing_target_is_structured_error(tmp_path: Path) -> None:
    code, output = run_cli("scan", str(tmp_path / "nope"), "--workdir", str(tmp_path / ".envdoctor"), cwd=tmp_path)
    assert code == 3
    assert "TARGET_NOT_FOUND" in output


def test_cli_redirected_output_is_valid_utf8(tmp_path: Path) -> None:
    """回归守卫（历史事故 F-0003）：重定向/管道输出必须是合法 UTF-8。

    故意不设置 PYTHONIOENCODING：旧实现下 dev shim 绕过 main() 的 reconfigure，
    cp936 环境的管道输出会是不合法 UTF-8，严格解码在此抛 UnicodeDecodeError。
    """
    env = {key: value for key, value in os.environ.items() if key not in {"PYTHONIOENCODING", "PYTHONUTF8"}}
    proc = subprocess.run(
        [sys.executable, str(DEV), "probe", "--workdir", str(tmp_path / ".envdoctor")],
        cwd=str(tmp_path),
        capture_output=True,
        env=env,
        stdin=subprocess.DEVNULL,
    )
    assert proc.returncode == 0
    proc.stdout.decode("utf-8")  # 严格解码；旧实现在此抛 UnicodeDecodeError


def test_cli_invalid_config_exits_with_internal_error_code(tmp_path: Path) -> None:
    """§4：内部错误 → 退出码 3（而不是裸 traceback 的 1）。"""
    code, output = run_cli(
        "doctor",
        str(tmp_path / "nope"),
        "--config",
        str(tmp_path / "missing.toml"),
        "--workdir",
        str(tmp_path / ".envdoctor"),
        cwd=tmp_path,
    )
    assert code == 3
    assert "CONFIG_INVALID" in output


def test_cli_config_file_is_accepted(tmp_path: Path, projects_dir: Path) -> None:
    config_path = tmp_path / "envdoctor.toml"
    config_path.write_text('[defaults]\nindex_url = "https://mirror.example/simple"\n', encoding="utf-8")
    code, _output = run_cli(
        "doctor",
        str(projects_dir / "clean-uv"),
        "--no-verify",
        "--config",
        str(config_path),
        "--workdir",
        str(tmp_path / ".envdoctor"),
        "-q",
        cwd=tmp_path,
    )
    assert code == 2  # 静态分析永远是 unknown（诚实性红线）


def test_cli_unexpected_internal_error_exits_three(tmp_path: Path, projects_dir: Path, monkeypatch) -> None:
    """任何未预期异常都必须映射到退出码 3，而不是裸 traceback。"""
    from typer.testing import CliRunner

    from envdoctor.cli import main as cli_main

    def _boom(*_args, **_kwargs):
        raise OSError("disk on fire")

    monkeypatch.setattr(cli_main, "write_json_report", _boom)
    runner = CliRunner()
    result = runner.invoke(
        cli_main.app,
        [
            "doctor",
            str(projects_dir / "clean-uv"),
            "--no-verify",
            "--workdir",
            str(tmp_path / ".envdoctor"),
        ],
    )
    assert result.exit_code == 3


def test_cli_verify_performed_is_false_when_uv_is_missing(tmp_path: Path, projects_dir: Path, monkeypatch) -> None:
    """诚实性：uv 缺失导致验证未真正执行时，verify_performed 必须是 false。"""
    from typer.testing import CliRunner

    from envdoctor.cli import main as cli_main

    monkeypatch.setattr(cli_main.shutil, "which", lambda *_args, **_kwargs: None)
    runner = CliRunner()
    workdir = tmp_path / ".envdoctor"
    result = runner.invoke(
        cli_main.app,
        [
            "doctor",
            str(projects_dir / "tiny-ml"),
            "--yes",
            "--workdir",
            str(workdir),
        ],
    )
    assert result.exit_code == 2
    runs = sorted((workdir / "runs").iterdir())
    report = json.loads((runs[-1] / "report.json").read_text(encoding="utf-8"))
    assert report["verify_performed"] is False
    assert report["overall"] == "unknown"
