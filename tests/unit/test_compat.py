from __future__ import annotations

from pathlib import Path

from envdoctor.compat.engine import (
    _may_install_at_or_above,
    _may_install_below,
    analyze,
    build_context,
    decide_overall,
    evaluate_condition,
)
from envdoctor.compat.knowledge import load_matrix
from envdoctor.domain.enums import (
    Aspect,
    CheckpointState,
    Confidence,
    Overall,
    VerdictState,
)
from envdoctor.domain.models import Checkpoint, Dependency
from envdoctor.paths import WorkLayout
from envdoctor.scan.scanner import scan_target
from support import make_profile, make_scan


def _scan_fixture(projects_dir: Path, tmp_path: Path, name: str):
    work = WorkLayout(tmp_path / ".envdoctor")
    return scan_target(str(projects_dir / name), work=work)


def _by_id(verdicts, verdict_id: str):
    found = [verdict for verdict in verdicts if verdict.id == verdict_id]
    assert found, f"缺少判定：{verdict_id}（实际：{[v.id for v in verdicts]}）"
    return found[0]


# --- matrix -------------------------------------------------------------------


def test_matrix_exact_lookup() -> None:
    matrix = load_matrix()
    assert matrix.min_driver("12.1", "Windows") == "531.14"
    assert matrix.min_driver("12.1", "Linux") == "530.30.02"
    assert matrix.min_driver("11.8", "windows") == "520.06"


def test_matrix_covers_current_cuda_releases() -> None:
    """Values follow the official NVIDIA table (GA rows) — see the data file's source_url."""
    matrix = load_matrix()
    assert matrix.min_driver("12.7", "windows") == "570.65"
    assert matrix.min_driver("12.8", "windows") == "570.65"
    assert matrix.min_driver("12.9", "windows") == "576.02"
    assert matrix.min_driver("13.0", "windows") == "580"
    assert matrix.min_driver("13.0", "linux") == "580.65.06"


def test_matrix_major_only_uses_lowest_minor() -> None:
    matrix = load_matrix()
    assert matrix.min_driver("12", "windows") == "527.41"
    assert matrix.min_driver("12", "linux") == "525.60.13"


def test_matrix_unknown_and_unsupported_platform() -> None:
    matrix = load_matrix()
    assert matrix.min_driver("14.0", "windows") is None
    assert matrix.min_driver("12.1", "Darwin") is None


def test_matrix_max_runtime_for_driver() -> None:
    matrix = load_matrix()
    assert matrix.max_runtime_for_driver("551.61", "windows") == "12.4"
    assert matrix.max_runtime_for_driver("555.85", "windows") == "12.5"
    assert matrix.max_runtime_for_driver("999.0", "windows") == "13.0"
    assert matrix.max_runtime_for_driver("519.0", "windows") is None
    assert matrix.max_runtime_for_driver("530.30.02", "linux") == "12.1"
    assert matrix.max_runtime_for_driver("not-a-version", "windows") is None


# --- condition DSL ------------------------------------------------------------


def test_may_install_below() -> None:
    assert _may_install_below([], "0.43") is True
    assert _may_install_below([">=0.43"], "0.43") is False
    assert _may_install_below(["==0.42.0"], "0.43") is True
    assert _may_install_below([">=0.40,<0.45"], "0.43") is True
    assert _may_install_below(["==0.43.0"], "0.43") is False
    assert _may_install_below(["garbage"], "0.43") is True


def test_may_install_at_or_above() -> None:
    assert _may_install_at_or_above([], "2.11") is True
    assert _may_install_at_or_above(["==2.10.1"], "2.11") is False
    assert _may_install_at_or_above([">=2.16"], "2.11") is True
    assert _may_install_at_or_above(["<2.11"], "2.11") is False
    assert _may_install_at_or_above([">=2.9"], "2.11") is True


def test_evaluate_condition_boolean_composition(tmp_path: Path) -> None:
    scan = make_scan([Dependency(name="tensorflow", specifier="==2.10.1", source="s")])
    ctx = build_context(scan, make_profile())
    assert evaluate_condition({"kind": "lib_present", "name": "tensorflow"}, ctx) is True
    assert evaluate_condition({"all": [{"kind": "lib_present", "name": "tensorflow"}]}, ctx) is True
    assert evaluate_condition({"not": {"kind": "lib_present", "name": "torch"}}, ctx) is True
    assert evaluate_condition({"any": [{"kind": "lib_present", "name": "torch"}]}, ctx) is False
    assert evaluate_condition({"kind": "os", "name": "windows"}, ctx) is True
    assert evaluate_condition({"kind": "gpu_present", "value": False}, ctx) is True
    assert evaluate_condition({"kind": "unknown-kind"}, ctx) is False


