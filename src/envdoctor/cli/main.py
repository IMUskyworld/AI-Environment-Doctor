"""CLI wiring (DEVELOPMENT.md §4): commands, gate, exit codes, interrupt safety."""

from __future__ import annotations

import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console

from envdoctor import __version__
from envdoctor.compat.engine import analyze, decide_overall
from envdoctor.config import load_config
from envdoctor.domain.enums import CheckpointState, Overall, VerdictState
from envdoctor.domain.models import Checkpoint, Durations
from envdoctor.errors import EnvDoctorError, describe
from envdoctor.explain.llm import explain_report
from envdoctor.paths import WorkLayout, make_run_id, resolve_workdir
from envdoctor.probe.machine import probe_machine
from envdoctor.report.console import render_report, render_verdicts
from envdoctor.report.writer import build_report, dump_json, write_html_report, write_json_report
from envdoctor.scan.scanner import scan_target
from envdoctor.verify.planner import VerifyOptions, build_plan
from envdoctor.verify.runner import VerifyRunner

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="envdoctor — 这个 AI 项目能不能在我的机器上跑？用证据回答。",
)
console = Console()
err_console = Console(stderr=True)

EXIT_RUNS = 0
EXIT_CANNOT_RUN = 1
EXIT_UNKNOWN = 2
EXIT_INTERNAL = 3
EXIT_INTERRUPTED = 130

OVERALL_EXIT = {
    Overall.RUNS: EXIT_RUNS,
    Overall.CANNOT_RUN: EXIT_CANNOT_RUN,
    Overall.UNKNOWN: EXIT_UNKNOWN,
}


def _reconfigure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


@app.callback()
def _bootstrap() -> None:
    # Runs for every entry point (console script, dev shim, tests) before any command:
    # cp936 consoles otherwise cannot encode the Chinese output (DEVELOPMENT.md §24).
    _reconfigure_console()


def _fail(exc: BaseException) -> typer.Exit:
    payload = describe(exc)
    err_console.print(f"[red][{payload['code']}][/] {payload['message']}")
    if payload.get("details"):
        err_console.print(f"[dim]{payload['details']}[/]")
    return typer.Exit(EXIT_INTERNAL)


def _print_warnings(warnings) -> None:
    for note in warnings:
        console.print(f"[dim]{note.code}：{note.message}[/]")


def _print_machine_summary(profile) -> None:
    gpu = profile.gpu
    if gpu.present:
        names = ", ".join(device.name for device in gpu.devices) or "未知设备"
        gpu_line = f"{names}（驱动 {gpu.driver_version or '未知'}，CUDA ≤ {gpu.driver_max_cuda or '未知'}）"
    else:
        gpu_line = f"未检测到（{gpu.reason or '原因未知'}）"
    console.print(f"[bold]系统[/] {profile.os.name} {profile.os.release} {profile.os.arch}")
    console.print(f"[bold]Python[/] {profile.python.version} [dim]{profile.python.executable}[/]")
    tooling = (
        f"uv {profile.tooling.uv or '—'} · git {profile.tooling.git or '—'} · conda {profile.tooling.conda or '—'}"
    )
    console.print(f"[bold]工具链[/] {tooling}")
    console.print(f"[bold]GPU[/] {gpu_line}")


# --- commands ------------------------------------------------------------------


@app.command()
def version() -> None:
    """打印版本信息。"""
    console.print(f"envdoctor {__version__}")


@app.command()
def probe(
    json_out: Path | None = typer.Option(None, "--json", help="另存 JSON 到指定路径"),
    workdir: Path | None = typer.Option(None, "--workdir", help="运行目录根（默认 ./.envdoctor）"),
) -> None:
    """只探查本机：GPU / 解释器 / 工具链 / 资源。"""
    try:
        work = WorkLayout(resolve_workdir(str(workdir) if workdir else None))
        run = work.new_run(make_run_id())
        profile = probe_machine()
        dump_json(profile.model_dump(mode="json"), run.probe_path)
        if json_out:
            dump_json(profile.model_dump(mode="json"), json_out)
        _print_machine_summary(profile)
        _print_warnings(profile.warnings)
        console.print(f"[dim]产物[/] {run.probe_path}")
    except typer.Exit:
        raise
    except EnvDoctorError as exc:
        raise _fail(exc) from None
    except Exception as exc:
        raise _fail(exc) from None
    raise typer.Exit(EXIT_RUNS)


