"""Run/workdir layout per DEVELOPMENT.md §7."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DEFAULT_WORKDIR_NAME = ".envdoctor"


def make_run_id(now: datetime | None = None) -> str:
    moment = now or datetime.now()
    return f"{moment:%Y%m%d-%H%M%S}-{secrets.token_hex(4)}"


def resolve_workdir(explicit: str | None) -> Path:
    root = Path(explicit) if explicit else Path.cwd() / DEFAULT_WORKDIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


@dataclass
class RunLayout:
    dir: Path

    def __post_init__(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)

    @property
    def scan_path(self) -> Path:
        return self.dir / "scan.json"

    @property
    def probe_path(self) -> Path:
        return self.dir / "probe.json"

    @property
    def verdicts_path(self) -> Path:
        return self.dir / "verdicts.json"

    @property
    def verify_path(self) -> Path:
        return self.dir / "verify.json"

    @property
    def requirements_path(self) -> Path:
        return self.dir / "verify-requirements.txt"

    @property
    def report_json_path(self) -> Path:
        return self.dir / "report.json"

    @property
    def report_html_path(self) -> Path:
        return self.dir / "report.html"

    @property
    def interrupted_path(self) -> Path:
        return self.dir / "interrupted"

    @property
    def logs_dir(self) -> Path:
        path = self.dir / "logs"
        path.mkdir(exist_ok=True)
        return path

    def log_path(self, checkpoint_id: str) -> Path:
        return self.logs_dir / f"{checkpoint_id}.log"


@dataclass
class WorkLayout:
    root: Path

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def runs_dir(self) -> Path:
        path = self.root / "runs"
        path.mkdir(exist_ok=True)
        return path

    @property
    def cache_repos_dir(self) -> Path:
        path = self.root / "cache" / "repos"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def new_run(self, run_id: str) -> RunLayout:
        return RunLayout(self.runs_dir / run_id)
