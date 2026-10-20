"""Web console for envdoctor (DEVELOPMENT.md §26).

A local, single-user browser UI over the same pipeline the CLI uses:
scan → probe → analyze → (optional) verify → report.

The module is import-safe: nothing starts listening until
:func:`envdoctor.web.server.run_console` is called.
"""

from __future__ import annotations

from envdoctor.web.app import WebApp
from envdoctor.web.jobs import Job, JobManager
from envdoctor.web.server import build_app, run_console

__all__ = ["Job", "JobManager", "WebApp", "build_app", "run_console"]
