"""Shared builders for tests (importable thanks to pytest pythonpath=["tests"])."""

from __future__ import annotations

from datetime import datetime

from envdoctor.domain.enums import GpuSource, RequirementStrength, TargetKind
from envdoctor.domain.models import (
    CudaHints,
    CurrentPython,
    Dependency,
    GpuDevice,
    GpuInfo,
    Interpreter,
    MachineProfile,
    OsInfo,
    ScanResult,
    Target,
    Tooling,
)

DEFAULT_VRAM = 24576 * 1024 * 1024


def make_profile(
    *,
    os_name: str = "Windows",
    os_release: str = "11",
    gpu_present: bool = False,
    driver: str | None = None,
    driver_max_cuda: str | None = None,
    device_name: str = "NVIDIA GeForce RTX 3090",
    interpreters: tuple[str, ...] = ("3.11.9",),
    reason: str | None = None,
) -> MachineProfile:
    devices = (
        [GpuDevice(index=0, name=device_name, vram_bytes=DEFAULT_VRAM, compute_capability="8.6")] if gpu_present else []
    )
    gpu = GpuInfo(
        present=gpu_present,
        source=GpuSource.NVIDIA_SMI if gpu_present else GpuSource.NONE,
        reason=None if gpu_present else (reason or "nvidia-smi 未找到（PATH 与 System32 均无）"),
        driver_version=driver,
        driver_max_cuda=driver_max_cuda,
        devices=devices,
    )
    versions = list(interpreters)
    return MachineProfile(
        os=OsInfo(name=os_name, release=os_release, arch="AMD64"),
        python=CurrentPython(
            version=versions[0] if versions else "3.11.9",
            executable="C:/fake/python.exe",
            console_encoding="utf-8",
        ),
        interpreters=[Interpreter(version=v, path=f"C:/fake/{v}/python.exe", source="fake") for v in versions],
        tooling=Tooling(uv="0.12.10", git="2.55.0", pip="24.0"),
        gpu=gpu,
        memory_bytes=32 * 1024**3,
        temp_disk_free_bytes=500 * 1024**3,
        collected_at=datetime(2026, 9, 22, 12, 0, 0).isoformat(),
    )


def make_report(*, checkpoints=None, scan=None, profile=None, verify_performed: bool = False):
    from envdoctor.compat.engine import analyze, decide_overall
    from envdoctor.domain.models import Durations, Report
    from envdoctor.report.writer import build_report

    scan = scan or make_scan([Dependency(name="torch", specifier="==2.4.1", source="requirements.txt")])
    profile = profile or make_profile()
    verdicts = analyze(scan, profile)
    checkpoint_list = list(checkpoints or [])
    overall, reason = decide_overall(verdicts, checkpoint_list)
    report: Report = build_report(
        run_id="20260922-120000-abcd1234",
        scan=scan,
        probe=profile,
        verdicts=verdicts,
        checkpoints=checkpoint_list,
        overall=overall,
        overall_reason=reason,
        verify_performed=verify_performed,
        explain=None,
        durations=Durations(),
    )
    return report


def make_scan(
    dependencies: list[Dependency],
    *,
    requested_runtime: str | None = None,
    strength: RequirementStrength = RequirementStrength.NONE,
    python_requires: str | None = None,
    dependency_files: list | None = None,
) -> ScanResult:
    return ScanResult(
        target=Target(kind=TargetKind.LOCAL, original="synthetic", path="C:/fake/project"),
        python={"requires": python_requires, "source": "pyproject.toml:project.requires-python"}
        if python_requires
        else {},  # type: ignore[arg-type]
        dependencies=dependencies,
        cuda=CudaHints(requested_runtime=requested_runtime, gpu_required_strength=strength),
        dependency_files=dependency_files or [],
    )
