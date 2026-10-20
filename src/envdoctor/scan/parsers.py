"""Parsers for the ecosystem files envdoctor understands (DEVELOPMENT.md §9.2).

Parsers never execute project code (setup.py is regex-only) and never raise for
malformed input: problems become ``Note`` entries and the scan continues.
"""

from __future__ import annotations

import configparser
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from packaging.requirements import InvalidRequirement, Requirement

from envdoctor.domain.models import Dependency, Note

MAX_REQUIREMENT_DEPTH = 5

_INDEX_URL_RE = re.compile(r"^(?:--index-url|--extra-index-url|-i)(?:=|\s+)(\S+)$")
_FIND_LINKS_RE = re.compile(r"^(?:--find-links|-f)(?:=|\s+)(\S+)$")
_REQUIREMENT_RE = re.compile(r"^(?:-r|--requirement)(?:=|\s+)(\S+)$")
_EDITABLE_RE = re.compile(r"^(?:-e|--editable)(?:=|\s+)(\S+)$")
_CONSTRAINT_RE = re.compile(r"^(?:-c|--constraint)(?:=|\s+)(\S+)$")
_HASH_RE = re.compile(r"\s+--hash=\S+")
_OTHER_OPTION_RE = re.compile(r"^--[a-zA-Z-]+(?:=|\s|$)")


@dataclass
class RequirementsParse:
    files: list[str] = field(default_factory=list)
    dependencies: list[Dependency] = field(default_factory=list)
    index_urls: list[str] = field(default_factory=list)
    find_links: list[str] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)


def dependency_from_line(line: str, *, source: str, group: str = "default") -> Dependency | None:
    stripped = _HASH_RE.sub("", line.strip())
    try:
        req = Requirement(stripped)
    except InvalidRequirement:
        return None
    return Dependency(
        name=req.name,
        specifier=str(req.specifier) or None,
        extras=sorted(req.extras),
        marker=str(req.marker) if req.marker else None,
        source=source,
        group=group,
    )


def parse_requirements_text(text: str, *, source: str) -> RequirementsParse:
    result = RequirementsParse(files=[source])
    joined = re.sub(r"\\\r?\n", " ", text)
    for raw in joined.splitlines():
        line = re.split(r"\s+#", raw, maxsplit=1)[0].strip()
        if not line or line.startswith("#"):
            continue
        if match := _INDEX_URL_RE.match(line):
            result.index_urls.append(match.group(1))
            continue
        if match := _FIND_LINKS_RE.match(line):
            result.find_links.append(match.group(1))
            continue
        if match := _REQUIREMENT_RE.match(line):
            result.notes.append(Note(code="REQUIREMENTS_INCLUDE", message=f"{source}: 递归包含 {match.group(1)}"))
            continue  # recursion is handled by parse_requirements_file
        if match := _EDITABLE_RE.match(line):
            result.notes.append(
                Note(
                    code="EDITABLE_SKIPPED",
                    message=f"{source}: editable 安装被跳过（{match.group(1)}），验证环境不安装它",
                )
            )
            continue
        if match := _CONSTRAINT_RE.match(line):
            result.notes.append(Note(code="CONSTRAINT_NOTE", message=f"{source}: 约束文件 {match.group(1)} 不参与安装"))
            continue
        if _OTHER_OPTION_RE.match(line) or line.startswith("-"):
            continue
        dep = dependency_from_line(line, source=source)
        if dep is None:
            result.notes.append(Note(code="REQUIREMENT_UNPARSEABLE", message=f"{source}: 无法解析依赖行: {line[:120]}"))
            continue
        result.dependencies.append(dep)
    return result


def _requirement_refs(text: str) -> list[str]:
    joined = re.sub(r"\\\r?\n", " ", text)
    refs = []
    for raw in joined.splitlines():
        line = re.split(r"\s+#", raw, maxsplit=1)[0].strip()
        if match := _REQUIREMENT_RE.match(line):
            refs.append(match.group(1))
    return refs


def parse_requirements_file(path: Path, *, depth: int = 0, seen: set[str] | None = None) -> RequirementsParse:
    """Parse a requirements file, following ``-r`` includes (depth-limited, cycle-safe)."""
    seen = seen if seen is not None else set()
    result = RequirementsParse()
    key = str(path.resolve()).lower()
    if depth > MAX_REQUIREMENT_DEPTH:
        result.notes.append(
            Note(code="REQUIREMENTS_DEPTH_LIMIT", message=f"递归深度超过 {MAX_REQUIREMENT_DEPTH}：{path.name}")
        )
        return result
    if key in seen:
        result.notes.append(Note(code="REQUIREMENTS_CYCLE", message=f"检测到循环包含，已跳过：{path.name}"))
        return result
    seen.add(key)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        result.notes.append(Note(code="REQUIREMENTS_UNREADABLE", message=f"无法读取 {path}: {exc}"))
        return result
    own = parse_requirements_text(text, source=path.name)
    result.files = [path.name]
    result.dependencies.extend(own.dependencies)
    result.index_urls.extend(own.index_urls)
    result.find_links.extend(own.find_links)
    result.notes.extend(own.notes)
    for ref in _requirement_refs(text):
        child = (path.parent / ref).resolve()
        if not child.is_file():
            result.notes.append(
                Note(code="REQUIREMENTS_INCLUDE_MISSING", message=f"{path.name}: 引用的文件不存在: {ref}")
            )
            continue
        sub = parse_requirements_file(child, depth=depth + 1, seen=seen)
        result.files.extend(sub.files)
        result.dependencies.extend(sub.dependencies)
        result.index_urls.extend(sub.index_urls)
        result.find_links.extend(sub.find_links)
        result.notes.extend(sub.notes)
    return result


