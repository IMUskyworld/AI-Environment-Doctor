from __future__ import annotations

from pathlib import Path

import pytest

from envdoctor.domain.enums import MlRole, RequirementStrength, TargetKind
from envdoctor.errors import GitError, TargetError
from envdoctor.paths import WorkLayout
from envdoctor.scan.scanner import cuda_runtime_from_token, looks_like_git_url, remote_candidate, scan_target
from envdoctor.subproc import run_command


def _scan(project: Path, tmp_path: Path, **kwargs: object):
    work = WorkLayout(tmp_path / ".envdoctor")
    return scan_target(str(project), work=work, **kwargs)  # type: ignore[arg-type]


def test_cuda_runtime_from_token() -> None:
    assert cuda_runtime_from_token("cu121") == "12.1"
    assert cuda_runtime_from_token("cu118") == "11.8"
    assert cuda_runtime_from_token("cu124") == "12.4"
    assert cuda_runtime_from_token("cu12") == "12"
    assert cuda_runtime_from_token("cu102") == "10.2"
    assert cuda_runtime_from_token("cu110") == "11.0"
    assert cuda_runtime_from_token("nope") is None


def test_looks_like_git_url() -> None:
    assert looks_like_git_url("https://github.com/x/y")
    assert looks_like_git_url("git@github.com:x/y.git")
    assert not looks_like_git_url("./local/path")
    assert not looks_like_git_url("C:/code/project")


def test_remote_candidate_rewrites_shorthand() -> None:
    assert remote_candidate("github.com/owner/repo") == "https://github.com/owner/repo"
    assert remote_candidate("https://github.com/owner/repo") == "https://github.com/owner/repo"
    assert remote_candidate("git@github.com:owner/repo.git") == "git@github.com:owner/repo.git"
    # 本地路径绝不能被当成远程地址
    assert remote_candidate("C:/code/project") is None
    assert remote_candidate("./local/path") is None
    assert remote_candidate("owner/repo") is None
    assert remote_candidate("some/relative/dir") is None


def test_resolve_target_rejects_unknown_local_path(tmp_path: Path) -> None:
    work = WorkLayout(tmp_path / ".envdoctor")
    with pytest.raises(TargetError):
        scan_target(str(tmp_path / "does-not-exist"), work=work)


def test_resolve_target_reports_failure_for_unreachable_remote(tmp_path: Path) -> None:
    """显式 URL 克隆失败时，保留 GIT_CLONE_FAILED 与 stderr 摘要。"""
    work = WorkLayout(tmp_path / ".envdoctor")
    with pytest.raises(GitError) as excinfo:
        scan_target("https://example.invalid/definitely/not-a-repo.git", work=work, clone_timeout_s=30.0)
    assert excinfo.value.code == "GIT_CLONE_FAILED"


def test_resolve_target_falls_back_to_url_for_shorthand(tmp_path: Path) -> None:
    """§9.1：路径不成立时降级为 URL 尝试；两者都失败 → TARGET_NOT_FOUND。"""
    work = WorkLayout(tmp_path / ".envdoctor")
    with pytest.raises(TargetError) as excinfo:
        scan_target("example.invalid/owner/repo", work=work, clone_timeout_s=30.0)
    assert excinfo.value.code == "TARGET_NOT_FOUND"


def test_scan_torch_cu121(projects_dir: Path, tmp_path: Path) -> None:
    result = _scan(projects_dir / "torch-cu121", tmp_path)
    assert result.target.kind == TargetKind.LOCAL
    assert result.cuda.requested_runtime == "12.1"
    assert result.cuda.gpu_required_strength == RequirementStrength.HARD
    assert any("download.pytorch.org" in item for item in result.cuda.evidence)
    torch = next(item for item in result.ml_stack if item.name == "torch")
    assert torch.role == MlRole.CORE
    assert torch.specifier == "==2.4.1+cu121"
    assert "train.py" in result.entrypoints
    assert "pip" in result.package_managers


def test_scan_tf_legacy_is_soft(projects_dir: Path, tmp_path: Path) -> None:
    result = _scan(projects_dir / "tf-legacy", tmp_path)
    assert result.cuda.requested_runtime is None
    assert result.cuda.gpu_required_strength == RequirementStrength.SOFT
    tensorflow = next(item for item in result.ml_stack if item.name == "tensorflow")
    assert tensorflow.specifier == "==2.10.1"


def test_scan_conda_pip(projects_dir: Path, tmp_path: Path) -> None:
    result = _scan(projects_dir / "conda-pip", tmp_path)
    assert result.cuda.requested_runtime == "12.1"
    assert result.cuda.gpu_required_strength == RequirementStrength.HARD
    assert result.python.requires == "==3.11.*"
    assert "conda" in result.package_managers
    names = {item.name for item in result.ml_stack}
    assert {"torch", "xformers"} <= names


def test_scan_clean_uv(projects_dir: Path, tmp_path: Path) -> None:
    result = _scan(projects_dir / "clean-uv", tmp_path)
    assert result.python.requires == ">=3.11"
    assert result.ml_stack == []
    assert result.cuda.gpu_required_strength == RequirementStrength.NONE
    assert "clean-demo" in result.entrypoints


def test_scan_broken_reqs_keeps_going(projects_dir: Path, tmp_path: Path) -> None:
    result = _scan(projects_dir / "broken-reqs", tmp_path)
    codes = {note.code for note in result.warnings}
    assert "REQUIREMENT_UNPARSEABLE" in codes
    assert "REQUIREMENTS_INCLUDE_MISSING" in codes
    assert result.cuda.requested_runtime == "12.4"


def test_scan_poetry_python_requirement(projects_dir: Path, tmp_path: Path) -> None:
    result = _scan(projects_dir / "poetry-demo", tmp_path)
    assert result.python.requires == ">=3.10,<4.0"
    assert "poetry" in result.package_managers


def test_scan_missing_target(tmp_path: Path) -> None:
    with pytest.raises(TargetError):
        _scan(tmp_path / "does-not-exist", tmp_path)


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "requirements.txt").write_text("numpy\n", encoding="utf-8")
    run_command(["git", "init", "-q"], cwd=str(path))
    run_command(["git", "add", "."], cwd=str(path))
    run_command(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "init"],
        cwd=str(path),
    )


def test_clone_target_from_local_git_repo(tmp_path: Path) -> None:
    repo = tmp_path / "demo.git"
    _init_repo(repo)
    work = WorkLayout(tmp_path / ".envdoctor")
    first = scan_target(str(repo), work=work)
    assert first.target.kind == TargetKind.GIT
    assert first.target.cloned is True
    assert first.target.commit
    assert Path(first.target.path).is_dir()
    second = scan_target(str(repo), work=work)
    assert second.target.cloned is False
    assert second.target.path == first.target.path


def test_clone_failure_is_structured(tmp_path: Path) -> None:
    work = WorkLayout(tmp_path / ".envdoctor")
    with pytest.raises(GitError):
        scan_target("https://example.invalid/definitely/not-a-repo.git", work=work, clone_timeout_s=30.0)
