"""MVP acceptance audit for DEVELOPMENT.md §22.

Each check produces executable evidence; GPU-only paths are skipped with an
explicit reason. Usage:

    python scripts/verify_mvp.py                # includes pytest (slow but complete)
    python scripts/verify_mvp.py --skip-tests   # when the caller already ran pytest

Exit code 0 only when nothing failed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# cp936 consoles cannot encode the Chinese output (DEVELOPMENT.md §24); this also keeps
# the whole audit log UTF-8 even though check_09 runs the CLI in-process.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from envdoctor.compat.engine import analyze, decide_overall  # noqa: E402
from envdoctor.domain.enums import GpuSource, Overall, VerdictState  # noqa: E402
from envdoctor.domain.models import (  # noqa: E402
    CurrentPython,
    GpuDevice,
    GpuInfo,
    Interpreter,
    MachineProfile,
    OsInfo,
    Tooling,
)
from envdoctor.paths import WorkLayout  # noqa: E402
from envdoctor.probe.machine import probe_machine  # noqa: E402
from envdoctor.scan.scanner import scan_target  # noqa: E402
from envdoctor.verify.runner import user_env_fingerprint  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "projects"
DEV = ROOT / "scripts" / "envdoctor_dev.py"
EXTERNAL_REF_RE = re.compile(r'(?:src|href)\s*=\s*"https?://')

RESULTS: list[tuple[str, str, str]] = []
_STATE: dict[str, object] = {}


def record(status: str, name: str, detail: str) -> None:
    RESULTS.append((status, name, detail))
    print(f"{status:<4} {name:<32} {detail}")


def run_cli(*args: str, cwd: Path, strip_llm_env: bool = False) -> tuple[int, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if strip_llm_env:
        for key in list(env):
            if key.startswith("ENVDOCTOR_LLM_"):
                env.pop(key)
    proc = subprocess.run(
        [sys.executable, str(DEV), *args],
        cwd=str(cwd),
        capture_output=True,
        env=env,
        stdin=subprocess.DEVNULL,
    )
    return proc.returncode, proc.stdout.decode("utf-8", errors="replace") + proc.stderr.decode(
        "utf-8", errors="replace"
    )


def _synth_windows_gpu_profile(*, driver: str, driver_max_cuda: str) -> MachineProfile:
    return MachineProfile(
        os=OsInfo(name="Windows", release="11", arch="AMD64"),
        python=CurrentPython(version="3.11.9", executable=sys.executable, console_encoding="utf-8"),
        interpreters=[Interpreter(version="3.11.9", path=sys.executable, source="current")],
        tooling=Tooling(),
        gpu=GpuInfo(
            present=True,
            source=GpuSource.NVIDIA_SMI,
            driver_version=driver,
            driver_max_cuda=driver_max_cuda,
            devices=[
                GpuDevice(
                    index=0, name="NVIDIA GeForce RTX 3090", vram_bytes=24576 * 1024 * 1024, compute_capability="8.6"
                )
            ],
        ),
        collected_at="2026-09-22T00:00:00+08:00",
    )


# --- checks -------------------------------------------------------------------


def check_01_probe_graceful(tmp: Path) -> tuple[str, str]:
    profile = probe_machine()
    if profile.gpu.present:
        return "SKIP", "本机有 GPU：无 GPU 降级路径不适用（GPU 真机项见 -m gpu）"
    if not profile.gpu.reason:
        return "FAIL", "gpu.present=false 但缺失 reason"
    return "PASS", f"gpu=false 且带原因：{profile.gpu.reason[:56]}…"


def check_02_fixture_verdicts(tmp: Path) -> tuple[str, str]:
    work = WorkLayout(tmp / ".envdoctor")
    profile = probe_machine()
    summary: list[str] = []
    for name in ["torch-cu121", "tf-legacy", "conda-pip", "clean-uv", "broken-reqs"]:
        scan = scan_target(str(FIXTURES / name), work=work)
        verdicts = analyze(scan, profile)
        if not verdicts:
            return "FAIL", f"{name} 没有任何判定"
        for verdict in verdicts:
            payload = verdict.model_dump()
            for field in ("id", "aspect", "state", "title", "summary", "evidence", "confidence"):
                if field not in payload or payload[field] in (None, "", []):
                    return "FAIL", f"{name}: 判定 {verdict.id} 缺字段 {field}"
        summary.append(f"{name}={len(verdicts)}")
    return "PASS", "5 个 fixture 判定字段齐全（" + "、".join(summary) + "）"


def check_03_blocked_and_matrix(tmp: Path) -> tuple[str, str]:
    work = WorkLayout(tmp / ".envdoctor")
    scan = scan_target(str(FIXTURES / "torch-cu121"), work=work)

    machine_profile = probe_machine()
    machine_verdicts = analyze(scan, machine_profile)
    overall, _ = decide_overall(machine_verdicts, [])
    blocked = [verdict for verdict in machine_verdicts if verdict.state == VerdictState.BLOCKED]
    if machine_profile.gpu.present:
        machine_note = "本机有 GPU：跳过无 GPU 场景"
    else:
        if not blocked or overall != Overall.CANNOT_RUN:
            return "FAIL", f"无 GPU 机器上 torch-cu121 应 blocked+cannot_run，实际 overall={overall.value}"
        machine_note = f"本机 blocked={blocked[0].id} → {overall.value}"

    old_driver = _synth_windows_gpu_profile(driver="528.49", driver_max_cuda="12.0")
    driver_verdicts = analyze(scan, old_driver)
    driver_verdict = next((verdict for verdict in driver_verdicts if verdict.id == "driver_vs_cuda"), None)
    if driver_verdict is None or driver_verdict.state != VerdictState.BLOCKED:
        return "FAIL", "旧驱动场景应产生 blocked 的 driver_vs_cuda"
    fix_text = driver_verdict.fix.text if driver_verdict.fix else ""
    sources = " ".join(item.source or "" for item in driver_verdict.evidence)
    if "531.14" not in fix_text:
        return "FAIL", f"fix 未包含矩阵最低驱动 531.14：{fix_text}"
    if "cuda_driver_matrix.json" not in sources:
        return "FAIL", "证据未引用 cuda_driver_matrix.json"
    return "PASS", f"{machine_note}；旧驱动 528.49 → blocked，fix 含 531.14（矩阵来源）"


def _doctor_no_verify_report(tmp: Path) -> tuple[int, dict, str]:
    report_path = tmp / "no-verify-report.json"
    code, output = run_cli(
        "doctor",
        str(FIXTURES / "clean-uv"),
        "--no-verify",
        "--json",
        str(report_path),
        "--workdir",
        str(tmp / ".envdoctor"),
        "-q",
        cwd=tmp,
    )
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    _STATE["no_verify_report"] = payload
    _STATE["no_verify_dir"] = report_path.parent
    return code, payload, output


def check_04_artifacts(tmp: Path) -> tuple[str, str]:
    code, _payload, _ = _doctor_no_verify_report(tmp)
    workdir = tmp / ".envdoctor"
    runs = sorted((workdir / "runs").iterdir())
    if len(runs) != 1:
        return "FAIL", f"期望 1 个 run 目录，实际 {len(runs)}"
    run_dir = runs[0]
    for name in ("scan.json", "probe.json", "verdicts.json", "report.json", "report.html"):
        if not (run_dir / name).is_file():
            return "FAIL", f"缺少 {name}"
    _STATE["run_dir"] = run_dir
    return "PASS", f"exit={code}，5 类产物落盘（{run_dir.name}）"


def check_05_static_only_is_honest(tmp: Path) -> tuple[str, str]:
    payload = _STATE.get("no_verify_report")
    if payload is None:
        _doctor_no_verify_report(tmp)
        payload = _STATE.get("no_verify_report")
    assert isinstance(payload, dict)
    if payload["overall"] == "runs" or payload["verify_performed"]:
        return "FAIL", f"--no-verify 不应给出 runs/verify_performed：{payload['overall']}"
    return "PASS", f"--no-verify → overall={payload['overall']}（unknown，未实测）"


def check_06_real_verify(tmp: Path) -> tuple[str, str]:
    index = os.environ.get("ENVDOCTOR_TEST_INDEX") or os.environ.get("ENVDOCTOR_INDEX_URL")
    if not index and not os.environ.get("CI"):
        return "SKIP", "需要 ENVDOCTOR_TEST_INDEX 或 CI（可达安装源）才能真装真跑"
    report_path = tmp / "verify-report.json"
    extra_args = ["--index-url", index] if index else []
    code, output = run_cli(
        "doctor",
        str(FIXTURES / "tiny-ml"),
        "--yes",
        "--timeout",
        "300",
        "--json",
        str(report_path),
        "--workdir",
        str(tmp / ".envdoctor"),
        "-q",
        *extra_args,
        cwd=tmp,
    )
    if not report_path.is_file():
        return "FAIL", f"未产出报告；exit={code}；输出尾：{output.strip()[-200:]}"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    states = {checkpoint["id"]: checkpoint["state"] for checkpoint in payload["checkpoints"]}
    required = ["venv", "install", "imports", "user-env-unchanged"]
    bad = [name for name in required if states.get(name) != "pass"]
    if bad or payload["overall"] != "runs" or not payload["verify_performed"]:
        return "FAIL", f"exit={code}，overall={payload['overall']}，异常检查点={bad or '无'}"
    _STATE["verify_report"] = payload
    return "PASS", "真实验证通过：venv/install/imports 全 pass，临时环境已清理，overall=runs"


def check_07_gate_non_tty(tmp: Path) -> tuple[str, str]:
    report_path = tmp / "gate-report.json"
    code, _ = run_cli(
        "doctor",
        str(FIXTURES / "clean-uv"),
        "--json",
        str(report_path),
        "--workdir",
        str(tmp / ".envdoctor"),
        "-q",
        cwd=tmp,
    )
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    if code != 2 or payload["verify_performed"]:
        return "FAIL", f"非交互无 --yes 应跳过验证：exit={code}, verify_performed={payload['verify_performed']}"
    if "非交互" not in payload["overall_reason"] and "跳过" not in payload["overall_reason"]:
        return "FAIL", f"跳过的原因不明确：{payload['overall_reason']}"
    return "PASS", "非交互 + 无 --yes → 显式跳过验证（exit=2）"


def check_08_user_env_unchanged(tmp: Path) -> tuple[str, str]:
    payload = _STATE.get("verify_report")
    if isinstance(payload, dict):
        states = {checkpoint["id"]: checkpoint["state"] for checkpoint in payload["checkpoints"]}
        if states.get("user-env-unchanged") == "pass":
            detail = next(
                checkpoint["detail"]
                for checkpoint in payload["checkpoints"]
                if checkpoint["id"] == "user-env-unchanged"
            )
            return "PASS", f"验证前后指纹一致（{detail}）"
        return "FAIL", "user-env-unchanged 检查点不为 pass"
    before = user_env_fingerprint()
    after = user_env_fingerprint()
    if before != after:
        return "FAIL", "解释器包集合指纹不稳定"
    return "PASS", f"指纹稳定 {before}（未覆盖真实安装路径，见 22-6 跳过原因）"


def check_09_interrupt_cleanup(tmp: Path) -> tuple[str, str]:
    from envdoctor.cli import main as cli_main

    class _InterruptingRunner:
        def __init__(self, **_kwargs) -> None:
            pass

        def run_checks(self):
            raise KeyboardInterrupt

    original = cli_main.VerifyRunner
    cli_main.VerifyRunner = _InterruptingRunner  # type: ignore[assignment]
    argv_backup = sys.argv
    sys.argv = [
        "envdoctor",
        "doctor",
        str(FIXTURES / "tiny-ml"),
        "--yes",
        "--workdir",
        str(tmp / ".envdoctor"),
    ]
    code = None
    try:
        try:
            cli_main.app()
        except SystemExit as exc:
            code = exc.code
    finally:
        cli_main.VerifyRunner = original  # type: ignore[assignment]
        sys.argv = argv_backup
    if code != 130:
        return "FAIL", f"中断退出码应为 130，实际 {code}"
    run_dirs = sorted((tmp / ".envdoctor" / "runs").iterdir())
    if not run_dirs or not (run_dirs[-1] / "interrupted").is_file():
        return "FAIL", "run 目录缺少 interrupted 标记"
    return "PASS", "退出码 130 + interrupted 标记（合成中断；真实控制台 Ctrl+C 建议人工复核一次）"


def check_10_explain_degrades(tmp: Path) -> tuple[str, str]:
    code, output = run_cli(
        "doctor",
        str(FIXTURES / "clean-uv"),
        "--no-verify",
        "--explain",
        "--workdir",
        str(tmp / ".envdoctor"),
        "-q",
        cwd=tmp,
        strip_llm_env=True,
    )
    if code != 2:
        return "FAIL", f"--explain 失败不应改变退出码（期望 2，实际 {code}）"
    if "AI 解释已跳过" not in output:
        return "FAIL", "未给出 AI 解释跳过提示"
    return "PASS", "未配置端点 → 明确警告且退出码不变（2）"


def check_11_html_self_contained(tmp: Path) -> tuple[str, str]:
    run_dir = _STATE.get("run_dir")
    if not isinstance(run_dir, Path):
        return "FAIL", "缺少 run 目录（依赖 22-4）"
    html = (run_dir / "report.html").read_text(encoding="utf-8")
    if EXTERNAL_REF_RE.search(html):
        return "FAIL", "HTML 存在外部资源引用"
    return "PASS", f"report.html 自包含（{len(html)} bytes，无外部 src/href）"


def check_12_quality_gate(_tmp: Path, *, skip_tests: bool) -> tuple[str, str]:
    steps = [
        ("ruff", [sys.executable, "-m", "ruff", "check", "src", "tests", "scripts"]),
        ("ruff format", [sys.executable, "-m", "ruff", "format", "--check", "src"]),
        ("pyright", [sys.executable, "-m", "pyright"]),
    ]
    if not skip_tests:
        steps.append(("pytest", [sys.executable, "-m", "pytest", "-q", "-m", "not gpu"]))
    failures: list[str] = []
    for name, argv in steps:
        proc = subprocess.run(argv, cwd=str(ROOT), capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        if proc.returncode != 0:
            tail = proc.stdout.decode("utf-8", errors="replace").strip().splitlines()[-3:]
            failures.append(f"{name}: {' | '.join(tail)}")
    if failures:
        return "FAIL", "；".join(failures)
    names = " + ".join(name for name, _ in steps)
    return "PASS", f"{names} 全绿" + ("（pytest 由调用方执行）" if skip_tests else "")


def check_13_knowledge_base_auditable(_tmp: Path) -> tuple[str, str]:
    """§22-13：知识库每条规则都有官方来源、修复动作与单元测试。"""
    from envdoctor.compat.knowledge import (
        load_matrix,
        load_package_cuda_requirements,
        load_rules,
    )

    rules = load_rules()
    if not rules:
        return "FAIL", "规则库为空"
    missing: list[str] = []
    for rule in rules:
        source = rule.get("source") or {}
        if not source.get("url"):
            missing.append(f"{rule.get('id')}: 缺 source.url")
        if not rule.get("fix"):
            missing.append(f"{rule.get('id')}: 缺 fix")
        if not rule.get("message"):
            missing.append(f"{rule.get('id')}: 缺 message")
    if missing:
        return "FAIL", "；".join(missing[:5])

    for entry in load_package_cuda_requirements():
        if not entry.source_url:
            return "FAIL", f"包级 CUDA 需求 {entry.name} 缺来源链接"

    matrix = load_matrix()
    if not matrix.covered_versions():
        return "FAIL", "驱动矩阵为空"
    if matrix.min_driver("12.1", "windows") != "531.14":
        return "FAIL", "驱动矩阵数值与官方表不一致（12.1/windows）"

    return (
        "PASS",
        f"{len(rules)} 条规则全部带来源与修复动作；矩阵覆盖 {len(matrix.covered_versions())} 个 CUDA 版本",
    )


def main() -> int:
    skip_tests = "--skip-tests" in sys.argv[1:]
    tmp_root = Path(tempfile.mkdtemp(prefix="envdoctor-mvp-"))
    checks = [
        ("22-1", check_01_probe_graceful),
        ("22-2", check_02_fixture_verdicts),
        ("22-3", check_03_blocked_and_matrix),
        ("22-4", check_04_artifacts),
        ("22-5", check_05_static_only_is_honest),
        ("22-6", check_06_real_verify),
        ("22-7", check_07_gate_non_tty),
        ("22-8", check_08_user_env_unchanged),
        ("22-9", check_09_interrupt_cleanup),
        ("22-10", check_10_explain_degrades),
        ("22-11", check_11_html_self_contained),
        ("22-13", check_13_knowledge_base_auditable),
    ]
    try:
        for number, check in checks:
            try:
                check_dir = tmp_root / number
                check_dir.mkdir(parents=True, exist_ok=True)
                status, detail = check(check_dir)
            except Exception as exc:
                status, detail = "FAIL", f"{type(exc).__name__}: {exc}"
            record(status, f"{number} {check.__name__.removeprefix('check_')}", detail)
        try:
            gate_dir = tmp_root / "22-12"
            gate_dir.mkdir(parents=True, exist_ok=True)
            status, detail = check_12_quality_gate(gate_dir, skip_tests=skip_tests)
        except Exception as exc:
            status, detail = "FAIL", f"{type(exc).__name__}: {exc}"
        record(status, "22-12 quality_gate", detail)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    passed = sum(1 for status, _, _ in RESULTS if status == "PASS")
    skipped = sum(1 for status, _, _ in RESULTS if status == "SKIP")
    failed = sum(1 for status, _, _ in RESULTS if status == "FAIL")
    print(f"\n{passed} PASS / {skipped} SKIP / {failed} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
