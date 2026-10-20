from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from envdoctor.config import EnvDoctorConfig
from envdoctor.domain.enums import CheckpointState
from envdoctor.domain.models import Dependency, Interpreter
from envdoctor.paths import RunLayout, WorkLayout
from envdoctor.scan.scanner import scan_target
from envdoctor.subproc import CommandResult
from envdoctor.verify.planner import (
    VerifyOptions,
    build_plan,
    choose_interpreter,
    requirement_lines_from_deps,
)
from envdoctor.verify.runner import (
    VerifyRunner,
    check_reachability,
    user_env_fingerprint,
    venv_python_path,
)
from support import make_profile

UV = "C:/fake/uv.exe"


def _scan_fixture(projects_dir: Path, tmp_path: Path, name: str):
    work = WorkLayout(tmp_path / ".envdoctor")
    return scan_target(str(projects_dir / name), work=work)


# --- planner ------------------------------------------------------------------


def test_choose_interpreter_requested() -> None:
    from support import make_scan

    scan = make_scan([])
    profile = make_profile(interpreters=("3.11.9", "3.12.4"))
    chosen, reason = choose_interpreter(scan, profile, "3.12")
    assert chosen is not None
    assert chosen.version == "3.12.4"
    assert "命中" in reason

    missing, reason = choose_interpreter(scan, profile, "3.10")
    assert missing is None
    assert "未找到版本为 3.10" in reason


def test_choose_interpreter_respects_requires_python() -> None:
    from support import make_scan

    scan = make_scan([], python_requires=">=3.12")
    profile = make_profile(interpreters=("3.11.9", "3.12.4"))
    chosen, reason = choose_interpreter(scan, profile, None)
    assert chosen is not None
    assert chosen.version == "3.12.4"

    unsatisfiable, reason = choose_interpreter(make_scan([], python_requires="==3.10.*"), profile, None)
    assert unsatisfiable is None
    assert "--python" in reason


def test_requirement_lines_merge_and_filter() -> None:
    deps = [
        Dependency(name="torch", specifier=None, source="a", group="default"),
        Dependency(name="torch", specifier="==2.4.1", source="b", group="default"),
        Dependency(name="numpy", specifier=">=1.26", source="a", group="default"),
        Dependency(name="pytorch-cuda", specifier="==12.1", source="c", group="conda"),
        Dependency(name="pytest", specifier=">=8", source="d", group="dev"),
        Dependency(name="requests", specifier=None, extras=["socks"], marker="python_version >= '3.10'", source="a"),
    ]
    lines, skipped = requirement_lines_from_deps(deps)
    assert "torch==2.4.1" in lines  # pinned variant wins
    assert "numpy>=1.26" in lines
    assert all("pytorch-cuda" not in line for line in lines)
    assert "requests[socks] ; python_version >= '3.10'" in lines
    assert skipped == ["dev"]


def test_build_plan_index_priority(projects_dir: Path, tmp_path: Path) -> None:
    scan = _scan_fixture(projects_dir, tmp_path, "torch-cu121")
    config = EnvDoctorConfig()
    config.defaults.index_url = "https://config.example/simple"
    options = VerifyOptions(index_url="https://flag.example/simple", extra_index_urls=["https://extra.example/simple"])
    plan = build_plan(scan, make_profile(), options, config)
    assert plan.primary_index == "https://flag.example/simple"
    assert "https://download.pytorch.org/whl/cu121" in plan.extra_indexes
    assert "https://config.example/simple" in plan.extra_indexes
    assert "https://extra.example/simple" in plan.extra_indexes
    assert plan.installable
    assert [name for name, _module in plan.ml_modules] == ["torch", "torchvision"]


def test_build_plan_without_flags_uses_project_index(projects_dir: Path, tmp_path: Path) -> None:
    scan = _scan_fixture(projects_dir, tmp_path, "torch-cu121")
    plan = build_plan(scan, make_profile(), VerifyOptions(), EnvDoctorConfig())
    assert plan.primary_index == "https://download.pytorch.org/whl/cu121"
    assert plan.extra_indexes == []


