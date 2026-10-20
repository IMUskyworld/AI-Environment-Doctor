"""Project scanner (DEVELOPMENT.md §9): target resolution + ecosystem detection."""

from __future__ import annotations

import hashlib
import re
import shutil
import tomllib
from pathlib import Path

from envdoctor.domain.enums import MlRole, RequirementStrength, TargetKind
from envdoctor.domain.models import (
    CudaHints,
    Dependency,
    DependencyFile,
    MlStackItem,
    Note,
    PythonRequirement,
    ScanResult,
    Target,
)
from envdoctor.errors import (
    GIT_CLONE_FAILED,
    GIT_CLONE_TIMEOUT,
    GIT_UNAVAILABLE,
    GitError,
    TargetError,
)
from envdoctor.paths import WorkLayout
from envdoctor.scan import parsers
from envdoctor.subproc import run_command

ML_ROLES: dict[str, MlRole] = {
    "torch": MlRole.CORE,
    "torchvision": MlRole.CORE,
    "torchaudio": MlRole.CORE,
    "tensorflow": MlRole.CORE,
    "tensorflow-gpu": MlRole.CORE,
    "tensorflow-cpu": MlRole.CORE,
    "jax": MlRole.CORE,
    "jaxlib": MlRole.CORE,
    "keras": MlRole.CORE,
    "paddlepaddle": MlRole.CORE,
    "paddlepaddle-gpu": MlRole.CORE,
    "mxnet": MlRole.CORE,
    "mindspore": MlRole.CORE,
    "xformers": MlRole.EXTENSION,
    "flash-attn": MlRole.EXTENSION,
    "bitsandbytes": MlRole.EXTENSION,
    "deepspeed": MlRole.EXTENSION,
    "triton": MlRole.EXTENSION,
    "apex": MlRole.EXTENSION,
    "transformer-engine": MlRole.EXTENSION,
    "vllm": MlRole.EXTENSION,
    "sglang": MlRole.EXTENSION,
    "megatron-core": MlRole.EXTENSION,
    "scikit-learn": MlRole.LIBRARY,
    "transformers": MlRole.LIBRARY,
    "accelerate": MlRole.LIBRARY,
    "peft": MlRole.LIBRARY,
    "datasets": MlRole.LIBRARY,
    "diffusers": MlRole.LIBRARY,
    "sentence-transformers": MlRole.LIBRARY,
    "timm": MlRole.LIBRARY,
    "fairscale": MlRole.LIBRARY,
    "openai-whisper": MlRole.LIBRARY,
    "faster-whisper": MlRole.LIBRARY,
    "ultralytics": MlRole.LIBRARY,
    "llama-cpp-python": MlRole.LIBRARY,
    "safetensors": MlRole.LIBRARY,
    "tokenizers": MlRole.LIBRARY,
    "sentencepiece": MlRole.LIBRARY,
    "opencv-python": MlRole.LIBRARY,
    "nmslib": MlRole.LIBRARY,
    "faiss-cpu": MlRole.LIBRARY,
    "faiss-gpu": MlRole.LIBRARY,
    "onnxruntime-gpu": MlRole.TOOLING,
    "onnxruntime": MlRole.TOOLING,
    "cupy": MlRole.TOOLING,
    "cupy-cuda12x": MlRole.TOOLING,
    "tensorrt": MlRole.TOOLING,
    "wandb": MlRole.TOOLING,
    "mlflow": MlRole.TOOLING,
    "clearml": MlRole.TOOLING,
    "ray": MlRole.TOOLING,
    "tensorboard": MlRole.TOOLING,
    "gradio": MlRole.TOOLING,
    "streamlit": MlRole.TOOLING,
    "optuna": MlRole.TOOLING,
    "hydra-core": MlRole.TOOLING,
}

HARD_GPU_SIGNALS = {
    "flash-attn",
    "xformers",
    "deepspeed",
    "triton",
    "bitsandbytes",
    "apex",
    "transformer-engine",
    "onnxruntime-gpu",
    "tensorrt",
    "cupy",
    "cupy-cuda12x",
}

