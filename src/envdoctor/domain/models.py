"""Pydantic models: the data contract between pipeline stages.

Schemas are exported to ``docs/schemas/`` by ``scripts/export_schemas.py``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from envdoctor.domain.enums import (
    Aspect,
    CheckpointState,
    Confidence,
    GpuSource,
    MlRole,
    Overall,
    RequirementStrength,
    TargetKind,
    VerdictState,
)

SCHEMA_VERSION = "1.0"


class Note(BaseModel):
    """A non-fatal observation attached to scan/probe results."""

    code: str
    message: str


# --- scan --------------------------------------------------------------------


class Target(BaseModel):
    kind: TargetKind
    original: str
    path: str
    commit: str | None = None
    cloned: bool = False


class PythonRequirement(BaseModel):
    requires: str | None = None
    source: str | None = None


class DependencyFile(BaseModel):
    kind: str
    path: str
    index_urls: list[str] = Field(default_factory=list)
    find_links: list[str] = Field(default_factory=list)
    note: str | None = None


class Dependency(BaseModel):
    name: str
    specifier: str | None = None
    extras: list[str] = Field(default_factory=list)
    marker: str | None = None
    source: str
    group: str = "default"


class MlStackItem(BaseModel):
    name: str
    role: MlRole
    specifier: str | None = None
    notes: list[str] = Field(default_factory=list)


class CudaHints(BaseModel):
    requested_runtime: str | None = None
    evidence: list[str] = Field(default_factory=list)
    gpu_required_strength: RequirementStrength = RequirementStrength.NONE


class ScanResult(BaseModel):
    schema_version: str = SCHEMA_VERSION
    target: Target
    python: PythonRequirement = Field(default_factory=PythonRequirement)
    package_managers: list[str] = Field(default_factory=list)
    dependency_files: list[DependencyFile] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    ml_stack: list[MlStackItem] = Field(default_factory=list)
    cuda: CudaHints = Field(default_factory=CudaHints)
    entrypoints: list[str] = Field(default_factory=list)
    warnings: list[Note] = Field(default_factory=list)


# --- probe -------------------------------------------------------------------


class OsInfo(BaseModel):
    name: str
    release: str
    arch: str


class CurrentPython(BaseModel):
    version: str
    executable: str
    console_encoding: str | None = None


class Interpreter(BaseModel):
    version: str
    path: str
    source: str


class Tooling(BaseModel):
    uv: str | None = None
    pip: str | None = None
    git: str | None = None
    conda: str | None = None


class GpuDevice(BaseModel):
    index: int
    name: str
    vram_bytes: int | None = None
    compute_capability: str | None = None


class GpuInfo(BaseModel):
    present: bool = False
    source: GpuSource = GpuSource.NONE
    reason: str | None = None
    driver_version: str | None = None
    driver_max_cuda: str | None = None
    devices: list[GpuDevice] = Field(default_factory=list)


class MachineProfile(BaseModel):
    schema_version: str = SCHEMA_VERSION
    os: OsInfo
    python: CurrentPython
    interpreters: list[Interpreter] = Field(default_factory=list)
    tooling: Tooling = Field(default_factory=Tooling)
    gpu: GpuInfo = Field(default_factory=GpuInfo)
    memory_bytes: int | None = None
    temp_disk_free_bytes: int | None = None
    collected_at: str
    warnings: list[Note] = Field(default_factory=list)


# --- compat ------------------------------------------------------------------


class Evidence(BaseModel):
    kind: str
    detail: str
    source: str | None = None


class Fix(BaseModel):
    kind: str = "instruction"
    text: str
    command: str | None = None


class Verdict(BaseModel):
    id: str
    aspect: Aspect
    state: VerdictState
    title: str
    summary: str
    evidence: list[Evidence] = Field(default_factory=list)
    fix: Fix | None = None
    rule_id: str | None = None
    confidence: Confidence = Confidence.HIGH


# --- verify ------------------------------------------------------------------


class Checkpoint(BaseModel):
    id: str
    title: str
    state: CheckpointState
    seconds: float = 0.0
    detail: str | None = None
    log_path: str | None = None
    skip_reason: str | None = None


# --- report ------------------------------------------------------------------


class ToolInfo(BaseModel):
    name: str = "envdoctor"
    version: str


class Durations(BaseModel):
    scan_s: float = 0.0
    probe_s: float = 0.0
    analyze_s: float = 0.0
    verify_s: float = 0.0
    total_s: float = 0.0


class ExplainResult(BaseModel):
    model: str | None = None
    text: str | None = None
    error: str | None = None


class Report(BaseModel):
    schema_version: str = SCHEMA_VERSION
    tool: ToolInfo
    run_id: str
    generated_at: str
    overall: Overall
    overall_reason: str
    verify_performed: bool
    target: Target
    scan: ScanResult
    probe: MachineProfile
    verdicts: list[Verdict] = Field(default_factory=list)
    checkpoints: list[Checkpoint] = Field(default_factory=list)
    explain: ExplainResult | None = None
    durations: Durations = Field(default_factory=Durations)