# --- runner -------------------------------------------------------------------


class FakeChild:
    """A fake subprocess layer for VerifyRunner: patterns, not real processes."""

    def __init__(self, *, fail_install: bool = False, fail_venv: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.fail_install = fail_install
        self.fail_venv = fail_venv
        self.venv_dirs: list[str] = []

    def __call__(self, argv, *, timeout_s=30.0, cwd=None, env_extra=None, stdin_data=None):
        self.calls.append(list(argv))
        if len(argv) >= 2 and argv[1] == "venv":
            env_dir = argv[-1]
            self.venv_dirs.append(env_dir)
            if self.fail_venv:
                return CommandResult(argv=argv, returncode=1, stderr="venv creation failed", seconds=0.01)
            scripts = Path(env_dir) / "Scripts"
            scripts.mkdir(parents=True, exist_ok=True)
            (scripts / "python.exe").write_text("", encoding="utf-8")
            return CommandResult(argv=argv, returncode=0, seconds=0.01)
        if len(argv) >= 2 and argv[1] == "pip":
            if self.fail_install:
                return CommandResult(
                    argv=argv, returncode=1, stderr="ERROR: No matching distribution found", seconds=0.02
                )
            return CommandResult(argv=argv, returncode=0, stderr="Installed 3 packages", seconds=0.02)
        if len(argv) >= 2 and argv[1] == "-c":
            return CommandResult(argv=argv, returncode=0, stdout="1.2.3\n", seconds=0.01)
        if argv and str(argv[-1]).endswith("-script.py"):
            payload = {"python": "3.11.9", "torch": None}
            if "device" in str(argv[-1]):
                payload = {"python": "3.11.9"}
            return CommandResult(argv=argv, returncode=0, stdout=json.dumps(payload), seconds=0.01)
        if argv and argv[0] in {"cmd", "sh"}:
            return CommandResult(argv=argv, returncode=0, stdout="ok", seconds=0.01)
        return CommandResult(argv=argv, returncode=0, seconds=0.01)


def _plan(projects_dir: Path, tmp_path: Path, name: str = "tiny-ml", **options_kwargs):
    scan = _scan_fixture(projects_dir, tmp_path, name)
    options = VerifyOptions(**options_kwargs)
    return build_plan(scan, make_profile(), options, EnvDoctorConfig())


def _runner(tmp_path: Path, plan, child: FakeChild, **kwargs) -> VerifyRunner:
    return VerifyRunner(
        run=RunLayout(tmp_path / "run"),
        plan=plan,
        uv_path=kwargs.pop("uv_path", UV),
        runner=child,
        reachability=kwargs.pop("reachability", lambda url, **kw: True),
        echo=kwargs.pop("echo", None),
    )


def test_runner_happy_path_cleans_up(projects_dir: Path, tmp_path: Path) -> None:
    plan = _plan(projects_dir, tmp_path, smoke_cmds=["echo hello"])
    child = FakeChild()
    result = _runner(tmp_path, plan, child).run_checks()
    states = {checkpoint.id: checkpoint.state for checkpoint in result.checkpoints}
    assert states["venv"] == CheckpointState.PASS
    assert states["install"] == CheckpointState.PASS
    assert states["imports"] == CheckpointState.PASS
    assert states["device"] == CheckpointState.PASS
    assert states["minimal-model"] == CheckpointState.SKIP  # no torch/tf/jax in tiny-ml
    assert states["smoke-1"] == CheckpointState.PASS
    assert states["user-env-unchanged"] == CheckpointState.PASS
    assert result.env_dir is None  # cleaned up by default
    assert child.venv_dirs and not Path(child.venv_dirs[0]).exists()
    requirements = Path(result.requirements_path or "").read_text(encoding="utf-8")
    assert "scikit-learn" in requirements


def test_runner_keep_env_preserves_directory(projects_dir: Path, tmp_path: Path) -> None:
    plan = _plan(projects_dir, tmp_path, keep_env=True)
    child = FakeChild()
    result = _runner(tmp_path, plan, child).run_checks()
    assert result.env_dir is not None
    assert Path(result.env_dir).exists()
    shutil.rmtree(result.env_dir, ignore_errors=True)


def test_runner_uv_missing(projects_dir: Path, tmp_path: Path) -> None:
    plan = _plan(projects_dir, tmp_path)
    result = _runner(tmp_path, plan, FakeChild(), uv_path=None).run_checks()
    assert result.note is not None and "uv" in result.note
    assert [checkpoint.state for checkpoint in result.checkpoints] == [CheckpointState.SKIP]


def test_runner_no_interpreter_note(projects_dir: Path, tmp_path: Path) -> None:
    scan = _scan_fixture(projects_dir, tmp_path, "tiny-ml")
    plan = build_plan(scan, make_profile(interpreters=()), VerifyOptions(), EnvDoctorConfig())
    result = _runner(tmp_path, plan, FakeChild()).run_checks()
    assert result.note is not None
    assert result.checkpoints == []


def test_runner_unreachable_index_skips_install(projects_dir: Path, tmp_path: Path) -> None:
    plan = _plan(projects_dir, tmp_path, index_url="https://blocked.example/simple")
    result = _runner(tmp_path, plan, FakeChild(), reachability=lambda url, **kw: False).run_checks()
    states = {checkpoint.id: checkpoint.state for checkpoint in result.checkpoints}
    assert states["venv"] == CheckpointState.PASS
    assert states["install"] == CheckpointState.SKIP
    assert states["user-env-unchanged"] == CheckpointState.PASS


def test_runner_install_failure_stops_chain(projects_dir: Path, tmp_path: Path) -> None:
    plan = _plan(projects_dir, tmp_path)
    result = _runner(tmp_path, plan, FakeChild(fail_install=True)).run_checks()
    states = {checkpoint.id: checkpoint.state for checkpoint in result.checkpoints}
    assert states["install"] == CheckpointState.FAIL
    assert "imports" not in states
    assert "device" not in states
    assert states["user-env-unchanged"] == CheckpointState.PASS


def test_runner_venv_failure(projects_dir: Path, tmp_path: Path) -> None:
    plan = _plan(projects_dir, tmp_path)
    result = _runner(tmp_path, plan, FakeChild(fail_venv=True)).run_checks()
    states = {checkpoint.id: checkpoint.state for checkpoint in result.checkpoints}
    assert states["venv"] == CheckpointState.FAIL
    assert "install" not in states


def test_runner_no_installable_deps(tmp_path: Path) -> None:
    from support import make_scan

    scan = make_scan([])
    plan = build_plan(scan, make_profile(), VerifyOptions(), EnvDoctorConfig())
    result = _runner(tmp_path, plan, FakeChild()).run_checks()
    assert result.note is not None
    assert plan.installable is False


# --- helpers ------------------------------------------------------------------


def test_user_env_fingerprint_stable() -> None:
    first = user_env_fingerprint()
    assert first == user_env_fingerprint()
    assert len(first) == 16


def test_venv_python_path(tmp_path: Path) -> None:
    env = tmp_path / "env"
    (env / "Scripts").mkdir(parents=True)
    (env / "Scripts" / "python.exe").write_text("", encoding="utf-8")
    assert venv_python_path(str(env)).endswith("python.exe")
    other = tmp_path / "other"
    (other / "bin").mkdir(parents=True)
    assert venv_python_path(str(other)).endswith(str(Path("bin") / "python"))


def test_check_reachability_refused_port() -> None:
    assert check_reachability("http://127.0.0.1:1/simple", timeout_s=1.0) is False


def test_interpreter_dataclass_roundtrip() -> None:
    interpreter = Interpreter(version="3.11.9", path=sys.executable, source="current")
    assert interpreter.version == "3.11.9"
