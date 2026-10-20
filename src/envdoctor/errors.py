"""Structured error model.

Every failure envdoctor reports carries a stable code so that terminals, JSON
artifacts and tests can all match on the same identifier instead of on message text.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# --- error codes (stable public identifiers, do not rename) -------------------
TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
TARGET_NOT_A_DIRECTORY = "TARGET_NOT_A_DIRECTORY"
GIT_UNAVAILABLE = "GIT_UNAVAILABLE"
GIT_CLONE_FAILED = "GIT_CLONE_FAILED"
GIT_CLONE_TIMEOUT = "GIT_CLONE_TIMEOUT"
GIT_COMMAND_TIMEOUT = "GIT_COMMAND_TIMEOUT"
PYPROJECT_PARSE_FAILED = "PYPROJECT_PARSE_FAILED"
REQUIREMENTS_FILE_UNREADABLE = "REQUIREMENTS_FILE_UNREADABLE"
YAML_PARSE_FAILED = "YAML_PARSE_FAILED"
PIPFILE_PARSE_FAILED = "PIPFILE_PARSE_FAILED"
CONFIG_INVALID = "CONFIG_INVALID"
WORKDIR_CREATE_FAILED = "WORKDIR_CREATE_FAILED"
RUN_ARTIFACT_WRITE_FAILED = "RUN_ARTIFACT_WRITE_FAILED"
VERIFY_UV_MISSING = "VERIFY_UV_MISSING"
VERIFY_NO_INSTALL_SPEC = "VERIFY_NO_INSTALL_SPEC"
VERIFY_NO_INTERPRETER = "VERIFY_NO_INTERPRETER"
VERIFY_VENV_FAILED = "VERIFY_VENV_FAILED"
VERIFY_INSTALL_FAILED = "VERIFY_INSTALL_FAILED"
VERIFY_INDEX_UNREACHABLE = "VERIFY_INDEX_UNREACHABLE"
VERIFY_STEP_TIMEOUT = "VERIFY_STEP_TIMEOUT"
VERIFY_SCRIPT_FAILED = "VERIFY_SCRIPT_FAILED"
VERIFY_CONFIRMATION_REQUIRED = "VERIFY_CONFIRMATION_REQUIRED"
REPORT_RENDER_FAILED = "REPORT_RENDER_FAILED"
LLM_DISABLED = "LLM_DISABLED"
LLM_REQUEST_FAILED = "LLM_REQUEST_FAILED"
LLM_OUTPUT_INVALID = "LLM_OUTPUT_INVALID"
INTERNAL_ERROR = "INTERNAL_ERROR"
UNKNOWN_ERROR = "UNKNOWN_ERROR"


class EnvDoctorError(Exception):
    """An error with a machine-readable code.

    ``details`` must never contain raw environment values, secrets or full file
    contents: it is rendered to terminals and embedded in reports.
    """

    code: str = UNKNOWN_ERROR
    component: str = "core"
    recoverable: bool = True

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        component: str | None = None,
        recoverable: bool | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if component is not None:
            self.component = component
        if recoverable is not None:
            self.recoverable = recoverable
        self.details: dict[str, Any] = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "component": self.component,
            "recoverable": self.recoverable,
            "details": self.details,
        }

    def __str__(self) -> str:  # pragma: no cover - convenience for tracebacks
        return f"[{self.code}] {self.message}"


class TargetError(EnvDoctorError):
    code = TARGET_NOT_FOUND
    component = "scan"


class GitError(EnvDoctorError):
    code = GIT_CLONE_FAILED
    component = "scan"


class ConfigError(EnvDoctorError):
    code = CONFIG_INVALID
    component = "config"
    recoverable = False


class VerifyError(EnvDoctorError):
    code = VERIFY_SCRIPT_FAILED
    component = "verify"


def describe(exc: BaseException, *, fallback_component: str = "core") -> dict[str, Any]:
    """Normalize any exception into the structured error shape used in JSON files."""
    if isinstance(exc, EnvDoctorError):
        return exc.to_dict()
    return {
        "code": getattr(exc, "code", INTERNAL_ERROR) if exc else INTERNAL_ERROR,
        "message": f"{type(exc).__name__}: {exc}",
        "component": fallback_component,
        "recoverable": False,
        "details": {"exception_type": type(exc).__name__},
    }
