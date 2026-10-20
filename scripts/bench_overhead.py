"""Measure probe / scan / analyze / report overhead against DEVELOPMENT.md §17.

Usage:  python scripts/bench_overhead.py
Exit 1 when a measurement exceeds its target (2x grace on CI runners).
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from envdoctor.compat.engine import analyze, decide_overall  # noqa: E402
from envdoctor.domain.models import Durations  # noqa: E402
from envdoctor.paths import WorkLayout  # noqa: E402
from envdoctor.probe.machine import probe_machine  # noqa: E402
from envdoctor.report.writer import build_report, write_html_report, write_json_report  # noqa: E402
from envdoctor.scan.scanner import scan_target  # noqa: E402

TARGETS = {
    "probe": 2.5,
    "scan (torch-cu121 fixture)": 1.0,
    "scan (this repo)": 1.0,
    "analyze": 0.5,
    "report (json+html)": 0.5,
}

REPEATS = 3


def best_of(action, repeats: int = REPEATS) -> tuple[float, object]:
    best = float("inf")
    result = None
    for _ in range(repeats):
        started = time.perf_counter()
        result = action()
        best = min(best, time.perf_counter() - started)
    return best, result


def main() -> int:
    grace = 2.0 if os.environ.get("CI") else 1.0
    workdir = Path(tempfile.mkdtemp(prefix="envdoctor-bench-"))
    work = WorkLayout(workdir / ".envdoctor")

    probe_seconds, profile = best_of(lambda: probe_machine())
    fixtures = ROOT / "tests" / "fixtures" / "projects"
    scan_seconds, scan_result = best_of(lambda: scan_target(str(fixtures / "torch-cu121"), work=work))
    repo_seconds, _ = best_of(lambda: scan_target(str(ROOT), work=work), repeats=1)
    analyze_seconds, verdicts = best_of(lambda: analyze(scan_result, profile))

    def make_report_files() -> None:
        overall, reason = decide_overall(verdicts, [])
        report = build_report(
            run_id="bench",
            scan=scan_result,
            probe=profile,
            verdicts=verdicts,
            checkpoints=[],
            overall=overall,
            overall_reason=reason,
            verify_performed=False,
            explain=None,
            durations=Durations(),
        )
        write_json_report(report, workdir / "report.json")
        write_html_report(report, workdir / "report.html")

    report_seconds, _ = best_of(make_report_files)

    measurements = {
        "probe": probe_seconds,
        "scan (torch-cu121 fixture)": scan_seconds,
        "scan (this repo)": repo_seconds,
        "analyze": analyze_seconds,
        "report (json+html)": report_seconds,
    }
    print(f"{'operation':<28} {'actual':>8} {'target':>8}  verdict")
    failures = 0
    for name, actual in measurements.items():
        target = TARGETS[name]
        ok = actual <= target * grace
        failures += 0 if ok else 1
        print(f"{name:<28} {actual:>7.3f}s {target:>7.1f}s  {'PASS' if ok else 'FAIL'}")
    import shutil

    shutil.rmtree(workdir, ignore_errors=True)
    if failures:
        print(f"\n{failures} 项超过目标（grace x{grace:g}）")
        return 1
    print("\nall measurements within targets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