ROLE_ORDER = {MlRole.CORE: 0, MlRole.EXTENSION: 1, MlRole.LIBRARY: 2, MlRole.TOOLING: 3}

_CUDA_INDEX_RE = re.compile(r"/whl/(cu\d{2,3})")
_LOCAL_VERSION_RE = re.compile(r"\+cu(\d{2,3})")
_CONDA_CUDA_NAMES = {"pytorch-cuda", "cudatoolkit"}

ROOT_ENTRYPOINT_CANDIDATES = ["train.py", "main.py", "app.py", "run.py", "demo.py", "inference.py", "eval.py"]
MAX_ENTRYPOINTS = 12
MAX_REQUIREMENTS_FILES = 20


def cuda_runtime_from_token(token: str) -> str | None:
    """``cu121`` → ``12.1``, ``cu118`` → ``11.8``, ``cu12`` → ``12`` (major only)."""
    digits = token.lower().removeprefix("cu")
    if not digits.isdigit():
        return None
    if len(digits) == 2:
        return digits
    if len(digits) == 3 and digits[:2] in {"10", "11", "12", "13"}:
        minor = digits[2:]
        if minor.startswith("0") and len(minor) > 1:
            minor = minor.lstrip("0")
        return f"{digits[:2]}.{minor}"
    return None


def looks_like_git_url(value: str) -> bool:
    return value.startswith(("http://", "https://", "git@", "ssh://")) or value.endswith(".git")


_SCP_LIKE_RE = re.compile(r"^[^/\\:@]+@[^/\\:]+:")
_DOMAIN_PATH_RE = re.compile(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}/[^/\s]+/[^/\s]+$")


def remote_candidate(value: str) -> str | None:
    """Turn a shorthand into a cloneable URL, or ``None`` when it cannot be one.

    ``github.com/owner/repo`` → ``https://github.com/owner/repo``;
    ``git@github.com:owner/repo.git`` and ``https://…`` pass through unchanged.
    A bare Windows path (``C:\\code\\x``) is never treated as remote.
    """
    if "://" in value:
        return value
    if _SCP_LIKE_RE.match(value):
        return value
    if _DOMAIN_PATH_RE.match(value):
        return f"https://{value}"
    return None


def resolve_target(
    target: str,
    *,
    work: WorkLayout,
    refresh: bool = False,
    clone_timeout_s: float = 120.0,
) -> Target:
    if looks_like_git_url(target):
        return _clone_target(target, work, refresh=refresh, timeout_s=clone_timeout_s)
    path = Path(target).expanduser()
    if path.is_dir():
        return Target(kind=TargetKind.LOCAL, original=target, path=str(path.resolve()))
    # §9.1：路径不成立时降级为 URL 尝试（github.com/owner/repo 这类简写也能用）
    candidate = remote_candidate(target)
    if candidate is not None:
        try:
            return _clone_target(candidate, work, refresh=refresh, timeout_s=clone_timeout_s)
        except GitError as exc:
            raise TargetError(
                f"目标既不是本地目录也无法作为 git 仓库克隆：{target}（{exc.message}）",
                code=TargetError.code,
                recoverable=False,
                details={"target": target, "git_error": exc.code},
            ) from None
    raise TargetError(
        f"目标不存在或不是目录：{target}（本地路径或 git URL）",
        code=TargetError.code,
        recoverable=False,
        details={"target": target},
    )


def _git_available() -> bool:
    result = run_command(["git", "--version"], timeout_s=5.0)
    return result.ok