@dataclass
class PyProjectParse:
    dependencies: list[Dependency] = field(default_factory=list)
    python_requires: tuple[str, str] | None = None
    package_managers: set[str] = field(default_factory=set)
    scripts: list[str] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)


_CARET_RE = re.compile(r"^\^(\d+)(?:\.(\d+))?(?:\.(\d+))?$")
_TILDE_RE = re.compile(r"^~(\d+)(?:\.(\d+))?(?:\.(\d+))?$")


def poetry_constraint_to_pep440(constraint: str) -> str | None:
    """Translate the common poetry range forms (``^3.10`` / ``~3.11``) to PEP 440."""
    constraint = constraint.strip()
    if constraint in {"*", ""}:
        return None
    if match := _CARET_RE.match(constraint):
        major = int(match.group(1))
        minor = int(match.group(2)) if match.group(2) else 0
        lower = f"{major}.{minor}" if match.group(2) else str(major)
        upper = f"{major + 1}.0" if match.group(2) else str(major + 1)
        return f">={lower},<{upper}"
    if match := _TILDE_RE.match(constraint):
        major = int(match.group(1))
        if match.group(2) is None:
            return f">={major},<{major + 1}"
        minor = int(match.group(2))
        return f">={major}.{minor},<{major + 1}.0"
    return constraint


def _poetry_dep(name: str, value: object, *, source: str, group: str) -> Dependency:
    specifier: str | None = None
    extras: list[str] = []
    marker: str | None = None
    if isinstance(value, str):
        specifier = poetry_constraint_to_pep440(value)
    elif isinstance(value, dict):
        raw_version = value.get("version")
        if isinstance(raw_version, str):
            specifier = poetry_constraint_to_pep440(raw_version)
        raw_extras = value.get("extras")
        if isinstance(raw_extras, list):
            extras = sorted(str(item) for item in raw_extras)
        raw_markers = value.get("markers") or value.get("python")
        if isinstance(raw_markers, str):
            marker = raw_markers
    return Dependency(name=name, specifier=specifier, extras=extras, marker=marker, source=source, group=group)


def parse_pyproject(path: Path) -> PyProjectParse:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    result = PyProjectParse()
    source = path.name

    project = data.get("project") or {}
    for spec in project.get("dependencies") or []:
        dep = dependency_from_line(str(spec), source=f"{source}:project.dependencies")
        if dep:
            result.dependencies.append(dep)
    for group, specs in (project.get("optional-dependencies") or {}).items():
        for spec in specs or []:
            dep = dependency_from_line(str(spec), source=f"{source}:optional:{group}", group=str(group))
            if dep:
                result.dependencies.append(dep)
    requires_python = project.get("requires-python")
    if isinstance(requires_python, str) and requires_python.strip():
        result.python_requires = (requires_python.strip(), f"{source}:project.requires-python")
    result.scripts.extend(str(name) for name in (project.get("scripts") or {}))

    tool = data.get("tool") or {}
    poetry = tool.get("poetry") or {}
    if poetry:
        result.package_managers.add("poetry")
        for name, value in (poetry.get("dependencies") or {}).items():
            if str(name).lower() == "python":
                if result.python_requires is None and isinstance(value, str):
                    translated = poetry_constraint_to_pep440(value)
                    if translated:
                        result.python_requires = (translated, f"{source}:tool.poetry.dependencies.python")
                continue
            result.dependencies.append(
                _poetry_dep(str(name), value, source=f"{source}:tool.poetry.dependencies", group="default")
            )
        for group_name, group_data in (poetry.get("group") or {}).items():
            for name, value in (group_data.get("dependencies") or {}).items():
                result.dependencies.append(
                    _poetry_dep(
                        str(name), value, source=f"{source}:tool.poetry.group.{group_name}", group=str(group_name)
                    )
                )
    if "uv" in tool:
        result.package_managers.add("uv")
    build_system = data.get("build-system") or {}
    build_requires = build_system.get("requires") or []
    if build_requires:
        result.notes.append(
            Note(
                code="BUILD_REQUIRES",
                message=f"{source}: 构建依赖 {', '.join(str(x) for x in build_requires)} 不参与运行时安装",
            )
        )
    return result


