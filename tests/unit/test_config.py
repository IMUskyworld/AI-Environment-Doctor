"""Configuration loading (DEVELOPMENT.md §16): file lookup, env overrides, LLM switch."""

from __future__ import annotations

from pathlib import Path

import pytest

from envdoctor.config import (
    EnvDoctorConfig,
    find_config_path,
    llm_ready,
    load_config,
    user_config_path,
)
from envdoctor.errors import ConfigError

CONFIG_TEXT = """
[defaults]
index_url = "https://mirror.example/simple"
timeout_s = 120
keep_env = true

[llm]
base_url = "https://llm.example/v1"
model = "qwen-test"
"""


def _write(tmp_path: Path, text: str = CONFIG_TEXT) -> Path:
    path = tmp_path / "envdoctor.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_explicit_config_path_wins(tmp_path: Path) -> None:
    path = _write(tmp_path)
    config = load_config(str(path))
    assert config.defaults.index_url == "https://mirror.example/simple"
    assert config.defaults.timeout_s == 120
    assert config.defaults.keep_env is True
    assert config.llm.base_url == "https://llm.example/v1"
    assert config.llm.model == "qwen-test"


def test_missing_explicit_config_is_a_structured_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_config(str(tmp_path / "nope.toml"))
    assert excinfo.value.code == "CONFIG_INVALID"


def test_local_config_is_found_in_cwd(tmp_path: Path) -> None:
    _write(tmp_path)
    found = find_config_path(None, cwd=tmp_path)
    assert found is not None
    assert found.name == "envdoctor.toml"


def test_user_config_path_is_absolute() -> None:
    assert user_config_path().is_absolute()


def test_env_overrides_beat_the_file(tmp_path: Path, monkeypatch) -> None:
    path = _write(tmp_path)
    monkeypatch.setenv("ENVDOCTOR_INDEX_URL", "https://env.example/simple")
    monkeypatch.setenv("ENVDOCTOR_LLM_BASE_URL", "https://env-llm.example/v1")
    monkeypatch.setenv("ENVDOCTOR_LLM_MODEL", "env-model")
    config = load_config(str(path))
    assert config.defaults.index_url == "https://env.example/simple"
    assert config.llm.base_url == "https://env-llm.example/v1"
    assert config.llm.model == "env-model"


def test_llm_enabled_env_switch(monkeypatch) -> None:
    monkeypatch.setenv("ENVDOCTOR_LLM_ENABLED", "0")
    config = load_config()
    assert config.llm.enabled is False
    ready, reason = llm_ready(config)
    assert ready is False
    assert "enabled" in reason


def test_llm_disabled_in_file_blocks_explain(tmp_path: Path, monkeypatch) -> None:
    path = _write(tmp_path, CONFIG_TEXT + "\nenabled = false\n")
    monkeypatch.setenv("ENVDOCTOR_LLM_API_KEY", "k")
    config = load_config(str(path))
    assert config.llm.enabled is False
    ready, _reason = llm_ready(config)
    assert ready is False


def test_unknown_config_keys_are_ignored(tmp_path: Path) -> None:
    """旧配置文件里的废弃键不应让整个工具报错。"""
    path = _write(tmp_path, CONFIG_TEXT + "\nunknown_section = 1\n")
    config = load_config(str(path))
    assert config.defaults.timeout_s == 120


def test_invalid_toml_is_a_structured_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "[defaults\nbroken = ")
    with pytest.raises(ConfigError) as excinfo:
        load_config(str(path))
    assert excinfo.value.code == "CONFIG_INVALID"


def test_default_config_shape() -> None:
    config = EnvDoctorConfig()
    assert config.defaults.timeout_s == 600
    assert config.defaults.index_url is None
    assert config.llm.enabled is None