def _clone_target(url: str, work: WorkLayout, *, refresh: bool, timeout_s: float) -> Target:
    if not _git_available():
        raise GitError("未找到 git，无法克隆 URL 目标；请安装 git 或改用本地路径", code=GIT_UNAVAILABLE)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    dest = work.cache_repos_dir / digest
    cloned = False
    if dest.exists() and refresh:
        shutil.rmtree(dest)
    if not dest.exists():
        result = run_command(
            ["git", "clone", "--depth", "1", url, str(dest)],
            timeout_s=timeout_s,
            env_extra={"GIT_TERMINAL_PROMPT": "0"},
        )
        if result.timed_out:
            raise GitError(f"git clone 超时（{timeout_s:.0f}s）：{url}", code=GIT_CLONE_TIMEOUT, details={"url": url})
        if not result.ok:
            raise GitError(
                f"git clone 失败：{url}",
                code=GIT_CLONE_FAILED,
                details={"url": url, "stderr_tail": result.tail(20)},
            )
        cloned = True
    commit = None
    probe = run_command(["git", "-C", str(dest), "rev-parse", "HEAD"], timeout_s=5.0)
    if probe.ok:
        commit = probe.stdout.strip() or None
    return Target(kind=TargetKind.GIT, original=url, path=str(dest), commit=commit, cloned=cloned)


def scan_target(target: str, *, work: WorkLayout, refresh: bool = False, clone_timeout_s: float = 120.0) -> ScanResult:
    resolved = resolve_target(target, work=work, refresh=refresh, clone_timeout_s=clone_timeout_s)
    return scan_root(Path(resolved.path), target=resolved)


