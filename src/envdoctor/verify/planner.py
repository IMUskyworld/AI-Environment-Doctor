"""Verification planning (DEVELOPMENT.md §12): interpreter choice, index, requirements."""

from __future__ import annotations

from dataclasses import dataclass, field

from packaging.specifiers import InvalidSpecifier, SpecifierSet

from envdoctor.config import EnvDoctorConfig
from envdoctor.domain.enums import MlRole
from envdoctor.domain.models import Dependency, Interpreter, MachineProfile, ScanResult

IMPORT_MODULES: dict[str, str] = {
    "torch": "torch",
    "torchvision": "torchvision",
    "torchaudio": "torchaudio",
    "tensorflow": "tensorflow",
    "tensorflow-gpu": "tensorflow",
    "tensorflow-cpu": "tensorflow",
    "keras": "keras",
    "jax": "jax",
    "jaxlib": "jaxlib",
    "paddlepaddle": "paddle",
    "paddlepaddle-gpu": "paddle",
    "mxnet": "mxnet",
    "mindspore": "mindspore",
    "xformers": "xformers",
    "flash-attn": "flash_attn",
    "bitsandbytes": "bitsandbytes",
    "deepspeed": "deepspeed",
    "triton": "triton",
    "apex": "apex",
    "transformer-engine": "transformer_engine",
    "scikit-learn": "sklearn",
    "transformers": "transformers",
    "accelerate": "accelerate",
    "peft": "peft",
    "datasets": "datasets",
    "diffusers": "diffusers",
    "sentence-transformers": "sentence_transformers",
    "timm": "timm",
    "safetensors": "safetensors",
    "tokenizers": "tokenizers",
    "sentencepiece": "sentencepiece",
    "opencv-python": "cv2",
    "nmslib": "nmslib",
    "faiss-cpu": "faiss",
    "faiss-gpu": "faiss",
    "openai-whisper": "whisper",
    "faster-whisper": "faster_whisper",
    "fairscale": "fairscale",
    "llama-cpp-python": "llama_cpp",
}

VERIFIABLE_ROLES = {MlRole.CORE, MlRole.EXTENSION, MlRole.LIBRARY}


@dataclass
class VerifyOptions:
    python_version: str | None = None
    index_url: str | None = None
    extra_index_urls: list[str] = field(default_factory=list)
    timeout_s: float = 600.0
    keep_env: bool = False
    smoke_cmds: list[str] = field(default_factory=list)


@dataclass
class VerifyPlan:
    interpreter: Interpreter | None
    interpreter_reason: str
    primary_index: str | None
    extra_indexes: list[str]
    requirement_lines: list[str]
    requirements_source: str
    skipped_groups: list[str]
    smoke_cmds: list[str]
    keep_env: bool
    timeout_s: float
    ml_modules: list[tuple[str, str]]
    warnings: list[str]

    @property
    def installable(self) -> bool:
        return bool(self.requirement_lines)


def _version_tuple(value: str) -> tuple[int, int, int]:
    parts: list[int] = []
    for piece in value.split(".")[:3]:
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return parts[0], parts[1], parts[2]


def choose_interpreter(
    scan: ScanResult,
    probe: MachineProfile,
    requested: str | None,
) -> tuple[Interpreter | None, str]:
    candidates = probe.interpreters
    available = ", ".join(item.version for item in candidates) or "（无）"
    if requested:
        for item in candidates:
            if item.version.startswith(requested):
                return item, f"--python {requested} 命中 {item.version}（{item.path}）"
        return None, f"未找到版本为 {requested} 的解释器；已发现：{available}"
    requires = scan.python.requires
    if requires:
        try:
            spec = SpecifierSet(requires)
        except InvalidSpecifier:
            spec = None
        if spec is not None:
            matching = [item for item in candidates if spec.contains(item.version, prereleases=True)]
            if not matching:
                return (
                    None,
                    f"没有满足 requires-python「{requires}」的解释器；已发现：{available}（可用 --python 显式指定）",
                )
            best = max(matching, key=lambda item: _version_tuple(item.version))
            return best, f"自动选择满足「{requires}」的最高版本 {best.version}"
    if not candidates:
        return None, "未发现任何 Python 解释器"
    best = max(candidates, key=lambda item: _version_tuple(item.version))
    return best, f"项目未声明 Python 约束，选择最高可用版本 {best.version}"


