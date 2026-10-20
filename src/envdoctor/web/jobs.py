"""Background jobs for the web console (DEVELOPMENT.md §26).

A job is a unit of work the browser can start and follow: a static analysis
(``scan`` → ``probe`` → ``analyze``) or a real verification (install + smoke).
Jobs run on background threads so the HTTP handler never blocks.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

MAX_JOBS = 100


@dataclass
class Job:
    """One unit of work, observable from the browser."""

    id: str
    kind: str
    state: str = "queued"  # queued | running | done | failed
    phase: str = ""
    target: str | None = None
    run_id: str | None = None
    consent: bool = False
    refresh: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    logs: list[str] = field(default_factory=list)
    error: str | None = None
    result: dict[str, Any] = field(default_factory=dict)

    def log(self, message: str) -> None:
        """Append a log line (thread-safe enough for a single writer + readers)."""
        stamp = time.strftime("%H:%M:%S")
        self.logs.append(f"[{stamp}] {message}")
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "state": self.state,
            "phase": self.phase,
            "target": self.target,
            "run_id": self.run_id,
            "consent": self.consent,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "logs": list(self.logs[-400:]),
            "error": self.error,
            "result": dict(self.result),
        }


class JobManager:
    """Thread-backed job registry.

    Deliberately tiny: a dict plus a thread per job. envdoctor's web console is
    a single-user local tool, so there is no queue, no persistence and no
    cross-process coordination.
    """

    def __init__(self, *, max_jobs: int = MAX_JOBS) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._max_jobs = max_jobs

    def submit(self, kind: str, worker: Callable[[Job], dict[str, Any]], **meta: Any) -> Job:
        job = Job(id=secrets.token_hex(8), kind=kind, **meta)
        with self._lock:
            self._jobs[job.id] = job
            self._prune_locked()
        thread = threading.Thread(target=self._run, args=(job, worker), daemon=True)
        thread.start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True)

    def _prune_locked(self) -> None:
        if len(self._jobs) <= self._max_jobs:
            return
        for job_id, job in sorted(self._jobs.items(), key=lambda item: item[1].created_at):
            if len(self._jobs) <= self._max_jobs:
                break
            if job.state in {"done", "failed"}:
                self._jobs.pop(job_id, None)

    @staticmethod
    def _run(job: Job, worker: Callable[[Job], dict[str, Any]]) -> None:
        job.state = "running"
        job.updated_at = time.time()
        try:
            job.result = worker(job)
            job.state = "done"
            job.phase = "完成"
        except Exception as exc:
            job.state = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.updated_at = time.time()