@app.command()
def scan(
    target: str = typer.Argument(..., help="本地路径或 git URL"),
    json_out: Path | None = typer.Option(None, "--json", help="另存 JSON 到指定路径"),
    workdir: Path | None = typer.Option(None, "--workdir"),
    refresh: bool = typer.Option(False, "--refresh", help="重新克隆 URL 目标"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="输出扫描提示"),
) -> None:
    """只扫项目：依赖 / Python 约束 / ML 栈 / CUDA 线索。"""
    try:
        work = WorkLayout(resolve_workdir(str(workdir) if workdir else None))
        run = work.new_run(make_run_id())
        result = scan_target(target, work=work, refresh=refresh)
        dump_json(result.model_dump(mode="json"), run.scan_path)
        if json_out:
            dump_json(result.model_dump(mode="json"), json_out)
        console.print(f"[bold]目标[/] {result.target.kind.value} {result.target.path}")
        console.print(
            f"[bold]Python[/] {result.python.requires or '未声明'}"
            + (f" [dim]({result.python.source})[/]" if result.python.source else "")
        )
        console.print(f"[bold]包管理器[/] {', '.join(result.package_managers) or '未检测到'}")
        console.print(
            "[bold]ML 栈[/] "
            + ("、".join(f"{item.name}{item.specifier or ''}" for item in result.ml_stack) or "未检测到")
        )
        console.print(
            f"[bold]CUDA[/] {result.cuda.requested_runtime or '未显式声明'}"
            f" · GPU 需求 {result.cuda.gpu_required_strength.value}"
        )
        console.print(
            f"[bold]依赖[/] {len(result.dependencies)} 条 · [bold]入口[/] {', '.join(result.entrypoints) or '—'}"
        )
        if verbose:
            _print_warnings(result.warnings)
        else:
            console.print(f"[dim]提示 {len(result.warnings)} 条（-v 查看）[/]")
        console.print(f"[dim]产物[/] {run.scan_path}")
    except typer.Exit:
        raise
    except EnvDoctorError as exc:
        raise _fail(exc) from None
    except Exception as exc:
        raise _fail(exc) from None
    raise typer.Exit(EXIT_RUNS)


@app.command(name="analyze")
def analyze_command(
    target: str = typer.Argument(..., help="本地路径或 git URL"),
    json_out: Path | None = typer.Option(None, "--json", help="另存 JSON 到指定路径"),
    workdir: Path | None = typer.Option(None, "--workdir"),
    refresh: bool = typer.Option(False, "--refresh"),
) -> None:
    """scan + probe + 规则引擎：输出逐项判定（不安装任何东西）。"""
    try:
        work = WorkLayout(resolve_workdir(str(workdir) if workdir else None))
        run = work.new_run(make_run_id())
        result = scan_target(target, work=work, refresh=refresh)
        dump_json(result.model_dump(mode="json"), run.scan_path)
        profile = probe_machine()
        dump_json(profile.model_dump(mode="json"), run.probe_path)
        verdicts = analyze(result, profile)
        dump_json([verdict.model_dump(mode="json") for verdict in verdicts], run.verdicts_path)
        if json_out:
            dump_json([verdict.model_dump(mode="json") for verdict in verdicts], json_out)
        _print_machine_summary(profile)
        console.print()
        render_verdicts(console, verdicts)
        blocked = [verdict for verdict in verdicts if verdict.state == VerdictState.BLOCKED]
        console.print(
            f"[dim]判定 {len(verdicts)} 条："
            f"{sum(1 for v in verdicts if v.state == VerdictState.OK)} ok / "
            f"{sum(1 for v in verdicts if v.state == VerdictState.WARN)} warn / "
            f"{len(blocked)} blocked；完整证据见 {run.verdicts_path}[/]"
        )
    except typer.Exit:
        raise
    except EnvDoctorError as exc:
        raise _fail(exc) from None
    except Exception as exc:
        raise _fail(exc) from None
    raise typer.Exit(EXIT_RUNS)


