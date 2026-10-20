from __future__ import annotations

import sys
from pathlib import Path

from envdoctor.domain.enums import GpuSource
from envdoctor.domain.models import GpuInfo
from envdoctor.probe.machine import (
    collect_interpreters,
    current_version_string,
    parse_py_launcher,
    parse_uv_python_list,
    probe_machine,
)
from envdoctor.probe.nvidia import (
    assess_smi_outputs,
    parse_smi_header,
    parse_smi_query_csv,
    probe_gpu,
)
from envdoctor.subproc import CommandResult, run_command

SMI_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "smi"

QUERY_WIN = "0, NVIDIA GeForce RTX 3090, 551.61, 24576, 8.6\n"
QUERY_LINUX_MULTI = "0, NVIDIA A100-SXM4-40GB, 535.54.03, 40960, 8.0\n1, NVIDIA A100-SXM4-40GB, 535.54.03, 40960, 8.0\n"


def _read(name: str) -> str:
    return (SMI_FIXTURES / name).read_text(encoding="utf-8")


def test_parse_smi_header_win() -> None:
    driver, cuda = parse_smi_header(_read("win_3090.txt"))
    assert driver == "551.61"
    assert cuda == "12.4"


def test_parse_smi_header_linux() -> None:
    driver, cuda = parse_smi_header(_read("linux_a100.txt"))
    assert driver == "535.54.03"
    assert cuda == "12.2"


def test_parse_smi_query_csv() -> None:
    devices = parse_smi_query_csv(QUERY_WIN)
    assert len(devices) == 1
    assert devices[0].name == "NVIDIA GeForce RTX 3090"
    assert devices[0].vram_bytes == 24576 * 1024 * 1024
    assert devices[0].compute_capability == "8.6"

    multi = parse_smi_query_csv(QUERY_LINUX_MULTI)
    assert [device.index for device in multi] == [0, 1]


def test_assess_smi_outputs_ok() -> None:
    query = CommandResult(argv=["nvidia-smi"], returncode=0, stdout=QUERY_WIN)
    header = CommandResult(argv=["nvidia-smi"], returncode=0, stdout=_read("win_3090.txt"))
    info = assess_smi_outputs(query, header)
    assert info is not None
    assert info.present is True
    assert info.source == GpuSource.NVIDIA_SMI
    assert info.driver_version == "551.61"
    assert info.driver_max_cuda == "12.4"


def test_assess_smi_outputs_failure() -> None:
    assert assess_smi_outputs(None, None) is None
    assert assess_smi_outputs(CommandResult(argv=[], returncode=1, stderr="boom"), None) is None
    assert assess_smi_outputs(CommandResult(argv=[], returncode=0, stdout=""), None) is None


def _smi_runner(query: CommandResult, header: CommandResult):
    def runner(argv, *, timeout_s=30.0, **_kwargs):
        if any("--query-gpu" in arg for arg in argv):
            return query
        return header

    return runner


def test_probe_gpu_uses_smi_when_available() -> None:
    query = CommandResult(argv=[], returncode=0, stdout=QUERY_WIN)
    header = CommandResult(argv=[], returncode=0, stdout=_read("win_3090.txt"))
    info, attempts = probe_gpu(
        smi_path="C:/fake/nvidia-smi.exe",
        runner=_smi_runner(query, header),
        nvml_probe=lambda: (None, "should not be reached"),
    )
    assert info.present is True
    assert info.source == GpuSource.NVIDIA_SMI
    assert attempts == []


def test_probe_gpu_falls_back_to_nvml() -> None:
    nvml_info = GpuInfo(present=True, source=GpuSource.NVML, driver_version="550.54", driver_max_cuda="12.4")
    info, attempts = probe_gpu(
        find=lambda: None,
        nvml_probe=lambda: (nvml_info, ""),
    )
    assert info.source == GpuSource.NVML
    assert any("nvidia-smi 未找到" in item for item in attempts)


def test_probe_gpu_none_with_reason() -> None:
    info, attempts = probe_gpu(
        find=lambda: None,
        nvml_probe=lambda: (None, "NVML 初始化失败：NVMLError: driver not loaded"),
    )
    assert info.present is False
    assert info.source == GpuSource.NONE
    assert info.reason is not None
    assert "nvidia-smi 未找到" in info.reason
    assert "driver not loaded" in info.reason
    assert len(attempts) == 2


def test_parse_py_launcher() -> None:
    text = (
        " -V:3.11 *        C:\\Python311\\python.exe\n -V:3.12          C:\\Python312\\python.exe\nSome header line\n"
    )
    found = parse_py_launcher(text)
    assert [(item.version, item.source) for item in found] == [("3.11", "py-launcher"), ("3.12", "py-launcher")]
    assert found[0].path == "C:\\Python311\\python.exe"


def test_parse_uv_python_list() -> None:
    text = (
        "cpython-3.11.9-windows-x86_64-none                 C:\\Python311\\python.exe\n"
        "cpython-3.12.7-windows-x86_64-none                 <download available>\n"
        "cpython-3.10.11-windows-x86_64-none                C:\\code\\a b\\python.exe\n"
    )
    found = parse_uv_python_list(text)
    assert [item.version for item in found] == ["3.11.9", "3.10.11"]
    assert found[1].path == "C:\\code\\a b\\python.exe"


def test_collect_interpreters_current_first() -> None:
    found = collect_interpreters(runner=run_command)
    assert found[0].source == "current"
    assert found[0].version == current_version_string()


def test_probe_machine_end_to_end() -> None:
    profile = probe_machine()
    assert profile.python.version == current_version_string()
    assert profile.python.executable
    assert profile.os.name in {"Windows", "Linux", "Darwin"}
    assert isinstance(profile.gpu.present, bool)
    if not profile.gpu.present:
        assert profile.gpu.reason
    assert profile.python.console_encoding is not None
    assert sys.executable in [item.path for item in profile.interpreters]
