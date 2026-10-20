"""Tests for the data-driven knowledge base: driver matrix, package CUDA needs, rules.

Everything asserted here is backed by a source in ``compat/data/*.json`` — if a
number changes upstream, the data file changes and this file follows.
"""

from __future__ import annotations

from envdoctor.compat.engine import (
    _pinned_below,
    _requires_at_least,
    analyze,
    build_context,
    evaluate_condition,
)
from envdoctor.compat.knowledge import load_matrix, load_package_cuda_requirements
from envdoctor.domain.enums import CheckpointState, Overall, VerdictState
from envdoctor.domain.models import Checkpoint, Dependency
from support import make_profile, make_scan


def _by_id(verdicts, verdict_id: str):
    found = [verdict for verdict in verdicts if verdict.id == verdict_id]
    assert found, f"缺少判定：{verdict_id}（实际：{[v.id for v in verdicts]}）"
    return found[0]


def _deps(*items: tuple[str, str | None]) -> list[Dependency]:
    return [Dependency(name=name, specifier=spec, source="requirements.txt") for name, spec in items]


# --- version predicates --------------------------------------------------------


def test_pinned_below_requires_an_explicit_ceiling() -> None:
    assert _pinned_below([], "2.3") is False  # 未固定 ≠ 固定在旧版
    assert _pinned_below(["==2.1.0"], "2.3") is True
    assert _pinned_below(["<2.3"], "2.3") is True
    assert _pinned_below([">=2.1,<2.2"], "2.3") is True
    assert _pinned_below(["==2.4.0"], "2.3") is False
    assert _pinned_below([">=2.4"], "2.3") is False
    assert _pinned_below(["garbage"], "2.3") is False


def test_requires_at_least_requires_an_explicit_floor() -> None:
    assert _requires_at_least([], "1.19") is False
    assert _requires_at_least([">=1.19"], "1.19") is True
    assert _requires_at_least(["==1.30.0"], "1.19") is True
    assert _requires_at_least(["==1.18.0"], "1.19") is False
    assert _requires_at_least(["<1.19"], "1.19") is False


def test_evaluate_condition_supports_new_kinds() -> None:
    scan = make_scan(_deps(("torch", "==2.1.0"), ("onnxruntime-gpu", ">=1.19")))
    ctx = build_context(scan, make_profile())
    assert evaluate_condition({"kind": "lib_pinned_below", "name": "torch", "version": "2.3"}, ctx) is True
    assert (
        evaluate_condition({"kind": "lib_requires_at_least", "name": "onnxruntime-gpu", "version": "1.19"}, ctx) is True
    )
    assert evaluate_condition({"kind": "lib_requires_at_least", "name": "torch", "version": "2.3"}, ctx) is False


# --- package CUDA requirements ------------------------------------------------


def test_package_cuda_requirements_data_is_well_formed() -> None:
    entries = load_package_cuda_requirements()
    assert entries, "包级 CUDA 需求表不能为空"
    for entry in entries:
        assert entry.name
        assert entry.cuda
        assert entry.source_url, f"{entry.name} 缺少来源链接"


def test_package_cuda_requirement_warns_when_driver_too_old() -> None:
    """onnxruntime-gpu 未固定版本时可能解析到需要 CUDA 13.0 的版本。"""
    scan = make_scan(_deps(("onnxruntime-gpu", None)))
    profile = make_profile(gpu_present=True, driver="551.61", driver_max_cuda="12.4")
    verdict = _by_id(analyze(scan, profile), "package_cuda_onnxruntime-gpu")
    assert verdict.state == VerdictState.WARN
    assert verdict.fix is not None
    assert "580" in verdict.fix.text


def test_package_cuda_requirement_blocks_when_pinned_above_driver() -> None:
    scan = make_scan(_deps(("onnxruntime-gpu", ">=1.27")))
    profile = make_profile(gpu_present=True, driver="551.61", driver_max_cuda="12.4")
    verdict = _by_id(analyze(scan, profile), "package_cuda_onnxruntime-gpu")
    assert verdict.state == VerdictState.BLOCKED
    overall, _reason = __import__("envdoctor.compat.engine", fromlist=["decide_overall"]).decide_overall(
        analyze(scan, profile), []
    )
    assert overall == Overall.CANNOT_RUN


def test_package_cuda_requirement_ok_with_new_driver() -> None:
    scan = make_scan(_deps(("onnxruntime-gpu", ">=1.27")))
    profile = make_profile(gpu_present=True, driver="580.65.06", driver_max_cuda="13.0")
    verdict = _by_id(analyze(scan, profile), "package_cuda_onnxruntime-gpu")
    assert verdict.state == VerdictState.OK


def test_package_cuda_requirement_absent_package_is_silent() -> None:
    scan = make_scan(_deps(("torch", "==2.4.1")))
    verdicts = analyze(scan, make_profile(gpu_present=True, driver="551.61", driver_max_cuda="12.4"))
    assert not [verdict for verdict in verdicts if verdict.id.startswith("package_cuda_")]


