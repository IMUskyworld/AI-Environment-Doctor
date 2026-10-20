"""Real verification against a real index: uv venv → install → import → cleanup.

Skipped unless a reachable index is configured (``ENVDOCTOR_TEST_INDEX`` locally,
plain PyPI on CI). This is the §22-6 evidence path.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from envdoctor.config import EnvDoctorConfig
from envdoctor.domain.enums import CheckpointState
from envdoctor.paths import RunLayout, WorkLayout
from envdoctor.probe.machine import probe_machine
from envdoctor.scan.scanner import scan_target
from envdoctor.verify.planner import VerifyOptions, build_plan
from envdoctor.verify.runner import VerifyRunner

pytestmark = [pytest.mark.network, pytest.mark.slow]

INDEX = os.environ.get("ENVDOCTOR_TEST_INDEX")
SKIP_REASON = "需要可达的安装源：设置 ENVDOCTOR_TEST_INDEX，或在 CI 环境运行"


@pytest.mark.skipif(not INDEX and not os.environ.get("CI"), reason=SKIP_REASON)
def test_real_venv_install_import_cleanup(projects_dir: Path, tmp_path: Path) -> None:
    work = WorkLayout(tmp_path / ".envdoctor")
    scan = scan_target(str(projects_dir / "tiny-ml"), work=work)
    profile = probe_machine()
    plan = build_plan(scan, profile, VerifyOptions(index_url=INDEX), EnvDoctorConfig())
    assert plan.interpreter is not None

    uv = shutil.which("uv")
    assert uv, "uv 必须在 PATH 上（真实验证依赖它）"

    runner = VerifyRunner(run=RunLayout(tmp_path / "run"), plan=plan, uv_path=uv, echo=print)
    result = runner.run_checks()
    states = {checkpoint.id: checkpoint.state for checkpoint in result.checkpoints}

    assert states["venv"] == CheckpointState.PASS, result.note
    assert states["install"] == CheckpointState.PASS
    assert states["imports"] == CheckpointState.PASS
    assert states["user-env-unchanged"] == CheckpointState.PASS
    assert result.env_dir is None  # cleaned up
    assert result.requirements_path and Path(result.requirements_path).is_file()