def scan_root(root: Path, *, target: Target) -> ScanResult:
    warnings: list[Note] = []
    dep_files: list[DependencyFile] = []
    dependencies: list[Dependency] = []
    index_urls: list[str] = []
    find_links: list[str] = []
    package_managers: set[str] = set()
    python_candidates: list[tuple[int, str, str]] = []
    entry_points: list[str] = []

    def add_index_urls(urls: list[str]) -> None:
        for url in urls:
            if url not in index_urls:
                index_urls.append(url)

    requirements_paths = sorted(root.glob("requirements*.txt"))[:MAX_REQUIREMENTS_FILES]
    nested = root / "requirements"
    if nested.is_dir():
        requirements_paths.extend(sorted(nested.glob("*.txt"))[:MAX_REQUIREMENTS_FILES])
    for req_path in requirements_paths:
        parsed = parsers.parse_requirements_file(req_path)
        rel = str(req_path.relative_to(root))
        dep_files.append(
            DependencyFile(
                kind="requirements",
                path=rel,
                index_urls=list(parsed.index_urls),
                find_links=list(parsed.find_links),
            )
        )
        dependencies.extend(parsed.dependencies)
        add_index_urls(parsed.index_urls)
        for url in parsed.find_links:
            if url not in find_links:
                find_links.append(url)
        warnings.extend(parsed.notes)
        package_managers.add("pip")

    pyproject_path = root / "pyproject.toml"
    if pyproject_path.is_file():
        dep_files.append(DependencyFile(kind="pyproject", path="pyproject.toml"))
        try:
            parsed_pyproject = parsers.parse_pyproject(pyproject_path)
            dependencies.extend(parsed_pyproject.dependencies)
            warnings.extend(parsed_pyproject.notes)
            package_managers.update(parsed_pyproject.package_managers)
            entry_points.extend(parsed_pyproject.scripts)
            if parsed_pyproject.dependencies:
                package_managers.add("pip")
            if parsed_pyproject.python_requires:
                spec, source = parsed_pyproject.python_requires
                rank = 0 if "project.requires-python" in source else 1
                python_candidates.append((rank, spec, source))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            warnings.append(
                Note(code="PYPROJECT_PARSE_FAILED", message=f"pyproject.toml 解析失败：{type(exc).__name__}: {exc}")
            )

    setup_cfg_path = root / "setup.cfg"
    if setup_cfg_path.is_file():
        dep_files.append(DependencyFile(kind="setup_cfg", path="setup.cfg"))
        try:
            parsed_cfg = parsers.parse_setup_cfg(setup_cfg_path)
            dependencies.extend(parsed_cfg.dependencies)
            if parsed_cfg.python_requires:
                python_candidates.append((2, *parsed_cfg.python_requires))
        except (OSError, ValueError) as exc:
            warnings.append(
                Note(code="SETUP_CFG_PARSE_FAILED", message=f"setup.cfg 解析失败：{type(exc).__name__}: {exc}")
            )

    setup_py_path = root / "setup.py"
    if setup_py_path.is_file():
        dep_files.append(DependencyFile(kind="setup_py", path="setup.py", note="仅静态提取，不执行"))
        try:
            parsed_setup = parsers.parse_setup_py(setup_py_path)
            dependencies.extend(parsed_setup.dependencies)
            warnings.extend(parsed_setup.notes)
            if parsed_setup.python_requires:
                python_candidates.append((3, *parsed_setup.python_requires))
        except OSError as exc:
            warnings.append(Note(code="SETUP_PY_UNREADABLE", message=f"setup.py 读取失败：{exc}"))

    for env_name in ("environment.yml", "environment.yaml"):
        env_path = root / env_name
        if env_path.is_file():
            dep_files.append(DependencyFile(kind="environment_yml", path=env_name, note="仅解析，验证走 pip/uv"))
            try:
                parsed_env = parsers.parse_environment_yml(env_path)
                dependencies.extend(parsed_env.dependencies)
                warnings.extend(parsed_env.notes)
                package_managers.add("conda")
                if parsed_env.python_requires:
                    python_candidates.append((4, *parsed_env.python_requires))
            except Exception as exc:  # yaml errors have no single base class here
                warnings.append(
                    Note(code="YAML_PARSE_FAILED", message=f"{env_name} 解析失败：{type(exc).__name__}: {exc}")
                )
            break

    pipfile_path = root / "Pipfile"
    if pipfile_path.is_file():
        dep_files.append(DependencyFile(kind="pipfile", path="Pipfile"))
        try:
            parsed_pipfile = parsers.parse_pipfile(pipfile_path)
            dependencies.extend(parsed_pipfile.dependencies)
            package_managers.add("pipenv")
            if parsed_pipfile.python_requires:
                python_candidates.append((5, *parsed_pipfile.python_requires))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            warnings.append(Note(code="PIPFILE_PARSE_FAILED", message=f"Pipfile 解析失败：{type(exc).__name__}: {exc}"))

    python_version_path = root / ".python-version"
    if python_version_path.is_file():
        dep_files.append(DependencyFile(kind="python_version", path=".python-version"))
        try:
            found = parsers.parse_python_version_file(python_version_path)
            if found:
                python_candidates.append((6, *found))
        except OSError as exc:
            warnings.append(Note(code="PYTHON_VERSION_UNREADABLE", message=f".python-version 读取失败：{exc}"))

    if (root / "uv.lock").is_file() or (root / ".python-version").is_file():
        package_managers.add("uv")

    python_req = _select_python_requirement(python_candidates, warnings)
    ml_stack = _build_ml_stack(dependencies)
    cuda = _infer_cuda(dependencies, dep_files, ml_stack)
    entry_points.extend(_detect_root_entrypoints(root))
    deduped_entrypoints = list(dict.fromkeys(entry_points))[:MAX_ENTRYPOINTS]

    return ScanResult(
        target=target,
        python=python_req,
        package_managers=sorted(package_managers),
        dependency_files=dep_files,
        dependencies=dependencies,
        ml_stack=ml_stack,
        cuda=cuda,
        entrypoints=deduped_entrypoints,
        warnings=warnings,
    )


def _select_python_requirement(candidates: list[tuple[int, str, str]], warnings: list[Note]) -> PythonRequirement:
    from packaging.specifiers import InvalidSpecifier, SpecifierSet

    for _, spec, source in sorted(candidates, key=lambda item: item[0]):
        try:
            SpecifierSet(spec)
        except InvalidSpecifier:
            warnings.append(
                Note(code="PYTHON_REQUIRES_INVALID", message=f"{source}: 无法解析的 Python 约束「{spec}」，已忽略")
            )
            continue
        return PythonRequirement(requires=spec, source=source)
    return PythonRequirement()