def _confirm_gate(plan, verdicts, yes: bool) -> tuple[bool, str]:
    if yes:
        return True, ""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False, "非交互终端且未提供 --yes，已跳过真实验证（补上 --yes 或在本机终端重跑）"
    console.print()
    blocked = [verdict for verdict in verdicts if verdict.state == VerdictState.BLOCKED]
    if blocked:
        console.print(
            f"[yellow]注意：存在 {len(blocked)} 条阻断性判定（{blocked[0].title}）；"
            "实测大概率失败且可能消耗大量下载流量。[/]"
        )
    console.print("[bold]将要执行的真实验证计划[/]")
    assert plan.interpreter is not None
    console.print(f"  解释器    Python {plan.interpreter.version}  [dim]{plan.interpreter.path}[/]")
    console.print(f"  安装源    {plan.primary_index or 'PyPI 默认'}")
    console.print(f"  依赖      {len(plan.requirement_lines)} 个包（来自 {plan.requirements_source}）")
    console.print("  步骤      venv → install → imports → device → minimal-model")
    console.print("[yellow]⚠ 安装会执行第三方包的构建脚本，并消耗网络流量；环境为一次性临时目录，结束后删除。[/]")
    answer = typer.prompt("继续？[y/N]", default="n", show_default=False)
    if answer.strip().lower() not in {"y", "yes"}:
        return False, "已按你的选择跳过真实验证"
    return True, ""


