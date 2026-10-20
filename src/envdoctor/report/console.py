"""Console rendering (DEVELOPMENT.md §13.1): banner, tables, fixes, artifacts."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from envdoctor.domain.enums import CheckpointState, Overall, VerdictState
from envdoctor.domain.models import Report

VERDICT_DISPLAY: dict[VerdictState, tuple[str, str, str]] = {
    VerdictState.OK: ("✓", "OK", "green"),
    VerdictState.WARN: ("⚠", "WARN", "yellow"),
    VerdictState.BLOCKED: ("✗", "BLOCKED", "bold red"),
    VerdictState.UNKNOWN: ("?", "UNKNOWN", "magenta"),
    VerdictState.NOT_APPLICABLE: ("–", "N/A", "dim"),
}

VERDICT_ASCII: dict[VerdictState, str] = {
    VerdictState.OK: "OK",
    VerdictState.WARN: "WARN",
    VerdictState.BLOCKED: "NO",
    VerdictState.UNKNOWN: "??",
    VerdictState.NOT_APPLICABLE: "--",
}

CHECKPOINT_DISPLAY: dict[CheckpointState, tuple[str, str]] = {
    CheckpointState.PASS: ("✓", "green"),
    CheckpointState.FAIL: ("✗", "bold red"),
    CheckpointState.SKIP: ("•", "yellow"),
    CheckpointState.ERROR: ("!", "bold red"),
}

CHECKPOINT_ASCII: dict[CheckpointState, str] = {
    CheckpointState.PASS: "PASS",
    CheckpointState.FAIL: "FAIL",
    CheckpointState.SKIP: "SKIP",
    CheckpointState.ERROR: "ERR",
}

OVERALL_DISPLAY: dict[Overall, tuple[str, str, str]] = {
    Overall.RUNS: ("✅", "RUNS", "bold green"),
    Overall.CANNOT_RUN: ("❌", "CANNOT RUN", "bold red"),
    Overall.UNKNOWN: ("❓", "UNKNOWN", "bold yellow"),
}

OVERALL_ASCII: dict[Overall, str] = {
    Overall.RUNS: "[OK]",
    Overall.CANNOT_RUN: "[NO]",
    Overall.UNKNOWN: "[??]",
}


def _unicode_ok(console: Console) -> bool:
    encoding = getattr(getattr(console, "file", None), "encoding", None) or "utf-8"
    try:
        "✓⚠✗✅".encode(encoding)
        return True
    except (LookupError, UnicodeEncodeError):
        return False


def _verdict_table(verdicts, unicode_ok: bool) -> Table:
    table = Table(title="判定明细", title_justify="left", show_edge=False, pad_edge=False)
    table.add_column("状态", width=10)
    table.add_column("方面", width=10)
    table.add_column("结论")
    for verdict in verdicts:
        glyph, text, verdict_style = VERDICT_DISPLAY[verdict.state]
        if not unicode_ok:
            glyph, text = VERDICT_ASCII[verdict.state], ""
        cell = f"[{verdict_style}]{glyph} {text}[/]" if text else f"[{verdict_style}]{glyph}[/]"
        table.add_row(cell, f"[dim]{verdict.aspect.value}[/]", f"{verdict.title}\n[dim]{verdict.summary}[/]")
    return table


def render_verdicts(console: Console, verdicts) -> None:
    console.print(_verdict_table(verdicts, _unicode_ok(console)))


def render_report(
    console: Console,
    report: Report,
    *,
    artifacts: dict[str, str] | None = None,
    verify_note: str | None = None,
) -> None:
    unicode_ok = _unicode_ok(console)

    icon, label, style = OVERALL_DISPLAY[report.overall]
    if not unicode_ok:
        icon = OVERALL_ASCII[report.overall]
    console.print()
    console.print(
        Panel(
            f"[{style}]{icon} {label}[/]\n{report.overall_reason}",
            title=f"envdoctor · {report.target.original}",
            expand=False,
        )
    )

    console.print(
        f"[dim]目标[/] {report.target.kind.value} {report.target.path}"
        + (f" [dim]commit[/] {report.target.commit[:12]}" if report.target.commit else "")
    )
    console.print(
        f"[dim]项目[/] Python {report.scan.python.requires or '未声明'} · "
        f"ML 栈 {', '.join(item.name for item in report.scan.ml_stack) or '无'} · "
        f"CUDA {report.scan.cuda.requested_runtime or '未声明'} · "
        f"GPU 需求 {report.scan.cuda.gpu_required_strength.value}"
    )
    probe = report.probe
    if probe.gpu.present:
        device_names = ", ".join(device.name for device in probe.gpu.devices) or "未知设备"
        gpu_line = (
            f"{device_names}（驱动 {probe.gpu.driver_version or '未知'}，CUDA ≤ {probe.gpu.driver_max_cuda or '未知'}）"
        )
    else:
        gpu_line = f"未检测到（{probe.gpu.reason or '原因未知'}）"
    console.print(
        f"[dim]机器[/] {probe.os.name} {probe.os.release} · Python {probe.python.version} · "
        f"uv {probe.tooling.uv or '无'} · GPU {gpu_line}"
    )

    if verify_note:
        console.print(f"[yellow]真实验证未执行：[/]{verify_note}")

    console.print()
    console.print(_verdict_table(report.verdicts, unicode_ok))

    if report.checkpoints:
        checkpoints_table = Table(title="真实验证检查点", title_justify="left", show_edge=False, pad_edge=False)
        checkpoints_table.add_column("状态", width=10)
        checkpoints_table.add_column("步骤")
        checkpoints_table.add_column("耗时", width=8, justify="right")
        checkpoints_table.add_column("说明")
        for checkpoint in report.checkpoints:
            glyph, checkpoint_style = CHECKPOINT_DISPLAY[checkpoint.state]
            if not unicode_ok:
                glyph = CHECKPOINT_ASCII[checkpoint.state]
            seconds = f"{checkpoint.seconds:.1f}s" if checkpoint.seconds else "—"
            note = checkpoint.skip_reason or checkpoint.detail or "—"
            checkpoints_table.add_row(f"[{checkpoint_style}]{glyph}[/]", checkpoint.title, seconds, f"[dim]{note}[/]")
        console.print()
        console.print(checkpoints_table)

    fixes = [
        verdict
        for verdict in report.verdicts
        if verdict.fix and verdict.state in {VerdictState.BLOCKED, VerdictState.WARN}
    ]
    if fixes:
        console.print()
        console.print("[bold]修复建议[/]")
        for index, verdict in enumerate(fixes, start=1):
            assert verdict.fix is not None
            console.print(f"  {index}. [bold]{verdict.title}[/] → {verdict.fix.text}")
            if verdict.fix.command:
                console.print(f"     [dim]{verdict.fix.command}[/]")

    if report.explain and report.explain.text:
        console.print()
        console.print(f"[bold]AI 解释[/] [dim]({report.explain.model})[/]")
        console.print(f"  {report.explain.text}")

    if artifacts:
        console.print()
        for name, value in artifacts.items():
            console.print(f"[dim]{name}[/] {value}")