def requirement_lines_from_deps(dependencies: list[Dependency]) -> tuple[list[str], list[str]]:
    """Merge default-group deps into pip requirement lines; report skipped groups."""
    chosen: dict[str, Dependency] = {}
    skipped_groups: set[str] = set()
    for dep in dependencies:
        if dep.group == "conda":
            continue
        if dep.group != "default":
            skipped_groups.add(dep.group)
            continue
        key = dep.name.lower()
        current = chosen.get(key)
        if current is None or (current.specifier is None and dep.specifier is not None):
            chosen[key] = dep
    lines: list[str] = []
    for dep in sorted(chosen.values(), key=lambda item: item.name.lower()):
        line = dep.name
        if dep.extras:
            line += f"[{','.join(dep.extras)}]"
        if dep.specifier:
            line += dep.specifier
        if dep.marker:
            line += f" ; {dep.marker}"
        lines.append(line)
    return lines, sorted(skipped_groups)


def build_plan(
    scan: ScanResult,
    probe: MachineProfile,
    options: VerifyOptions,
    config: EnvDoctorConfig,
) -> VerifyPlan:
    interpreter, interpreter_reason = choose_interpreter(scan, probe, options.python_version)

    project_indexes = [url for dependency_file in scan.dependency_files for url in dependency_file.index_urls]
    ordered_indexes: list[str] = []
    for url in (
        ([options.index_url] if options.index_url else [])
        + project_indexes
        + ([config.defaults.index_url] if config.defaults.index_url else [])
    ):
        if url and url not in ordered_indexes:
            ordered_indexes.append(url)
    primary_index = ordered_indexes[0] if ordered_indexes else None
    extra_indexes = ordered_indexes[1:]
    for url in options.extra_index_urls:
        if url and url not in ordered_indexes and url != primary_index:
            extra_indexes.append(url)

    requirement_lines, skipped_groups = requirement_lines_from_deps(scan.dependencies)
    kinds = sorted({dependency_file.kind for dependency_file in scan.dependency_files})
    requirements_source = "、".join(kinds) if kinds else "（无依赖声明）"

    ml_modules: list[tuple[str, str]] = []
    tooling_excluded: list[str] = []
    no_stable_import: list[str] = []
    for item in scan.ml_stack:
        if item.role in VERIFIABLE_ROLES and item.name in IMPORT_MODULES:
            ml_modules.append((item.name, IMPORT_MODULES[item.name]))
        elif item.role not in VERIFIABLE_ROLES:
            tooling_excluded.append(item.name)
        else:
            no_stable_import.append(item.name)

    warnings: list[str] = []
    if skipped_groups:
        warnings.append(f"仅安装默认依赖组；以下依赖组未纳入验证：{', '.join(skipped_groups)}")
    if tooling_excluded:
        warnings.append(f"工具类依赖不参与 import 冒烟（无稳定导入名）：{', '.join(tooling_excluded)}")
    if no_stable_import:
        warnings.append(f"以下依赖不参与 import 冒烟（导入代价高或依赖 GPU 初始化）：{', '.join(no_stable_import)}")

    return VerifyPlan(
        interpreter=interpreter,
        interpreter_reason=interpreter_reason,
        primary_index=primary_index,
        extra_indexes=extra_indexes,
        requirement_lines=requirement_lines,
        requirements_source=requirements_source,
        skipped_groups=skipped_groups,
        smoke_cmds=list(options.smoke_cmds),
        keep_env=options.keep_env,
        timeout_s=options.timeout_s,
        ml_modules=ml_modules,
        warnings=warnings,
    )