def _role_for(name: str) -> MlRole | None:
    lowered = name.lower()
    if lowered in ML_ROLES:
        return ML_ROLES[lowered]
    if lowered.startswith("nvidia-"):
        return MlRole.TOOLING
    if lowered.startswith("cupy"):
        return MlRole.TOOLING
    return None


def _build_ml_stack(dependencies: list[Dependency]) -> list[MlStackItem]:
    by_name: dict[str, MlStackItem] = {}
    for dep in dependencies:
        role = _role_for(dep.name)
        if role is None:
            continue
        key = dep.name.lower()
        existing = by_name.get(key)
        if existing is None:
            by_name[key] = MlStackItem(name=key, role=role, specifier=dep.specifier)
        elif existing.specifier is None and dep.specifier is not None:
            existing.specifier = dep.specifier
    return sorted(by_name.values(), key=lambda item: (ROLE_ORDER[item.role], item.name))


def _infer_cuda(
    dependencies: list[Dependency], dep_files: list[DependencyFile], ml_stack: list[MlStackItem]
) -> CudaHints:
    # Priority order for the *chosen* runtime; every category still contributes evidence.
    local_entries: list[tuple[str, str]] = []
    index_entries: list[tuple[str, str]] = []
    conda_entries: list[tuple[str, str]] = []
    nvidia_entries: list[tuple[str, str]] = []

    for dep in dependencies:
        if dep.specifier and (match := _LOCAL_VERSION_RE.search(dep.specifier)):
            token = f"cu{match.group(1)}"
            runtime = cuda_runtime_from_token(token)
            if runtime:
                local_entries.append((f"{dep.source}: {dep.name}{dep.specifier} → {token}", runtime))

    for dep_file in dep_files:
        for url in dep_file.index_urls:
            if match := _CUDA_INDEX_RE.search(url):
                token = match.group(1)
                runtime = cuda_runtime_from_token(token)
                if runtime:
                    index_entries.append((f"{dep_file.path}: --index-url {url} → {token}", runtime))

    for dep in dependencies:
        if dep.name.lower() in _CONDA_CUDA_NAMES and dep.specifier:
            version = dep.specifier.lstrip("=<>!~")
            if version:
                conda_entries.append((f"{dep.source}: {dep.name}={version}", version))

    for dep in dependencies:
        lowered = dep.name.lower()
        if lowered.startswith("nvidia-") and (match := re.search(r"-cu(\d+)$", lowered)):
            token = f"cu{match.group(1)}"
            runtime = cuda_runtime_from_token(token)
            if runtime:
                nvidia_entries.append((f"{dep.source}: {dep.name}（CUDA {runtime}，minor 未知）", runtime))

    all_entries = local_entries + index_entries + conda_entries + nvidia_entries
    evidence = list(dict.fromkeys(text for text, _ in all_entries))
    requested: str | None = None
    for category in (local_entries, index_entries, conda_entries, nvidia_entries):
        if category:
            requested = category[0][1]
            break

    strength = RequirementStrength.NONE
    hard = any(_role_for(item.name) == MlRole.EXTENSION for item in ml_stack) or any(
        item.name in HARD_GPU_SIGNALS or item.name.startswith("nvidia-") or item.name.startswith("cupy")
        for item in ml_stack
    )
    soft = any(item.role == MlRole.CORE for item in ml_stack)
    if hard or (requested is not None):
        strength = RequirementStrength.HARD
    elif soft:
        strength = RequirementStrength.SOFT

    return CudaHints(requested_runtime=requested, evidence=evidence, gpu_required_strength=strength)


def _detect_root_entrypoints(root: Path) -> list[str]:
    found: list[str] = []
    for name in ROOT_ENTRYPOINT_CANDIDATES:
        if (root / name).is_file():
            found.append(name)
    for path in sorted(root.glob("train_*.py"))[:5]:
        found.append(path.name)
    scripts_dir = root / "scripts"
    if scripts_dir.is_dir():
        for path in sorted(scripts_dir.glob("*.sh"))[:5]:
            found.append(f"scripts/{path.name}")
    return found
