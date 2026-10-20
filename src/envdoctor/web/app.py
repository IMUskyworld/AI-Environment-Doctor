"""Web console application logic (DEVELOPMENT.md §26).

Transport-agnostic on purpose: every route is a method that takes plain
arguments and returns a :class:`Response`. The HTTP layer in
:mod:`envdoctor.web.server` only translates sockets into these calls, which
keeps the whole API unit-testable without opening a port.
"""

from __future__ import annotations

import hmac
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from envdoctor.compat.engine import analyze, decide_overall
from envdoctor.config import EnvDoctorConfig
from envdoctor.domain.models import (
    Durations,
    MachineProfile,
    ScanResult,
    Verdict,
)
from envdoctor.paths import RunLayout, WorkLayout, make_run_id
from envdoctor.probe.machine import probe_machine
from envdoctor.report.writer import build_report, dump_json, write_html_report, write_json_report
from envdoctor.scan.scanner import scan_target
from envdoctor.verify.planner import VerifyOptions, build_plan
from envdoctor.verify.runner import VerifyRunner
from envdoctor.web.jobs import Job, JobManager

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
INDEX_FILE = ASSETS_DIR / "index.html"

JSON_TYPE = "application/json; charset=utf-8"
HTML_TYPE = "text/html; charset=utf-8"


@dataclass
class Response:
    status: int
    content_type: str
    body: bytes
    headers: dict[str, str] | None = None


def json_response(payload: Any, status: int = 200) -> Response:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    return Response(status=status, content_type=JSON_TYPE, body=body)