def parse_setup_cfg(path: Path) -> PyProjectParse:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(path.read_text(encoding="utf-8", errors="replace"))
    result = PyProjectParse()
    source = path.name
    if parser.has_section("options"):
        section = parser["options"]
        raw_install = section.get("install_requires", "")
        for line in raw_install.splitlines():
            dep = dependency_from_line(line, source=f"{source}:options.install_requires")
            if dep:
                result.dependencies.append(dep)
        python_requires = section.get("python_requires")
        if python_requires:
            result.python_requires = (python_requires.strip(), f"{source}:options.python_requires")
    if parser.has_section("options.extras_require"):
        for group, raw in parser["options.extras_require"].items():
            for line in raw.splitlines():
                dep = dependency_from_line(line, source=f"{source}:extras.{group}", group=group)
                if dep:
                    result.dependencies.append(dep)
    return result


_INSTALL_REQUIRES_RE = re.compile(r"install_requires\s*=\s*\[(.*?)\]", re.DOTALL)
_PYTHON_REQUIRES_RE = re.compile(r"python_requires\s*=\s*['\"]([^'\"]+)['\"]")
_SPEC_RE = re.compile(r"['\"]([^'\"]+)['\"]")


def parse_setup_py(path: Path) -> PyProjectParse:
    """Regex-only extraction: setup.py is never executed."""
    text = path.read_text(encoding="utf-8", errors="replace")
    result = PyProjectParse()
    source = path.name
    if match := _INSTALL_REQUIRES_RE.search(text):
        for spec in _SPEC_RE.findall(match.group(1)):
            dep = dependency_from_line(spec, source=f"{source}:install_requires")
            if dep:
                result.dependencies.append(dep)
    if match := _PYTHON_REQUIRES_RE.search(text):
        result.python_requires = (match.group(1), f"{source}:python_requires")
    if not result.dependencies and not result.python_requires:
        result.notes.append(
            Note(code="SETUP_PY_UNPARSED", message=f"{source}: 未能静态提取依赖（未执行 setup.py），结果可能不完整")
        )
    return result


@dataclass
class EnvironmentParse:
    dependencies: list[Dependency] = field(default_factory=list)
    python_requires: tuple[str, str] | None = None
    notes: list[Note] = field(default_factory=list)


def _conda_pin(spec: str) -> str | None:
    version = spec.strip()
    if not version:
        return None
    return f"=={version}"


def _python_series_pin(version: str) -> str | None:
    """``3.11`` from conda means the 3.11 series → ``==3.11.*``."""
    version = version.strip()
    if not version:
        return None
    if "*" in version:
        return f"=={version}"
    if version.count(".") <= 1:
        return f"=={version}.*"
    return f"=={version}"


def parse_environment_yml(path: Path) -> EnvironmentParse:
    data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    source = path.name
    result = EnvironmentParse()
    if not isinstance(data, dict):
        result.notes.append(Note(code="CONDA_ENV_SHAPE", message=f"{source}: 顶层结构不是 mapping，已跳过"))
        return result
    for item in data.get("dependencies") or []:
        if isinstance(item, str):
            name, _, version = item.partition("=")
            name = name.strip()
            if not name:
                continue
            lowered = name.lower()
            if lowered == "python":
                spec = _python_series_pin(version)
                if spec:
                    result.python_requires = (spec, f"{source}:python")
                continue
            result.dependencies.append(
                Dependency(name=name, specifier=_conda_pin(version), source=f"{source}:conda", group="conda")
            )
        elif isinstance(item, dict):
            pip_lines = item.get("pip") or []
            for line in pip_lines:
                dep = dependency_from_line(str(line), source=f"{source}:pip")
                if dep:
                    result.dependencies.append(dep)
    return result


def parse_pipfile(path: Path) -> PyProjectParse:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    source = path.name
    result = PyProjectParse()
    for group, key in (("default", "packages"), ("dev", "dev-packages")):
        for name, value in (data.get(key) or {}).items():
            specifier = None
            if isinstance(value, str) and value not in {"*", ""}:
                specifier = poetry_constraint_to_pep440(value)
            elif isinstance(value, dict) and isinstance(value.get("version"), str):
                raw = value["version"]
                specifier = poetry_constraint_to_pep440(raw) if raw not in {"*", ""} else None
            result.dependencies.append(
                Dependency(name=str(name), specifier=specifier, source=f"{source}:{key}", group=group)
            )
    requires = data.get("requires") or {}
    python_version = requires.get("python_version") or requires.get("python_full_version")
    if isinstance(python_version, str) and python_version.strip():
        raw = python_version.strip()
        spec = raw if raw.startswith((">", "<", "=", "~", "!")) else f"=={raw}.*"
        result.python_requires = (spec, f"{source}:requires.python_version")
    return result


def parse_python_version_file(path: Path) -> tuple[str, str] | None:
    raw = path.read_text(encoding="utf-8", errors="replace").strip()
    if not raw:
        return None
    spec = raw if raw.startswith((">", "<", "=", "~", "!")) else f"=={raw}.*"
    return spec, path.name