@app.command()
def doctor(
    target: str = typer.Argument(..., help="本地路径或 git URL"),
    python_version: str | None = typer.Option(None, "--python", help="验证用解释器版本，例如 3.12"),
    no_verify: bool = typer.Option(False, "--no-verify", help="只做静态分析，跳过真实验证"),
    yes: bool = typer.Option(False, "--yes", help="跳过交互确认（CI / 批处理）"),
    keep_env: bool = typer.Option(False, "--keep-env", help="保留临时验证环境"),
    explain: bool = typer.Option(False, "--explain", help="附加 AI 解释（需配置 LLM 端点）"),
    index_url: str | None = typer.Option(None, "--index-url", help="验证环境安装源（覆盖项目配置）"),
    extra_index_url: list[str] | None = typer.Option(None, "--extra-index-url", help="追加安装源，可重复"),
    smoke_cmd: list[str] | None = typer.Option(None, "--smoke-cmd", help="额外冒烟命令，可重复"),
    timeout_s: float | None = typer.Option(None, "--timeout", help="安装步骤超时（秒；默认 600 或配置值）"),
    config_path: str | None = typer.Option(
        None, "--config", help="配置文件路径（默认 ./envdoctor.toml，其次用户级配置）"
    ),
    json_out: Path | None = typer.Option(None, "--json", help="另存 report.json 到指定路径"),
    html_out: Path | None = typer.Option(None, "--html", help="另存 report.html 到指定路径"),
    workdir: Path | None = typer.Option(None, "--workdir"),
    refresh: bool = typer.Option(False, "--refresh"),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="只输出结论"),
) -> None:
    """完整流水线：scan → probe → analyze → [确认] → verify → report。"""
    started = time.perf_counter()
    work = WorkLayout(resolve_workdir(str(workdir) if workdir else None))
    run = work.new_run(make_run_id())
    durations = Durations()
    try:
        config = load_config(config_path)

        scan_started = time.perf_counter()
        scan_result = scan_target(target, work=work, refresh=refresh)
        durations.scan_s = round(time.perf_counter() - scan_started, 3)
        dump_json(scan_result.model_dump(mode="json"), run.scan_path)

        probe_started = time.perf_counter()
        profile = probe_machine()
        durations.probe_s = round(time.perf_counter() - probe_started, 3)
        dump_json(profile.model_dump(mode="json"), run.probe_path)
        if verbose:
            _print_warnings(profile.warnings)

        analyze_started = time.perf_counter()
        verdicts = analyze(scan_result, profile)
        durations.analyze_s = round(time.perf_counter() - analyze_started, 3)
        dump_json([verdict.model_dump(mode="json") for verdict in verdicts], run.verdicts_path)

        options = VerifyOptions(
            python_version=python_version,
            index_url=index_url,
            extra_index_urls=list(extra_index_url or []),
            timeout_s=timeout_s if timeout_s is not None else float(config.defaults.timeout_s),
            keep_env=keep_env or config.defaults.keep_env,
            smoke_cmds=list(smoke_cmd or []),
        )
        plan = build_plan(scan_result, profile, options, config)
        for warning in plan.warnings:
            if verbose:
                console.print(f"[dim]{warning}[/]")

        checkpoints: list[Checkpoint] = []
        verify_note: str | None = None
        verify_performed = False
        if no_verify:
            verify_note = "已按 --no-verify 跳过真实验证（仅静态分析）"
        elif plan.interpreter is None:
            verify_note = plan.interpreter_reason
        elif not plan.installable:
            verify_note = "项目未声明任何可安装的 pip 依赖"
        else:
            proceed, gate_note = _confirm_gate(plan, verdicts, yes)
            if not proceed:
                verify_note = gate_note
            else:
                verify_started = time.perf_counter()
                runner = VerifyRunner(run=run, plan=plan, uv_path=shutil.which("uv"), echo=console.print)
                result = runner.run_checks()
                durations.verify_s = round(time.perf_counter() - verify_started, 3)
                if result.note:
                    verify_note = result.note
                if result.checkpoints:
                    checkpoints = result.checkpoints
                    dump_json(
                        [checkpoint.model_dump(mode="json") for checkpoint in checkpoints],
                        run.verify_path,
                    )
                    # 「真的验证过」= 至少有一个检查点不是 skip（uv 缺失/无依赖时全是 skip）
                    verify_performed = any(checkpoint.state != CheckpointState.SKIP for checkpoint in checkpoints)

        overall, overall_reason = decide_overall(verdicts, checkpoints, verify_note=verify_note)
        durations.total_s = round(time.perf_counter() - started, 3)
        report = build_report(
            run_id=run.dir.name,
            scan=scan_result,
            probe=profile,
            verdicts=verdicts,
            checkpoints=checkpoints,
            overall=overall,
            overall_reason=overall_reason,
            verify_performed=verify_performed,
            explain=None,
            durations=durations,
        )
        if explain:
            explain_result = explain_report(report, config)
            if explain_result.error:
                console.print(f"[yellow]AI 解释已跳过：[/]{explain_result.error}")
            report.explain = explain_result

        write_json_report(report, run.report_json_path)
        if json_out:
            write_json_report(report, json_out)
        write_html_report(report, run.report_html_path)
        if html_out:
            write_html_report(report, html_out)

        if quiet:
            label = {Overall.RUNS: "RUNS", Overall.CANNOT_RUN: "CANNOT RUN", Overall.UNKNOWN: "UNKNOWN"}[overall]
            console.print(f"{label}: {overall_reason}")
        else:
            render_report(
                console,
                report,
                artifacts={"run 目录": str(run.dir), "报告": str(run.report_html_path)},
                verify_note=verify_note,
            )
        raise typer.Exit(OVERALL_EXIT[overall])
    except KeyboardInterrupt:
        run.interrupted_path.write_text(datetime.now().astimezone().isoformat(timespec="seconds"), encoding="utf-8")
        err_console.print("[yellow]已中断（Ctrl+C）：临时环境已清理，部分结果保留在 run 目录。[/]")
        raise typer.Exit(EXIT_INTERRUPTED) from None
    except typer.Exit:
        raise
    except EnvDoctorError as exc:
        raise _fail(exc) from None
    except Exception as exc:
        raise _fail(exc) from None


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", "--host", help="监听地址（默认仅本机；0.0.0.0 需配合 --allow-remote）"),
    port: int = typer.Option(8765, "--port", help="监听端口（0 表示随机端口）"),
    workdir: Path | None = typer.Option(None, "--workdir", help="运行目录根（默认 ./.envdoctor，与 CLI 共用历史）"),
    config_path: str | None = typer.Option(
        None, "--config", help="配置文件路径（默认 ./envdoctor.toml，其次用户级配置）"
    ),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="启动后自动打开浏览器"),
    allow_remote: bool = typer.Option(False, "--allow-remote", help="允许非回环地址访问（危险：需自行承担风险）"),
) -> None:
    """Web 控制台：浏览器里选目标、看判定、跑实测验证。"""
    if allow_remote and host in {"127.0.0.1", "localhost"}:
        raise typer.BadParameter("--allow-remote 需要配合非回环 --host 使用")
    try:
        from envdoctor.web.server import run_console

        code = run_console(
            host=host,
            port=port,
            workdir=str(workdir) if workdir else None,
            config=load_config(config_path),
            open_browser=open_browser,
            allow_remote=allow_remote,
        )
    except typer.Exit:
        raise
    except EnvDoctorError as exc:
        raise _fail(exc) from None
    except Exception as exc:
        raise _fail(exc) from None
    raise typer.Exit(code)


def main() -> None:
    _reconfigure_console()
    try:
        app()
    except KeyboardInterrupt:  # pragma: no cover - interactive path
        err_console.print("[yellow]已中断（Ctrl+C）[/]")
        raise SystemExit(EXIT_INTERRUPTED) from None


if __name__ == "__main__":  # pragma: no cover
    main()
