"""Report assembly and serialization (DEVELOPMENT.md §13.2-13.3)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from envdoctor import __version__
from envdoctor.domain.enums import Overall
from envdoctor.domain.models import (
    Checkpoint,
    Durations,
    ExplainResult,
    MachineProfile,
    Report,
    ScanResult,
    ToolInfo,
    Verdict,
)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

VERDICT_STATE_CLASS = {
    "ok": "state-ok",
    "warn": "state-warn",
    "blocked": "state-blocked",
    "unknown": "state-unknown",
    "not_applicable": "state-na",
}

CHECKPOINT_STATE_CLASS = {
    "pass": "state-ok",
    "fail": "state-blocked",
    "skip": "state-warn",
    "error": "state-blocked",
}

OVERALL_CLASS = {
    Overall.RUNS: "state-ok",
    Overall.CANNOT_RUN: "state-blocked",
    Overall.UNKNOWN: "state-unknown",
}


def build_report(
    *,
    run_id: str,
    scan: ScanResult,
    probe: MachineProfile,
    verdicts: list[Verdict],
    checkpoints: list[Checkpoint],
    overall: Overall,
    overall_reason: str,
    verify_performed: bool,
    explain: ExplainResult | None,
    durations: Durations,
) -> Report:
    return Report(
        tool=ToolInfo(version=__version__),
        run_id=run_id,
        generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        overall=overall,
        overall_reason=overall_reason,
        verify_performed=verify_performed,
        target=scan.target,
        scan=scan,
        probe=probe,
        verdicts=verdicts,
        checkpoints=checkpoints,
        explain=explain,
        durations=durations,
    )


def dump_json(payload: object, path: Path) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def write_json_report(report: Report, path: Path) -> None:
    dump_json(report.model_dump(mode="json"), path)


def write_html_report(report: Report, path: Path) -> None:
    environment = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = environment.get_template("report.html.j2")
    html = template.render(
        report=report,
        verdict_state_class=VERDICT_STATE_CLASS,
        checkpoint_state_class=CHECKPOINT_STATE_CLASS,
        overall_class=OVERALL_CLASS,
    )
    path.write_text(html, encoding="utf-8", newline="\n")
