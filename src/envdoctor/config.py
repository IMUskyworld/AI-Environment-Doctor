"""Configuration loading (DEVELOPMENT.md §16).

Search order: ``--config`` > ``./envdoctor.toml`` > user config. Environment
variables override file values. The LLM API key lives **only** in the
environment and is never part of the config model or any artifact.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from envdoctor.errors import CONFIG_INVALID, ConfigError


class LlmConfig(BaseModel):
    """LLM 解释层配置。

    ``enabled`` 是显式开关：设为 ``false`` 时即使传了 ``--explain`` 也不调用 LLM；
    留空（``None``）表示跟随命令行 ``--explain``。
    """

    enabled: bool | None = None
    base_url: str | None = None
    model: str | None = None


class DefaultsConfig(BaseModel):
    index_url: str | None = None
    timeout_s: int = 600
    keep_env: bool = False


class EnvDoctorConfig(BaseModel):
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)


def user_config_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "envdoctor" / "config.toml"
    return Path.home() / ".config" / "envdoctor" / "config.toml"


def find_config_path(explicit: str | None, cwd: Path | None = None) -> Path | None:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise ConfigError(f"配置文件不存在：{path}", code=CONFIG_INVALID, details={"path": str(path)})
        return path
    local = (cwd or Path.cwd()) / "envdoctor.toml"
    if local.is_file():
        return local
    fallback = user_config_path()
    return fallback if fallback.is_file() else None


def load_config(explicit_path: str | None = None, cwd: Path | None = None) -> EnvDoctorConfig:
    path = find_config_path(explicit_path, cwd)
    data: dict[str, object] = {}
    if path is not None:
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(
                f"无法解析配置文件：{path}",
                code=CONFIG_INVALID,
                details={"path": str(path), "reason": f"{type(exc).__name__}: {exc}"},
            ) from exc
    try:
        config = EnvDoctorConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(
            f"配置文件字段无效：{path}",
            code=CONFIG_INVALID,
            details={"path": str(path), "reason": exc.errors()[:5]},
        ) from exc
    _apply_env_overrides(config)
    return config


def _apply_env_overrides(config: EnvDoctorConfig) -> None:
    index_url = os.environ.get("ENVDOCTOR_INDEX_URL")
    if index_url:
        config.defaults.index_url = index_url
    base_url = os.environ.get("ENVDOCTOR_LLM_BASE_URL")
    if base_url:
        config.llm.base_url = base_url
    model = os.environ.get("ENVDOCTOR_LLM_MODEL")
    if model:
        config.llm.model = model
    enabled = os.environ.get("ENVDOCTOR_LLM_ENABLED")
    if enabled is not None:
        config.llm.enabled = enabled.strip().lower() not in {"0", "false", "no", "off"}


def llm_api_key() -> str | None:
    """The API key is read from the environment at call time and never stored."""
    return os.environ.get("ENVDOCTOR_LLM_API_KEY") or None


def llm_ready(config: EnvDoctorConfig) -> tuple[bool, str]:
    """Return (ready, reason-when-not-ready) for the --explain layer."""
    if config.llm.enabled is False:
        return False, "配置中 llm.enabled = false，解释层已显式关闭"
    if not config.llm.base_url:
        return False, "未配置 LLM 端点（llm.base_url 或 ENVDOCTOR_LLM_BASE_URL）"
    if not config.llm.model:
        return False, "未配置 LLM 模型名（llm.model 或 ENVDOCTOR_LLM_MODEL）"
    if not llm_api_key():
        return False, "未配置 LLM API key（环境变量 ENVDOCTOR_LLM_API_KEY）"
    return True, ""
