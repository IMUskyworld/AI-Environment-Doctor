"""GPU probing: nvidia-smi → NVML → none. Every step's failure is recorded.

The degradation chain is a contract (DEVELOPMENT.md §10.1): a machine without a
driver must end up as ``present=false`` **with a reason**, never as an error and
never as "everything is fine".
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from envdoctor.domain.enums import GpuSource
from envdoctor.domain.models import GpuDevice, GpuInfo
from envdoctor.subproc import CommandResult, run_command

_DRIVER_RE = re.compile(r"Driver Version:\s*([0-9][0-9.]*)")
_CUDA_VERSION_RE = re.compile(r"CUDA Version:\s*([0-9]+\.[0-9]+)")
_QUERY_FIELDS = ["index", "name", "driver_version", "memory.total", "compute_cap"]


def find_smi() -> str | None:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    if os.name == "nt":
        candidate = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "nvidia-smi.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def parse_smi_header(text: str) -> tuple[str | None, str | None]:
    """Parse the default ``nvidia-smi`` banner → (driver_version, driver_max_cuda)."""
    driver = None
    cuda = None
    if match := _DRIVER_RE.search(text):
        driver = match.group(1)
    if match := _CUDA_VERSION_RE.search(text):
        cuda = match.group(1)
    return driver, cuda


def parse_smi_query_csv(text: str) -> list[GpuDevice]:
    """Parse ``--query-gpu ... --format=csv,noheader,nounits`` output."""
    devices: list[GpuDevice] = []
    for raw_line in text.splitlines():
        parts = [part.strip() for part in raw_line.split(",")]
        if len(parts) < 4 or not parts[0].isdigit():
            continue
        vram_bytes = None
        if parts[3].isdigit():
            vram_bytes = int(parts[3]) * 1024 * 1024
        compute = parts[4] if len(parts) > 4 and parts[4] not in {"", "N/A"} else None
        devices.append(GpuDevice(index=int(parts[0]), name=parts[1], vram_bytes=vram_bytes, compute_capability=compute))
    return devices


def assess_smi_outputs(query: CommandResult | None, header: CommandResult | None) -> GpuInfo | None:
    """Build a GpuInfo from smi outputs, or None when they are unusable."""
    if query is None or not query.ok or query.returncode != 0:
        return None
    devices = parse_smi_query_csv(query.stdout)
    if not devices:
        return None
    driver, driver_max_cuda = (None, None)
    if header is not None and header.returncode == 0:
        driver, driver_max_cuda = parse_smi_header(header.stdout)
    return GpuInfo(
        present=True,
        source=GpuSource.NVIDIA_SMI,
        driver_version=driver,
        driver_max_cuda=driver_max_cuda,
        devices=devices,
    )


def _decode_nvml_str(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    return str(value).strip()


def probe_with_nvml() -> tuple[GpuInfo | None, str]:
    """Second degradation step. Returns (info, failure-reason)."""
    try:
        import pynvml
    except ImportError:
        return None, "pynvml 未安装"
    try:
        pynvml.nvmlInit()
    except Exception as exc:
        return None, f"NVML 初始化失败：{type(exc).__name__}: {exc}"
    try:
        driver = _decode_nvml_str(pynvml.nvmlSystemGetDriverVersion())
        driver_max_cuda = None
        try:
            raw = int(pynvml.nvmlSystemGetCudaDriverVersion())
            driver_max_cuda = f"{raw // 1000}.{(raw % 1000) // 10}"
        except Exception:
            driver_max_cuda = None
        devices: list[GpuDevice] = []
        for index in range(pynvml.nvmlDeviceGetCount()):
            handle = pynvml.nvmlDeviceGetHandleByIndex(index)
            name = _decode_nvml_str(pynvml.nvmlDeviceGetName(handle))
            memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
            compute = None
            try:
                major, minor = pynvml.nvmlDeviceGetCudaComputeCapability(handle)
                compute = f"{major}.{minor}"
            except Exception:
                compute = None
            devices.append(GpuDevice(index=index, name=name, vram_bytes=int(memory.total), compute_capability=compute))
        if not devices:
            return None, "NVML 未报告任何 GPU"
        return (
            GpuInfo(
                present=True,
                source=GpuSource.NVML,
                driver_version=driver,
                driver_max_cuda=driver_max_cuda,
                devices=devices,
            ),
            "",
        )
    except Exception as exc:
        return None, f"NVML 查询失败：{type(exc).__name__}: {exc}"
    finally:
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass


def probe_gpu(
    *,
    smi_path: str | None = None,
    runner=run_command,
    nvml_probe=probe_with_nvml,
    find=find_smi,
    timeout_s: float = 5.0,
) -> tuple[GpuInfo, list[str]]:
    """Try nvidia-smi, then NVML. Returns (GpuInfo, attempts-log)."""
    attempts: list[str] = []

    smi = smi_path if smi_path is not None else find()
    if smi is None:
        attempts.append("nvidia-smi 未找到（PATH 与 System32 均无）")
    else:
        query = runner(
            [smi, f"--query-gpu={','.join(_QUERY_FIELDS)}", "--format=csv,noheader,nounits"],
            timeout_s=timeout_s,
        )
        header = runner([smi], timeout_s=timeout_s)
        info = assess_smi_outputs(query, header)
        if info is not None:
            return info, attempts
        failure = query.tail(5) if not query.ok else "nvidia-smi 输出中没有任何 GPU"
        attempts.append(f"nvidia-smi 不可用：{failure}")

    info, reason = nvml_probe()
    if info is not None:
        return info, attempts
    attempts.append(f"NVML 不可用：{reason}")

    return (
        GpuInfo(present=False, source=GpuSource.NONE, reason="；".join(attempts)),
        attempts,
    )
