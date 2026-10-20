"""Compatibility analysis engine (DEVELOPMENT.md §11).

Deterministic, evidence-backed verdicts: built-in checks for the facts every
project has (Python / GPU / driver), plus a small source-backed rule library for
ecosystem pitfalls. Missing data yields ``unknown``, never a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from envdoctor.compat.knowledge import (
    CudaMatrix,
    load_matrix,
    load_package_cuda_requirements,
    load_rules,
    platform_family,
)
from envdoctor.domain.enums import (
    Aspect,
    CheckpointState,
    Confidence,
    Overall,
    RequirementStrength,
    Severity,
    VerdictState,
)
from envdoctor.domain.models import Checkpoint, Dependency, Evidence, Fix, MachineProfile, ScanResult, Verdict

REQUIRED_CHECKPOINTS = {"venv", "install"}

SEVERITY_TO_STATE: dict[Severity, VerdictState] = {
    Severity.BLOCKER: VerdictState.BLOCKED,
    Severity.WARNING: VerdictState.WARN,
    Severity.INFO: VerdictState.WARN,
}

_PY_VERSION_RE = re.compile(r"(\d+\.\d+)")


@dataclass
class AnalysisContext:
    dependencies: list[Dependency]
    platform: str
    gpu_present: bool
    driver_version: str | None
    requested_cuda: str | None
    interpreter_versions: list[str]
    file_names: set[str]

    def has_lib(self, name: str) -> bool:
        lowered = name.lower()
        return any(dep.name.lower() == lowered for dep in self.dependencies)

    def specifiers_for(self, name: str) -> list[str]:
        lowered = name.lower()
        return [dep.specifier for dep in self.dependencies if dep.name.lower() == lowered and dep.specifier]

    def may_install_at_or_above(self, name: str, version: str) -> bool:
        """Could the resolver pick a version >= ``version``? Unpinned ⇒ yes."""
        return _may_install_at_or_above(self.specifiers_for(name), version)

    def requires_at_least(self, name: str, version: str) -> bool:
        """Is a version >= ``version`` guaranteed by the declared specifiers?"""
        return _requires_at_least(self.specifiers_for(name), version)

    def pinned_below(self, name: str, version: str) -> bool:
        """Is a version < ``version`` guaranteed by the declared specifiers?"""
        return _pinned_below(self.specifiers_for(name), version)


def build_context(scan: ScanResult, probe: MachineProfile) -> AnalysisContext:
    versions: list[str] = []
    for item in probe.interpreters:
        if item.version not in versions:
            versions.append(item.version)
    if probe.python.version not in versions:
        versions.append(probe.python.version)
    return AnalysisContext(
        dependencies=scan.dependencies,
        platform=probe.os.name,
        gpu_present=probe.gpu.present,
        driver_version=probe.gpu.driver_version,
        requested_cuda=scan.cuda.requested_runtime,
        interpreter_versions=versions,
        file_names={item.path for item in scan.dependency_files},
    )


# --- version helpers ----------------------------------------------------------


def _version_ge(left: str, right: str) -> bool:
    try:
        return Version(left) >= Version(right)
    except InvalidVersion:
        return False


def _cuda_tuple(value: str) -> tuple[int, int]:
    parts = value.split(".")
    major = int(parts[0]) if parts and parts[0].isdigit() else 0
    minor = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    return major, minor


def _cuda_ge(left: str, right: str) -> bool:
    return _cuda_tuple(left) >= _cuda_tuple(right)


def _specifier_blocks_below(spec: Any, threshold: Version) -> bool:
    """True when this single specifier guarantees versions >= threshold."""
    if spec.operator in {">=", ">"}:
        try:
            return Version(spec.version) >= threshold
        except InvalidVersion:
            return False
    if spec.operator == "==" and ".*" not in spec.version:
        try:
            return Version(spec.version) >= threshold
        except InvalidVersion:
            return False
    return False


def _specifier_blocks_at_or_above(spec: Any, threshold: Version) -> bool:
    """True when this single specifier guarantees versions < threshold."""
    try:
        version = Version(spec.version.rstrip(".*"))
    except InvalidVersion:
        return False
    if spec.operator in {"<=", "<"}:
        return version <= threshold
    if spec.operator == "==" and ".*" not in spec.version:
        return version < threshold
    return False


def _may_install_below(specifiers: list[str], threshold: str) -> bool:
    if not specifiers:
        return True
    threshold_version = Version(threshold)
    for specifier in specifiers:
        try:
            spec_set = SpecifierSet(specifier)
        except InvalidSpecifier:
            return True
        if not any(_specifier_blocks_below(spec, threshold_version) for spec in spec_set):
            return True
    return False


def _may_install_at_or_above(specifiers: list[str], threshold: str) -> bool:
    if not specifiers:
        return True
    threshold_version = Version(threshold)
    for specifier in specifiers:
        try:
            spec_set = SpecifierSet(specifier)
        except InvalidSpecifier:
            return True
        if not any(_specifier_blocks_at_or_above(spec, threshold_version) for spec in spec_set):
            return True
    return False


def _pinned_below(specifiers: list[str], threshold: str) -> bool:
    """True when the specifiers *guarantee* a version strictly below the threshold.

    An unpinned dependency is **not** pinned below anything: it may resolve to
    any version, so this stays ``False`` (the opposite of
    :func:`_may_install_below`, which is the "unknown → assume risky" reading).
    """
    if not specifiers:
        return False
    threshold_version = Version(threshold)
    for specifier in specifiers:
        try:
            spec_set = SpecifierSet(specifier)
        except InvalidSpecifier:
            return False
        if not any(_specifier_blocks_at_or_above(spec, threshold_version) for spec in spec_set):
            return False
    return True


def _requires_at_least(specifiers: list[str], threshold: str) -> bool:
    """True when the specifiers *guarantee* a version at or above the threshold."""
    if not specifiers:
        return False
    threshold_version = Version(threshold)
    for specifier in specifiers:
        try:
            spec_set = SpecifierSet(specifier)
        except InvalidSpecifier:
            return False
        if not any(_specifier_blocks_below(spec, threshold_version) for spec in spec_set):
            return False
    return True


def _suggest_python(requires: str) -> str:
    if match := _PY_VERSION_RE.search(requires):
        return match.group(1)
    return "3.12"


def _platform_matches(actual: str, expected: str) -> bool:
    return platform_family(actual) == platform_family(expected)


# --- condition DSL ------------------------------------------------------------


def evaluate_condition(condition: dict[str, Any], ctx: AnalysisContext) -> bool:
    if "all" in condition:
        return all(evaluate_condition(item, ctx) for item in condition["all"])
    if "any" in condition:
        return any(evaluate_condition(item, ctx) for item in condition["any"])
    if "not" in condition:
        return not evaluate_condition(condition["not"], ctx)
    kind = condition.get("kind")
    if kind == "lib_present":
        return ctx.has_lib(str(condition["name"]))
    if kind == "lib_may_install_below":
        return _may_install_below(ctx.specifiers_for(str(condition["name"])), str(condition["version"]))
    if kind == "lib_may_install_at_or_above":
        return _may_install_at_or_above(ctx.specifiers_for(str(condition["name"])), str(condition["version"]))
    if kind == "lib_pinned_below":
        return _pinned_below(ctx.specifiers_for(str(condition["name"])), str(condition["version"]))
    if kind == "lib_requires_at_least":
        return _requires_at_least(ctx.specifiers_for(str(condition["name"])), str(condition["version"]))
    if kind == "os":
        return _platform_matches(ctx.platform, str(condition["name"]))
    if kind == "gpu_present":
        return ctx.gpu_present is bool(condition["value"])
    if kind == "driver_lt":
        return not _version_ge(ctx.driver_version or "0", str(condition["version"]))
    if kind == "cuda_runtime_ge":
        return ctx.requested_cuda is not None and _cuda_ge(ctx.requested_cuda, str(condition["version"]))
    if kind == "python_available_lt":
        threshold = Version(str(condition["version"]))
        return all(Version(v) < threshold for v in ctx.interpreter_versions)
    if kind == "file_present":
        return str(condition["name"]) in ctx.file_names
    return False  # unknown condition kinds never fire (fail closed)


# --- built-in verdicts --------------------------------------------------------


def python_verdict(scan: ScanResult, probe: MachineProfile, ctx: AnalysisContext, matrix: CudaMatrix) -> list[Verdict]:
    del probe, matrix
    requires = scan.python.requires
    if not requires:
        return [
            Verdict(
                id="python_available",
                aspect=Aspect.PYTHON,
                state=VerdictState.NOT_APPLICABLE,
                title="未声明 Python 版本要求",
                summary="项目未声明 requires-python，跳过 Python 版本匹配",
                evidence=[
                    Evidence(
                        kind="scan", detail="未发现 requires-python / python_requires / environment.yml python 等约束"
                    )
                ],
            )
        ]
    try:
        spec = SpecifierSet(requires)
    except InvalidSpecifier:
        return [
            Verdict(
                id="python_available",
                aspect=Aspect.PYTHON,
                state=VerdictState.UNKNOWN,
                title="无法解析 Python 约束",
                summary=f"requires-python「{requires}」不是有效的 PEP 440 说明符",
                evidence=[Evidence(kind="scan", detail=f"{scan.python.source}: {requires}")],
                confidence=Confidence.MEDIUM,
            )
        ]
    matching = [version for version in ctx.interpreter_versions if spec.contains(version, prereleases=True)]
    evidence = [
        Evidence(kind="scan", detail=f"{scan.python.source}: {requires}"),
        Evidence(kind="probe", detail=f"已发现解释器：{', '.join(ctx.interpreter_versions) or '（无）'}"),
    ]
    if matching:
        return [
            Verdict(
                id="python_available",
                aspect=Aspect.PYTHON,
                state=VerdictState.OK,
                title="Python 版本满足要求",
                summary=f"要求 {requires}，可用：{', '.join(matching)}",
                evidence=evidence,
            )
        ]
    suggestion = _suggest_python(requires)
    return [
        Verdict(
            id="python_available",
            aspect=Aspect.PYTHON,
            state=VerdictState.BLOCKED,
            title="没有满足要求的 Python 解释器",
            summary=f"要求 {requires}，已发现：{', '.join(ctx.interpreter_versions)}",
            evidence=evidence,
            fix=Fix(
                kind="command",
                text=f"安装满足要求的解释器（例如 Python {suggestion}）后重跑",
                command=f"uv python install {suggestion}",
            ),
        )
    ]


def gpu_presence_verdict(
    scan: ScanResult, probe: MachineProfile, ctx: AnalysisContext, matrix: CudaMatrix
) -> list[Verdict]:
    del ctx, matrix
    strength = scan.cuda.gpu_required_strength
    if strength == RequirementStrength.NONE:
        return []
    evidence = [Evidence(kind="scan", detail=f"GPU 需求强度：{strength.value}")]
    evidence.extend(Evidence(kind="scan", detail=item) for item in scan.cuda.evidence[:3])
    gpu = probe.gpu
    if gpu.present:
        names = ", ".join(device.name for device in gpu.devices) or "（设备信息缺失）"
        evidence.append(Evidence(kind="probe", detail=f"来源：{gpu.source.value}；驱动 {gpu.driver_version or '未知'}"))
        return [
            Verdict(
                id="gpu_presence",
                aspect=Aspect.GPU,
                state=VerdictState.OK,
                title="检测到 NVIDIA GPU",
                summary=names,
                evidence=evidence,
            )
        ]
    evidence.append(Evidence(kind="probe", detail=f"未检测到 GPU：{gpu.reason or '原因未知'}"))
    if strength == RequirementStrength.HARD:
        return [
            Verdict(
                id="gpu_presence",
                aspect=Aspect.GPU,
                state=VerdictState.BLOCKED,
                title="项目需要 GPU，但本机未检测到 NVIDIA GPU",
                summary="依赖包含 GPU 专属组件（如 xformers/flash-attn/nvidia-*/+cuXXX），CPU 无法替代",
                evidence=evidence,
                fix=Fix(kind="instruction", text="改用带 NVIDIA GPU 与驱动的机器；或移除 GPU 专属依赖后重试"),
            )
        ]
    return [
        Verdict(
            id="gpu_presence",
            aspect=Aspect.GPU,
            state=VerdictState.WARN,
            title="未检测到 NVIDIA GPU，将回退 CPU",
            summary="框架可以在 CPU 上运行，但显存加速不可用",
            evidence=evidence,
            fix=Fix(kind="instruction", text="如需 GPU 加速：安装 NVIDIA 驱动并确认 nvidia-smi 可用"),
        )
    ]


def driver_vs_cuda_verdict(
    scan: ScanResult, probe: MachineProfile, ctx: AnalysisContext, matrix: CudaMatrix
) -> list[Verdict]:
    del ctx
    runtime = scan.cuda.requested_runtime
    if not (probe.gpu.present and runtime):
        return []
    platform = probe.os.name
    major_only = "." not in runtime
    evidence = [Evidence(kind="scan", detail=f"所需 CUDA runtime：{runtime}")]
    evidence.extend(Evidence(kind="scan", detail=item) for item in scan.cuda.evidence[:3])
    min_driver = matrix.min_driver(runtime, platform)
    if min_driver is None:
        evidence.append(
            Evidence(
                kind="matrix",
                detail=f"矩阵覆盖版本：{'、'.join(matrix.covered_versions())}",
                source="compat/data/cuda_driver_matrix.json",
            )
        )
        return [
            Verdict(
                id="driver_vs_cuda",
                aspect=Aspect.CUDA,
                state=VerdictState.UNKNOWN,
                title=f"知识库暂无 CUDA {runtime} 的驱动要求",
                summary="无法静态判定驱动是否满足该 CUDA 版本；实测阶段会实际安装验证",
                evidence=evidence,
                fix=Fix(kind="instruction", text="查阅 NVIDIA 官方最低驱动表，或直接运行实测验证"),
                confidence=Confidence.MEDIUM,
            )
        ]
    evidence.append(
        Evidence(
            kind="matrix",
            detail=f"CUDA {runtime}（{platform_family(platform)}）最低驱动 {min_driver}",
            source="compat/data/cuda_driver_matrix.json",
        )
    )
    driver = probe.gpu.driver_version
    if not driver:
        return [
            Verdict(
                id="driver_vs_cuda",
                aspect=Aspect.CUDA,
                state=VerdictState.UNKNOWN,
                title="无法读取驱动版本",
                summary=f"CUDA {runtime} 需要驱动 ≥ {min_driver}，但未取到当前驱动版本",
                evidence=evidence,
                confidence=Confidence.MEDIUM,
            )
        ]
    driver_max_cuda = probe.gpu.driver_max_cuda
    evidence.append(
        Evidence(kind="probe", detail=f"当前驱动 {driver}；驱动上报的 CUDA 上限 {driver_max_cuda or '未知'}")
    )
    driver_ok = _version_ge(driver, min_driver)
    runtime_ok = driver_max_cuda is None or _cuda_ge(driver_max_cuda, runtime)
    note = "（项目仅声明主版本，按该系列最低要求保守比较）" if major_only else ""
    confidence = Confidence.LOW if major_only else Confidence.HIGH
    if driver_ok and runtime_ok:
        return [
            Verdict(
                id="driver_vs_cuda",
                aspect=Aspect.CUDA,
                state=VerdictState.OK,
                title=f"驱动满足 CUDA {runtime} 要求",
                summary=f"驱动 {driver} ≥ 最低要求 {min_driver}{note}",
                evidence=evidence,
                confidence=confidence,
            )
        ]
    return [
        Verdict(
            id="driver_vs_cuda",
            aspect=Aspect.CUDA,
            state=VerdictState.BLOCKED,
            title=f"驱动版本过低，无法支持 CUDA {runtime}",
            summary=f"需要 ≥ {min_driver}，当前 {driver}"
            + (f"（驱动上报上限 CUDA {driver_max_cuda}）" if driver_max_cuda else "")
            + note,
            evidence=evidence,
            fix=Fix(kind="instruction", text=f"升级 NVIDIA 驱动到 ≥ {min_driver}（CUDA {runtime} 最低要求），然后重跑"),
            confidence=confidence,
        )
    ]


def cuda_index_verdict(
    scan: ScanResult, probe: MachineProfile, ctx: AnalysisContext, matrix: CudaMatrix
) -> list[Verdict]:
    del probe, ctx, matrix
    local_versions = [dep for dep in scan.dependencies if dep.specifier and "+cu" in dep.specifier]
    if not local_versions:
        return []
    torch_indexes = [
        url
        for dependency_file in scan.dependency_files
        for url in dependency_file.index_urls
        if "download.pytorch.org" in url
    ]
    names = ", ".join(f"{dep.name}{dep.specifier}" for dep in local_versions[:3])
    if torch_indexes:
        return [
            Verdict(
                id="cuda_index_configured",
                aspect=Aspect.DEPS,
                state=VerdictState.OK,
                title="CUDA wheel 源已配置",
                summary=f"已声明 PyTorch 官方源：{torch_indexes[0]}",
                evidence=[Evidence(kind="scan", detail=f"{names} 需要 PyTorch 官方源；已发现 {torch_indexes[0]}")],
            )
        ]
    match = re.search(r"\+cu(\d+)", local_versions[0].specifier or "")
    token = f"cu{match.group(1)}" if match else "cu121"
    return [
        Verdict(
            id="cuda_index_missing",
            aspect=Aspect.DEPS,
            state=VerdictState.WARN,
            title="使用 +cuXXX 本地版本但未配置 PyTorch wheel 源",
            summary=f"{names}：PyPI 不提供 +cuXXX wheel，直接安装会解析失败",
            evidence=[
                Evidence(
                    kind="scan",
                    detail="本地版本号（+cuXXX）只在 download.pytorch.org 的 wheel 索引中存在",
                    source="PyTorch 官方安装说明",
                )
            ],
            fix=Fix(
                kind="command",
                text="给安装命令或 requirements 增加 PyTorch 官方源",
                command=f"pip install --index-url https://download.pytorch.org/whl/{token} ...",
            ),
        )
    ]


def torch_cpu_wheel_verdict(
    scan: ScanResult, probe: MachineProfile, ctx: AnalysisContext, matrix: CudaMatrix
) -> list[Verdict]:
    del matrix
    if not probe.gpu.present or not ctx.has_lib("torch") or scan.cuda.requested_runtime:
        return []
    return [
        Verdict(
            id="torch_cpu_wheel_risk",
            aspect=Aspect.DEPS,
            state=VerdictState.WARN,
            title="未声明 CUDA 版本，torch wheel 变体未知",
            summary="解析出的 wheel 可能是 CPU 版（取决于安装源）；实测阶段会打印 torch.version.cuda 确认",
            evidence=[
                Evidence(kind="scan", detail="依赖含 torch 但没有任何 CUDA 线索（+cuXXX / torch 官方源 / nvidia-* 包）")
            ],
            fix=Fix(kind="instruction", text="如需确保 CUDA 版：显式使用 PyTorch 官方 cuXXX 源或固定 +cuXXX 版本"),
            confidence=Confidence.MEDIUM,
        )
    ]


def conda_env_verdict(
    scan: ScanResult, probe: MachineProfile, ctx: AnalysisContext, matrix: CudaMatrix
) -> list[Verdict]:
    del probe, matrix
    if not ({"environment.yml", "environment.yaml"} & ctx.file_names):
        return []
    return [
        Verdict(
            id="conda_env_present",
            aspect=Aspect.PLATFORM,
            state=VerdictState.WARN,
            title="检测到 conda environment.yml",
            summary="envdoctor 只解析它；验证走 pip/uv 子集，不是 conda 精确复现",
            evidence=[Evidence(kind="scan", detail="environment.yml 的 conda 依赖与 pip 子段已并入依赖列表")],
            fix=Fix(
                kind="instruction",
                text="需要 conda 精确复现时请手动执行 conda env create；envdoctor 的结论基于 pip 安装路径",
            ),
            confidence=Confidence.HIGH,
        )
    ]


def rule_verdicts(ctx: AnalysisContext) -> list[Verdict]:
    verdicts: list[Verdict] = []
    for rule in load_rules():
        platforms = [str(item) for item in rule.get("platforms", [])]
        if platforms and not any(_platform_matches(ctx.platform, item) for item in platforms):
            continue
        if not evaluate_condition(rule["when"], ctx):
            continue
        source = rule.get("source") or {}
        severity = Severity(str(rule.get("severity", "warning")))
        verdicts.append(
            Verdict(
                id=str(rule["id"]),
                aspect=Aspect(str(rule.get("aspect", "ecosystem"))),
                state=SEVERITY_TO_STATE[severity],
                title=str(rule["title"]),
                summary=str(rule["message"]),
                evidence=[
                    Evidence(
                        kind="rule",
                        detail=f"规则 {rule['id']} 命中",
                        source=str(source.get("url") or source.get("name") or ""),
                    )
                ],
                fix=Fix(kind="instruction", text=str(rule["fix"])) if rule.get("fix") else None,
                rule_id=str(rule["id"]),
                confidence=Confidence(str(rule.get("confidence", "medium"))),
            )
        )
    return verdicts


def package_cuda_requirement_verdict(
    scan: ScanResult, probe: MachineProfile, ctx: AnalysisContext, matrix: CudaMatrix
) -> list[Verdict]:
    """Packages whose own build pins a CUDA runtime (onnxruntime-gpu & friends).

    The project's requirements rarely mention CUDA for these packages, so the
    real driver requirement is invisible to a plain scan. The knowledge base
    (``package_cuda_requirements.json``) carries the upstream version → CUDA
    mapping, and the verdict cross-checks it against the probed driver.
    """
    del scan
    verdicts: list[Verdict] = []
    platform = probe.os.name
    entries = load_package_cuda_requirements()
    for name in dict.fromkeys(entry.name for entry in entries):
        if not ctx.has_lib(name):
            continue
        candidates = [entry for entry in entries if entry.name == name]
        applicable = [entry for entry in candidates if ctx.may_install_at_or_above(name, entry.min_version)]
        if not applicable:
            continue
        # 同一包的多个条目取最高版本：pip 会解析到最新版（要求只会更高，不会更低）
        entry = max(applicable, key=lambda item: Version(item.min_version))
        certain = ctx.requires_at_least(name, entry.min_version)
        min_driver = matrix.min_driver(entry.cuda, platform)
        evidence = [
            Evidence(
                kind="scan",
                detail=(
                    f"{entry.name} {'≥' if certain else '可能'} {entry.min_version}"
                    f"（{entry.note or '上游文档'}）→ 需要 CUDA {entry.cuda}"
                ),
                source=entry.source_url or None,
            )
        ]
        if min_driver is None:
            evidence.append(
                Evidence(
                    kind="matrix",
                    detail=f"知识库暂无 CUDA {entry.cuda} 的驱动要求",
                    source="compat/data/cuda_driver_matrix.json",
                )
            )
            verdicts.append(
                Verdict(
                    id=f"package_cuda_{entry.name}",
                    aspect=Aspect.CUDA,
                    state=VerdictState.UNKNOWN,
                    title=f"{entry.name} 需要 CUDA {entry.cuda}，但矩阵未覆盖该版本",
                    summary="无法静态判定驱动是否满足；实测阶段会实际安装验证",
                    evidence=evidence,
                    confidence=Confidence.MEDIUM,
                )
            )
            continue
        evidence.append(
            Evidence(
                kind="matrix",
                detail=f"CUDA {entry.cuda}（{platform_family(platform)}）最低驱动 {min_driver}",
                source="compat/data/cuda_driver_matrix.json",
            )
        )
        driver = probe.gpu.driver_version
        if not driver:
            verdicts.append(
                Verdict(
                    id=f"package_cuda_{entry.name}",
                    aspect=Aspect.CUDA,
                    state=VerdictState.UNKNOWN,
                    title=f"无法确认 {entry.name} 的驱动要求是否满足",
                    summary=f"{entry.name} 需要 CUDA {entry.cuda}（驱动 ≥ {min_driver}），但未取到当前驱动版本",
                    evidence=evidence,
                    confidence=Confidence.MEDIUM,
                )
            )
            continue
        driver_ok = _version_ge(driver, min_driver)
        if driver_ok:
            verdicts.append(
                Verdict(
                    id=f"package_cuda_{entry.name}",
                    aspect=Aspect.CUDA,
                    state=VerdictState.OK,
                    title=f"{entry.name} 的 CUDA {entry.cuda} 要求已满足",
                    summary=f"驱动 {driver} ≥ 最低要求 {min_driver}",
                    evidence=evidence,
                )
            )
            continue
        detail = (
            f"{entry.name} {'必须' if certain else '可能'}解析到 ≥{entry.min_version} 的版本"
            f"（用 CUDA {entry.cuda} 构建），需要驱动 ≥ {min_driver}，当前驱动 {driver}"
        )
        if certain:
            verdicts.append(
                Verdict(
                    id=f"package_cuda_{entry.name}",
                    aspect=Aspect.CUDA,
                    state=VerdictState.BLOCKED,
                    title=f"{entry.name} 要求的 CUDA {entry.cuda} 超出当前驱动能力",
                    summary=detail,
                    evidence=evidence,
                    fix=Fix(
                        kind="instruction",
                        text=(
                            f"升级 NVIDIA 驱动到 ≥ {min_driver}（CUDA {entry.cuda} 最低要求），"
                            f"或固定 {entry.name} 到更低版本"
                        ),
                    ),
                )
            )
        else:
            verdicts.append(
                Verdict(
                    id=f"package_cuda_{entry.name}",
                    aspect=Aspect.CUDA,
                    state=VerdictState.WARN,
                    title=f"{entry.name} 可能解析到需要 CUDA {entry.cuda} 的版本",
                    summary=detail + "（未固定版本时按最新版估算）",
                    evidence=evidence,
                    fix=Fix(
                        kind="instruction",
                        text=(
                            f"固定 {entry.name} 到与当前驱动匹配的版本，"
                            f"或升级 NVIDIA 驱动到 ≥ {min_driver}（CUDA {entry.cuda} 最低要求）"
                        ),
                    ),
                    confidence=Confidence.MEDIUM,
                )
            )
    return verdicts


BUILTIN_CHECKS = (
    python_verdict,
    gpu_presence_verdict,
    driver_vs_cuda_verdict,
    cuda_index_verdict,
    torch_cpu_wheel_verdict,
    conda_env_verdict,
    package_cuda_requirement_verdict,
)


def analyze(scan: ScanResult, probe: MachineProfile) -> list[Verdict]:
    matrix = load_matrix()
    ctx = build_context(scan, probe)
    verdicts: list[Verdict] = []
    for check in BUILTIN_CHECKS:
        verdicts.extend(check(scan, probe, ctx, matrix))
    verdicts.extend(rule_verdicts(ctx))
    return verdicts


def decide_overall(
    verdicts: list[Verdict],
    checkpoints: list[Checkpoint],
    *,
    verify_note: str | None = None,
) -> tuple[Overall, str]:
    blocked = [verdict for verdict in verdicts if verdict.state == VerdictState.BLOCKED]
    if blocked:
        return Overall.CANNOT_RUN, f"存在阻断性判定：{blocked[0].title}"
    warn_count = sum(1 for verdict in verdicts if verdict.state == VerdictState.WARN)
    if checkpoints:
        failed = [checkpoint for checkpoint in checkpoints if checkpoint.state == CheckpointState.FAIL]
        if failed:
            return Overall.CANNOT_RUN, f"实测检查点失败：{failed[0].title}"
        blocking = [
            checkpoint
            for checkpoint in checkpoints
            if checkpoint.id in REQUIRED_CHECKPOINTS
            and checkpoint.state in {CheckpointState.SKIP, CheckpointState.ERROR}
        ]
        if blocking:
            return Overall.UNKNOWN, f"关键检查点未完成：{blocking[0].title}"
        reason = "全部必需检查点通过"
        if warn_count:
            reason += f"；{warn_count} 条注意事项"
        return Overall.RUNS, reason
    if verify_note:
        return Overall.UNKNOWN, verify_note
    return Overall.UNKNOWN, "未执行真实验证；静态分析只能给出可能性，不能证明可运行"