# --- built-in verdicts --------------------------------------------------------


def test_python_blocked_without_matching_interpreter(projects_dir: Path, tmp_path: Path) -> None:
    scan = _scan_fixture(projects_dir, tmp_path, "clean-uv")
    verdicts = analyze(scan, make_profile(interpreters=("3.10.9",)))
    verdict = _by_id(verdicts, "python_available")
    assert verdict.state == VerdictState.BLOCKED
    assert verdict.fix is not None
    assert verdict.fix.command == "uv python install 3.11"


def test_python_ok_and_na(projects_dir: Path, tmp_path: Path) -> None:
    scan = _scan_fixture(projects_dir, tmp_path, "clean-uv")
    verdict = _by_id(analyze(scan, make_profile(interpreters=("3.11.9",))), "python_available")
    assert verdict.state == VerdictState.OK

    scan_no_python = _scan_fixture(projects_dir, tmp_path, "torch-cu121")
    verdict_na = _by_id(analyze(scan_no_python, make_profile()), "python_available")
    assert verdict_na.state == VerdictState.NOT_APPLICABLE


def test_gpu_hard_requires_gpu(projects_dir: Path, tmp_path: Path) -> None:
    scan = _scan_fixture(projects_dir, tmp_path, "torch-cu121")
    verdicts = analyze(scan, make_profile(gpu_present=False))
    verdict = _by_id(verdicts, "gpu_presence")
    assert verdict.state == VerdictState.BLOCKED
    overall, reason = decide_overall(verdicts, [], verify_note=None)
    assert overall == Overall.CANNOT_RUN  # §22-3
    assert "阻断" in reason


def test_gpu_soft_warns_on_cpu_only(projects_dir: Path, tmp_path: Path) -> None:
    scan = _scan_fixture(projects_dir, tmp_path, "tf-legacy")
    verdict = _by_id(analyze(scan, make_profile(gpu_present=False)), "gpu_presence")
    assert verdict.state == VerdictState.WARN


def test_driver_satisfied_and_blocked(projects_dir: Path, tmp_path: Path) -> None:
    scan = _scan_fixture(projects_dir, tmp_path, "torch-cu121")
    ok_profile = make_profile(gpu_present=True, driver="551.61", driver_max_cuda="12.4")
    verdict_ok = _by_id(analyze(scan, ok_profile), "driver_vs_cuda")
    assert verdict_ok.state == VerdictState.OK

    old_profile = make_profile(gpu_present=True, driver="528.49", driver_max_cuda="12.0")
    verdict_bad = _by_id(analyze(scan, old_profile), "driver_vs_cuda")
    assert verdict_bad.state == VerdictState.BLOCKED
    assert verdict_bad.fix is not None
    assert "531.14" in verdict_bad.fix.text


def test_driver_major_only_is_conservative(projects_dir: Path, tmp_path: Path) -> None:
    scan = _scan_fixture(projects_dir, tmp_path, "torch-cu121")
    scan.cuda.requested_runtime = "12"
    profile = make_profile(gpu_present=True, driver="531.14", driver_max_cuda="12.4")
    verdict = _by_id(analyze(scan, profile), "driver_vs_cuda")
    assert verdict.state == VerdictState.OK
    assert verdict.confidence == Confidence.LOW
    assert "保守比较" in verdict.summary


def test_driver_unknown_runtime_is_unknown(projects_dir: Path, tmp_path: Path) -> None:
    scan = _scan_fixture(projects_dir, tmp_path, "torch-cu121")
    scan.cuda.requested_runtime = "14.0"
    profile = make_profile(gpu_present=True, driver="580.0", driver_max_cuda="13.0")
    verdict = _by_id(analyze(scan, profile), "driver_vs_cuda")
    assert verdict.state == VerdictState.UNKNOWN


def test_cuda_index_missing_warns(projects_dir: Path, tmp_path: Path) -> None:
    scan = _scan_fixture(projects_dir, tmp_path, "broken-reqs")
    verdict = _by_id(analyze(scan, make_profile()), "cuda_index_missing")
    assert verdict.state == VerdictState.WARN
    assert verdict.fix is not None
    assert "cu124" in (verdict.fix.command or "")

    ok_scan = _scan_fixture(projects_dir, tmp_path, "torch-cu121")
    assert _by_id(analyze(ok_scan, make_profile()), "cuda_index_configured").state == VerdictState.OK