def test_package_cuda_requirement_older_pinned_version_is_silent() -> None:
    """onnxruntime-gpu==1.19 不触发 CUDA 12.8/13.0 的要求条目。"""
    scan = make_scan(_deps(("onnxruntime-gpu", "==1.19.0")))
    verdicts = analyze(scan, make_profile(gpu_present=True, driver="551.61", driver_max_cuda="12.4"))
    assert not [verdict for verdict in verdicts if verdict.id.startswith("package_cuda_")]


# --- rules --------------------------------------------------------------------


def _rule_ids(scan, profile) -> set[str]:
    return {verdict.rule_id for verdict in analyze(scan, profile) if verdict.rule_id}


def test_vllm_rule_blocks_on_windows_only() -> None:
    scan = make_scan(_deps(("vllm", None)))
    windows = _rule_ids(scan, make_profile(os_name="Windows"))
    assert "vllm_no_windows_support" in windows
    assert "vllm_no_windows_support" not in _rule_ids(scan, make_profile(os_name="Linux"))

    verdict = _by_id(analyze(scan, make_profile(os_name="Windows")), "vllm_no_windows_support")
    assert verdict.state == VerdictState.BLOCKED
    assert verdict.fix is not None
    assert "WSL" in verdict.fix.text


def test_apex_rule_is_a_warning_on_windows() -> None:
    scan = make_scan(_deps(("apex", None)))
    verdict = _by_id(analyze(scan, make_profile(os_name="Windows")), "apex_windows_experimental")
    assert verdict.state == VerdictState.WARN


def test_numpy2_abi_rule_fires_only_for_old_pinned_torch() -> None:
    old = make_scan(_deps(("numpy", ">=1.26"), ("torch", "==2.1.0")))
    assert "numpy2_torch_abi_break" in _rule_ids(old, make_profile())

    new = make_scan(_deps(("numpy", ">=1.26"), ("torch", ">=2.3")))
    assert "numpy2_torch_abi_break" not in _rule_ids(new, make_profile())

    unpinned = make_scan(_deps(("numpy", ">=1.26"), ("torch", None)))
    assert "numpy2_torch_abi_break" not in _rule_ids(unpinned, make_profile())


def test_onnxruntime_cudnn_mismatch_rule() -> None:
    old_torch = make_scan(_deps(("onnxruntime-gpu", ">=1.19"), ("torch", "==2.3.1")))
    assert "onnxruntime_cudnn_major_mismatch" in _rule_ids(old_torch, make_profile())

    new_torch = make_scan(_deps(("onnxruntime-gpu", ">=1.19"), ("torch", ">=2.4")))
    assert "onnxruntime_cudnn_major_mismatch" not in _rule_ids(new_torch, make_profile())


def test_every_rule_has_a_source() -> None:
    from envdoctor.compat.knowledge import load_rules

    for rule in load_rules():
        source = rule.get("source") or {}
        assert source.get("url"), f"规则 {rule['id']} 缺少来源链接"
        assert rule.get("fix"), f"规则 {rule['id']} 缺少修复建议"
        assert rule.get("message"), f"规则 {rule['id']} 缺少说明文字"


# --- matrix coverage ----------------------------------------------------------


def test_matrix_lookup_is_consistent_with_source_table() -> None:
    matrix = load_matrix()
    for cuda, windows, linux in (
        ("11.8", "520.06", "520.61.05"),
        ("12.0", "527.41", "525.60.13"),
        ("12.1", "531.14", "530.30.02"),
        ("12.2", "536.25", "535.54.03"),
        ("12.3", "545.84", "545.23.06"),
        ("12.4", "551.61", "550.54.14"),
        ("12.5", "555.85", "555.42.02"),
        ("12.6", "560.76", "560.28.03"),
        ("12.8", "570.65", "570.26"),
        ("12.9", "576.02", "575.51.03"),
    ):
        assert matrix.min_driver(cuda, "windows") == windows
        assert matrix.min_driver(cuda, "linux") == linux


def test_checkpoints_still_decide_overall_after_new_verdicts() -> None:
    """新判定不能破坏既有 overall 契约。"""
    scan = make_scan(_deps(("onnxruntime-gpu", ">=1.27")))
    profile = make_profile(gpu_present=True, driver="551.61", driver_max_cuda="12.4")
    from envdoctor.compat.engine import decide_overall

    verdicts = analyze(scan, profile)
    passes = [
        Checkpoint(id="venv", title="venv", state=CheckpointState.PASS),
        Checkpoint(id="install", title="install", state=CheckpointState.PASS),
    ]
    assert decide_overall(verdicts, passes)[0] == Overall.CANNOT_RUN  # blocked 优先
    assert decide_overall(verdicts, [])[0] == Overall.CANNOT_RUN
