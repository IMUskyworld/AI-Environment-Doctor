"""Knowledge base: CUDA↔driver matrix and the compatibility rule library.

Both live as JSON data files under ``compat/data/`` so that fixes and additions
are data changes, not code changes. Every entry keeps its source for auditing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

DATA_DIR = Path(__file__).resolve().parent / "data"
MATRIX_PATH = DATA_DIR / "cuda_driver_matrix.json"
RULES_PATH = DATA_DIR / "compatibility_rules.json"
PACKAGE_CUDA_PATH = DATA_DIR / "package_cuda_requirements.json"


def platform_family(name: str) -> str:
    lowered = name.lower()
    if lowered.startswith("win"):
        return "windows"
    if lowered.startswith("linux"):
        return "linux"
    if lowered.startswith(("darwin", "mac")):
        return "darwin"
    return lowered


@dataclass(frozen=True)
class MatrixEntry:
    cuda: str
    windows_min_driver: str
    linux_min_driver: str


def _platform_column(platform: str) -> str | None:
    family = platform_family(platform)
    if family == "windows":
        return "windows_min_driver"
    if family == "linux":
        return "linux_min_driver"
    return None


class CudaMatrix:
    def __init__(self, entries: list[MatrixEntry]) -> None:
        self._entries = sorted(entries, key=lambda entry: Version(entry.cuda))

    def min_driver(self, cuda: str, platform: str) -> str | None:
        """Minimum driver for a CUDA runtime. Major-only (``12``) uses the lowest minor (conservative floor)."""
        column = _platform_column(platform)
        if column is None:
            return None
        for entry in self._entries:
            if entry.cuda == cuda:
                return getattr(entry, column)
        major_entries = [entry for entry in self._entries if entry.cuda.startswith(f"{cuda}.")]
        if major_entries:
            return getattr(major_entries[0], column)
        return None

    def max_runtime_for_driver(self, driver: str, platform: str) -> str | None:
        column = _platform_column(platform)
        if column is None:
            return None
        try:
            version = Version(driver)
        except InvalidVersion:
            return None
        best: str | None = None
        for entry in self._entries:
            try:
                if Version(getattr(entry, column)) <= version:
                    best = entry.cuda
            except InvalidVersion:  # pragma: no cover - data file is curated
                continue
        return best

    def covered_versions(self) -> list[str]:
        return [entry.cuda for entry in self._entries]


@lru_cache(maxsize=1)
def load_matrix() -> CudaMatrix:
    payload = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    entries = [
        MatrixEntry(
            cuda=str(item["cuda"]),
            windows_min_driver=str(item["windows_min_driver"]),
            linux_min_driver=str(item["linux_min_driver"]),
        )
        for item in payload["entries"]
    ]
    return CudaMatrix(entries)


@lru_cache(maxsize=1)
def load_rules() -> list[dict[str, Any]]:
    payload = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    return list(payload["rules"])


@dataclass(frozen=True)
class PackageCudaRequirement:
    """A package whose *build* pins a CUDA runtime (independent of the project)."""

    name: str
    min_version: str
    cuda: str
    note: str
    source_name: str
    source_url: str


@lru_cache(maxsize=1)
def load_package_cuda_requirements() -> list[PackageCudaRequirement]:
    payload = json.loads(PACKAGE_CUDA_PATH.read_text(encoding="utf-8"))
    entries: list[PackageCudaRequirement] = []
    for item in payload["packages"]:
        source = item.get("source") or {}
        entries.append(
            PackageCudaRequirement(
                name=str(item["name"]),
                min_version=str(item["min_version"]),
                cuda=str(item["cuda"]),
                note=str(item.get("note", "")),
                source_name=str(source.get("name", "")),
                source_url=str(source.get("url", "")),
            )
        )
    return entries