def test_conda_env_present(projects_dir: Path, tmp_path: Path) -> None:
    scan = _scan_fixture(projects_dir, tmp_path, "conda-pip")
    verdict = _by_id(analyze(scan, make_profile()), "conda_env_present")
    assert verdict.state == VerdictState.WARN


# --- rules --------------------------------------------------------------------


def _rule_ids(scan, profile) -> set[str]:
    return {verdict.rule_id for verdict in analyze(scan, profile) if verdict.rule_id}


def test_tf_rule_fires_for_modern_not_legacy(projects_dir: Path, tmp_path: Path) -> None:
    modern = _scan_fixture(projects_dir, tmp_path, "tf-modern")
    assert "tf_gpu_windows_post_210" in _rule_ids(modern, make_profile())
    assert "tf_gpu_windows_post_210" not in _rule_ids(modern, make_profile(os_name="Linux"))

    legacy = _scan_fixture(projects_dir, tmp_path, "tf-legacy")
    assert "tf_gpu_windows_post_210" not in _rule_ids(legacy, make_profile())


def test_flash_attn_bitsandbytes_triton_deepspeed_rules() -> None:
    def deps(*items: tuple[str, str | None]) -> list[Dependency]:
        return [Dependency(name=name, specifier=spec, source="requirements.txt") for name, spec in items]

    profile = make_profile()
    windows = _rule_ids(make_scan(deps(("flash-attn", None))), profile)
    assert "flash_attn_windows_source_build" in windows

    assert "bitsandbytes_windows_old" in _rule_ids(make_scan(deps(("bitsandbytes", None))), profile)
    assert "bitsandbytes_windows_old" not in _rule_ids(make_scan(deps(("bitsandbytes", ">=0.43"))), profile)
    assert "bitsandbytes_windows_old" in _rule_ids(make_scan(deps(("bitsandbytes", "==0.42.0"))), profile)

    assert "triton_windows_old" in _rule_ids(make_scan(deps(("triton", None))), profile)
    assert "triton_windows_old" not in _rule_ids(make_scan(deps(("triton", ">=3.1"))), profile)

    assert "deepspeed_windows" in _rule_ids(make_scan(deps(("deepspeed", None))), profile)
    assert "xformers_torch_pin" in _rule_ids(make_scan(deps(("xformers", None), ("torch", None))), profile)


def test_jax_rule_windows_only() -> None:
    scan = make_scan([Dependency(name="jax", specifier=">=0.4", source="requirements.txt")])
    assert "jax_windows_no_gpu_wheel" in _rule_ids(scan, make_profile())
    assert "jax_windows_no_gpu_wheel" not in _rule_ids(scan, make_profile(os_name="Linux"))


# --- overall decision ---------------------------------------------------------


def _checkpoint(identifier: str, state: CheckpointState) -> Checkpoint:
    return Checkpoint(id=identifier, title=identifier, state=state)


def test_decide_overall_paths() -> None:
    runs_verdicts = analyze(make_scan([]), make_profile())
    passes = [_checkpoint("venv", CheckpointState.PASS), _checkpoint("install", CheckpointState.PASS)]
    overall, reason = decide_overall(runs_verdicts, passes)
    assert overall == Overall.RUNS
    assert "通过" in reason

    failed = [*passes[:-1], _checkpoint("install", CheckpointState.FAIL)]
    assert decide_overall(runs_verdicts, failed)[0] == Overall.CANNOT_RUN

    skipped = [_checkpoint("venv", CheckpointState.PASS), _checkpoint("install", CheckpointState.SKIP)]
    assert decide_overall(runs_verdicts, skipped)[0] == Overall.UNKNOWN

    assert decide_overall(runs_verdicts, [], verify_note="非交互终端且未提供 --yes")[0] == Overall.UNKNOWN
    assert decide_overall(runs_verdicts, [])[0] == Overall.UNKNOWN


def test_scenario_windows_no_gpu_torch_cu121_is_cannot_run(projects_dir: Path, tmp_path: Path) -> None:
    """§22-3 end to end at the analysis layer (probe is faked as a no-GPU Windows box)."""
    scan = _scan_fixture(projects_dir, tmp_path, "torch-cu121")
    verdicts = analyze(scan, make_profile(gpu_present=False))
    overall, _ = decide_overall(verdicts, [])
    assert overall == Overall.CANNOT_RUN
    gpu_verdict = _by_id(verdicts, "gpu_presence")
    assert gpu_verdict.aspect == Aspect.GPU