class WebApp:
    """The console's brain: routes, authorization, and the two background jobs."""

    def __init__(
        self,
        *,
        work: WorkLayout,
        token: str,
        config: EnvDoctorConfig | None = None,
    ) -> None:
        self._work = work
        self._token = token
        self._config = config or EnvDoctorConfig()
        self._jobs = JobManager()

    # -- authorization ---------------------------------------------------------

    @property
    def workdir(self) -> Path:
        """Root of the run/work directory tree (shared with the CLI)."""
        return self._work.root

    def authorized(self, token: str | None) -> bool:
        """Constant-time token check; the console is a single-user local tool."""
        if not token:
            return False
        return hmac.compare_digest(token, self._token)

    # -- routing ---------------------------------------------------------------

    def get(self, path: str, *, token: str | None) -> Response:
        if not self.authorized(token):
            return json_response({"error": "UNAUTHORIZED", "message": "缺少或错误的访问令牌"}, status=403)
        if path in {"/", "/index.html"}:
            return self._index_page()
        if path == "/api/machine":
            payload = probe_machine().model_dump(mode="json")
            payload["cwd"] = str(Path.cwd())
            payload["workdir"] = str(self._work.root)
            return json_response(payload)
        if path == "/api/runs":
            return json_response(self._list_runs())
        if path == "/api/jobs":
            return json_response([job.to_dict() for job in self._jobs.list()])
        if path.startswith("/api/jobs/"):
            job = self._jobs.get(path.rsplit("/", 1)[-1])
            if job is None:
                return json_response({"error": "JOB_NOT_FOUND"}, status=404)
            return json_response(self._job_payload(job))
        if path.startswith("/api/runs/"):
            return self._run_route(path)
        return json_response({"error": "NOT_FOUND", "path": path}, status=404)

    def post(self, path: str, *, body: dict[str, Any], token: str | None) -> Response:
        if not self.authorized(token):
            return json_response({"error": "UNAUTHORIZED", "message": "缺少或错误的访问令牌"}, status=403)
        if path != "/api/jobs":
            return json_response({"error": "NOT_FOUND", "path": path}, status=404)
        kind = str(body.get("kind", ""))
        if kind == "analyze":
            target = str(body.get("target", "")).strip()
            if not target:
                return json_response({"error": "TARGET_REQUIRED"}, status=400)
            job = self._jobs.submit(
                "analyze",
                self._run_analyze,
                target=target,
                refresh=bool(body.get("refresh", False)),
            )
            return json_response(job.to_dict(), status=202)
        if kind == "verify":
            run_id = str(body.get("run_id", "")).strip()
            if not run_id:
                return json_response({"error": "RUN_ID_REQUIRED"}, status=400)
            if not body.get("consent"):
                # 同 CLI 的知情同意：没有明确同意就不执行安装
                return json_response(
                    {"error": "CONSENT_REQUIRED", "message": "真实验证会安装第三方依赖并执行其构建脚本，需要显式同意"},
                    status=400,
                )
            if not (self._work.runs_dir / run_id).is_dir():
                return json_response({"error": "RUN_NOT_FOUND", "run_id": run_id}, status=404)
            job = self._jobs.submit("verify", self._run_verify, run_id=run_id, consent=True)
            return json_response(job.to_dict(), status=202)
        return json_response({"error": "UNKNOWN_JOB_KIND", "kind": kind}, status=400)

    def delete(self, path: str, *, token: str | None) -> Response:
        if not self.authorized(token):
            return json_response({"error": "UNAUTHORIZED"}, status=403)
        if not path.startswith("/api/runs/"):
            return json_response({"error": "NOT_FOUND", "path": path}, status=404)
        run_id = path[len("/api/runs/") :].strip("/")
        if run_id.endswith("/report.html"):
            run_id = run_id[: -len("/report.html")]
        run_dir = self._work.runs_dir / run_id
        if not run_dir.is_dir():
            return json_response({"error": "RUN_NOT_FOUND", "run_id": run_id}, status=404)
        shutil.rmtree(run_dir, ignore_errors=True)
        return json_response({"deleted": run_id})

    # -- pages & assets --------------------------------------------------------

    def _index_page(self) -> Response:
        html = INDEX_FILE.read_text(encoding="utf-8")
        html = html.replace("__ENVDOCTOR_TOKEN__", self._token)
        return Response(status=200, content_type=HTML_TYPE, body=html.encode("utf-8"))

    def _run_route(self, path: str) -> Response:
        rest = path[len("/api/runs/") :]
        if rest.endswith("/report.html"):
            run_id = rest[: -len("/report.html")]
            report_path = self._work.runs_dir / run_id / "report.html"
            if not report_path.is_file():
                return json_response({"error": "REPORT_NOT_FOUND", "run_id": run_id}, status=404)
            return Response(
                status=200,
                content_type=HTML_TYPE,
                body=report_path.read_bytes(),
            )
        run_id = rest
        report_path = self._work.runs_dir / run_id / "report.json"
        if not report_path.is_file():
            return json_response({"error": "REPORT_NOT_FOUND", "run_id": run_id}, status=404)
        return Response(
            status=200,
            content_type=JSON_TYPE,
            body=report_path.read_bytes(),
        )

    # -- run history -----------------------------------------------------------

    def _list_runs(self) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        if not self._work.runs_dir.is_dir():
            return runs
        for run_dir in sorted(self._work.runs_dir.iterdir(), reverse=True):
            if not run_dir.is_dir():
                continue
            summary: dict[str, Any] = {"run_id": run_dir.name, "path": str(run_dir)}
            report_path = run_dir / "report.json"
            if report_path.is_file():
                try:
                    payload = json.loads(report_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    payload = {}
                summary.update(
                    {
                        "overall": payload.get("overall"),
                        "overall_reason": payload.get("overall_reason"),
                        "target": (payload.get("target") or {}).get("original"),
                        "generated_at": payload.get("generated_at"),
                        "verdicts": len(payload.get("verdicts") or []),
                        "checkpoints": len(payload.get("checkpoints") or []),
                        "verify_performed": payload.get("verify_performed"),
                    }
                )
            else:
                scan_path = run_dir / "scan.json"
                if scan_path.is_file():
                    try:
                        payload = json.loads(scan_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        payload = {}
                    summary.update(
                        {
                            "overall": None,
                            "target": (payload.get("target") or {}).get("original"),
                            "verdicts": 0,
                            "checkpoints": 0,
                        }
                    )
            runs.append(summary)
        return runs

    # -- jobs ------------------------------------------------------------------

    def _job_payload(self, job: Job) -> dict[str, Any]:
        payload = job.to_dict()
        payload["live_logs"] = self._live_logs(job)
        return payload

    def _live_logs(self, job: Job) -> list[str]:
        """Tail the run's log files so the browser sees install progress live."""
        if job.kind != "verify" or not job.run_id:
            return []
        logs_dir = self._work.runs_dir / job.run_id / "logs"
        if not logs_dir.is_dir():
            return []
        lines: list[str] = []
        for log_file in sorted(logs_dir.glob("*.log")):
            try:
                text = log_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            tail = [line for line in text.strip().splitlines() if line.strip()]
            if tail:
                lines.append(f"── {log_file.stem} ──")
                lines.extend(f"  {line}" for line in tail[-12:])
        return lines

    def _run_analyze(self, job: Job) -> dict[str, Any]:
        assert job.target is not None
        job.phase = "扫描项目"
        job.log(f"扫描目标：{job.target}")
        scan = scan_target(job.target, work=self._work, refresh=job.refresh)

        job.phase = "探查机器"
        job.log("探查 GPU / 驱动 / 解释器 / 工具链")
        profile = probe_machine()

        job.phase = "规则引擎判定"
        verdicts = analyze(scan, profile)
        overall, reason = decide_overall(verdicts, [])
        job.log(f"判定 {len(verdicts)} 条：overall={overall.value}（{reason}）")

        job.phase = "生成报告"
        run = self._work.new_run(make_run_id())
        dump_json(scan.model_dump(mode="json"), run.scan_path)
        dump_json(profile.model_dump(mode="json"), run.probe_path)
        dump_json([verdict.model_dump(mode="json") for verdict in verdicts], run.verdicts_path)
        report = build_report(
            run_id=run.dir.name,
            scan=scan,
            probe=profile,
            verdicts=verdicts,
            checkpoints=[],
            overall=overall,
            overall_reason=reason,
            verify_performed=False,
            explain=None,
            durations=Durations(),
        )
        write_json_report(report, run.report_json_path)
        write_html_report(report, run.report_html_path)
        job.run_id = run.dir.name
        job.log(f"报告已生成：{run.report_html_path}")
        return {
            "run_id": run.dir.name,
            "overall": overall.value,
            "overall_reason": reason,
            "verdicts": len(verdicts),
            "report_url": f"/api/runs/{run.dir.name}/report.html",
        }

    def _run_verify(self, job: Job) -> dict[str, Any]:
        assert job.run_id is not None
        run = RunLayout(self._work.runs_dir / job.run_id)
        scan = ScanResult.model_validate_json(run.scan_path.read_text(encoding="utf-8"))
        profile = MachineProfile.model_validate_json(run.probe_path.read_text(encoding="utf-8"))
        options = VerifyOptions(
            index_url=self._config.defaults.index_url,
            timeout_s=float(self._config.defaults.timeout_s),
            keep_env=self._config.defaults.keep_env,
        )
        plan = build_plan(scan, profile, options, self._config)
        job.phase = "真实验证"
        job.log(
            f"解释器 {plan.interpreter.version if plan.interpreter else '未选定'}；"
            f"安装源 {plan.primary_index or 'PyPI 默认'}；"
            f"依赖 {len(plan.requirement_lines)} 条"
        )
        if plan.interpreter is None:
            job.log(f"无法验证：{plan.interpreter_reason}")
            return {"run_id": job.run_id, "verified": False, "reason": plan.interpreter_reason}
        if not plan.installable:
            job.log("项目未声明任何可安装的 pip 依赖，跳过真实验证")
            return {"run_id": job.run_id, "verified": False, "reason": "项目未声明任何可安装的 pip 依赖"}

        started = time.perf_counter()
        runner = VerifyRunner(run=run, plan=plan, uv_path=shutil.which("uv"), echo=job.log)
        result = runner.run_checks()
        elapsed = round(time.perf_counter() - started, 3)
        states = {checkpoint.id: checkpoint.state.value for checkpoint in result.checkpoints}
        job.log("检查点：" + "、".join(f"{key}={value}" for key, value in states.items()))

        verdicts = self._load_verdicts(run.verdicts_path)
        overall, reason = decide_overall(verdicts, result.checkpoints, verify_note=result.note)
        report = build_report(
            run_id=run.dir.name,
            scan=scan,
            probe=profile,
            verdicts=verdicts,
            checkpoints=result.checkpoints,
            overall=overall,
            overall_reason=reason,
            verify_performed=any(checkpoint.state.value != "skip" for checkpoint in result.checkpoints),
            explain=None,
            durations=Durations(verify_s=elapsed, total_s=elapsed),
        )
        write_json_report(report, run.report_json_path)
        write_html_report(report, run.report_html_path)
        job.log(f"结论更新为 overall={overall.value}（{reason}）")
        return {
            "run_id": job.run_id,
            "verified": True,
            "overall": overall.value,
            "overall_reason": reason,
            "checkpoints": states,
            "report_url": f"/api/runs/{job.run_id}/report.html",
        }

    @staticmethod
    def _load_verdicts(path: Path) -> list[Verdict]:
        if not path.is_file():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [Verdict.model_validate(item) for item in payload]
