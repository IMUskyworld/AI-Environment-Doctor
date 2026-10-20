"""Enums shared across scan / probe / compat / verify / report."""

from __future__ import annotations

from enum import Enum


class TargetKind(str, Enum):
    LOCAL = "local"
    GIT = "git"


class RequirementStrength(str, Enum):
    """How strongly a project needs a GPU.

    ``hard`` — GPU-only signals (flash-attn, xformers, +cuXXX pins, nvidia-* deps ...).
    ``soft`` — device-agnostic frameworks (torch / tf / jax) that can fall back to CPU.
    ``none`` — nothing device-related found.
    """

    HARD = "hard"
    SOFT = "soft"
    NONE = "none"


class VerdictState(str, Enum):
    OK = "ok"
    WARN = "warn"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class Aspect(str, Enum):
    PYTHON = "python"
    PLATFORM = "platform"
    GPU = "gpu"
    CUDA = "cuda"
    DEPS = "deps"
    ECOSYSTEM = "ecosystem"
    VERIFY = "verify"


class Severity(str, Enum):
    BLOCKER = "blocker"
    WARNING = "warning"
    INFO = "info"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CheckpointState(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    ERROR = "error"


class Overall(str, Enum):
    RUNS = "runs"
    CANNOT_RUN = "cannot_run"
    UNKNOWN = "unknown"


class GpuSource(str, Enum):
    NVIDIA_SMI = "nvidia-smi"
    NVML = "nvml"
    NONE = "none"


class MlRole(str, Enum):
    CORE = "core"
    EXTENSION = "extension"
    LIBRARY = "library"
    TOOLING = "tooling"
