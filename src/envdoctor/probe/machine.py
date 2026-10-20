"""Machine probe (DEVELOPMENT.md §10): OS, interpreters, toolchain, GPU, resources.

Nothing here raises for a missing tool: absent things become ``null`` plus a
reason. The probe is the one place that must degrade gracefully on every machine.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import sys
import tempfile
from datetime import datetime

from envdoctor.domain.models import CurrentPython, Interpreter, MachineProfile, Note, OsInfo, Tooling
from envdoctor.probe.nvidia import probe_gpu
from envdoctor.subproc import run_command

_PY_LAUNCHER_LINE_RE = re.compile(r"^\s*-V:(\d+\.\d+)\s+\*?\s*(.+?)\s*$")
_UV_PYTHON_LINE_RE = re.compile(r"^cpython-(\d+\.\d+\.\d+)-\S+\s+(\S.*)$")
_UV_VERSION_RE = re.compile(r"uv\s+([0-9][^\s]*)")
_GIT_VERSION_RE = re.compile(r"git version\s+(\S+)")
_CONDA_VERSION_RE = re.compile(r"conda\s+([0-9][^\s]*)")
_PIP_VERSION_RE = re.compile(r"pip\s+([0-9][^\s]*)")


def current_version_string() -> str:
    info = sys.version_info
    return f"{info.major}.{info.minor}.{info.micro}"


def parse_py_launcher(text: str) -> list[Interpreter]:
    """Parse ``py -0p`` output: `` -V:3.11 *  C:\\Python311\\python.exe``."""
    found: list[Interpreter] = []
    for raw in text.splitlines():
        match = _PY_LAUNCHER_LINE_RE.match(raw)
        if not match:
            continue
        version, path = match.group(1), match.group(2).strip()
        if not path or path.startswith("<"):
            continue
        found.append(Interpreter(version=version, path=path, source="py-launcher"))
    return found


def parse_uv_python_list(text: str) -> list[Interpreter]:
    """Parse ``uv python list --only-installed`` output."""
    found: list[Interpreter] = []
    for raw in text.splitlines():
        match = _UV_PYTHON_LINE_RE.match(raw.strip())
        if not match:
            continue
        version, path = match.group(1), match.group(2).strip()
        if not path or "<" in path:  # "<download available>" rows
            continue
        found.append(Interpreter(version=version, path=path, source="uv"))
    return found


def _first_stdout_line(result) -> str:
    for line in result.stdout.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _run_version(runner, argv: list[str], pattern: re.Pattern[str]) -> str | None:
    result = runner(argv, timeout_s=5.0)
    if result.error is not None or result.returncode != 0:
        return None
    if match := pattern.search(_first_stdout_line(result)):
        return match.group(1)
    return None


def collect_interpreters(runner=run_command) -> list[Interpreter]:
    found: list[Interpreter] = [Interpreter(version=current_version_string(), path=sys.executable, source="current")]
    if os.name == "nt":
        result = runner(["py", "-0p"], timeout_s=5.0)
        if result.ok:
            found.extend(parse_py_launcher(result.stdout))
    uv = shutil.which("uv")
    if uv:
        result = runner([uv, "python", "list", "--only-installed"], timeout_s=10.0)
        if result.ok:
            found.extend(parse_uv_python_list(result.stdout))
    deduped: dict[tuple[str, str], Interpreter] = {}
    for item in found:
        parts = item.version.split(".")
        key = (parts[0] if parts else item.version, parts[1] if len(parts) > 1 else "")
        deduped.setdefault(key, item)
    return list(deduped.values())


def collect_tooling(runner=run_command) -> Tooling:
    return Tooling(
        uv=_run_version(runner, ["uv", "--version"], _UV_VERSION_RE),
        git=_run_version(runner, ["git", "--version"], _GIT_VERSION_RE),
        conda=_run_version(runner, ["conda", "--version"], _CONDA_VERSION_RE),
        pip=_run_version(runner, [sys.executable, "-m", "pip", "--version"], _PIP_VERSION_RE),
    )


def _os_info() -> OsInfo:
    name = platform.system() or "unknown"
    release = platform.release() or ""
    if name == "Windows":
        try:
            _, version, _, _ = platform.win32_ver()
            build = int(version.split(".")[-1]) if version else 0
            if build >= 22000:
                release = "11"
        except (OSError, ValueError):  # pragma: no cover - platform oddities
            pass
    return OsInfo(name=name, release=release, arch=platform.machine() or "unknown")


def probe_machine(*, runner=run_command, gpu_probe=probe_gpu) -> MachineProfile:
    warnings: list[Note] = []

    memory_bytes: int | None = None
    try:
        import psutil

        memory_bytes = int(psutil.virtual_memory().total)
    except Exception as exc:
        warnings.append(Note(code="PSUTIL_UNAVAILABLE", message=f"psutil 不可用：{type(exc).__name__}: {exc}"))

    temp_disk_free_bytes: int | None = None
    try:
        temp_disk_free_bytes = int(shutil.disk_usage(tempfile.gettempdir()).free)
    except OSError as exc:
        warnings.append(Note(code="TEMP_DISK_UNKNOWN", message=f"无法读取临时盘空间：{exc}"))

    gpu, _attempts = gpu_probe()

    return MachineProfile(
        os=_os_info(),
        python=CurrentPython(
            version=current_version_string(),
            executable=sys.executable,
            console_encoding=getattr(sys.stdout, "encoding", None),
        ),
        interpreters=collect_interpreters(runner),
        tooling=collect_tooling(runner),
        gpu=gpu,
        memory_bytes=memory_bytes,
        temp_disk_free_bytes=temp_disk_free_bytes,
        collected_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        warnings=warnings,
    )
