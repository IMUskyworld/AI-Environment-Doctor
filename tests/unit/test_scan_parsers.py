from __future__ import annotations

from pathlib import Path

from envdoctor.scan.parsers import (
    parse_environment_yml,
    parse_pipfile,
    parse_pyproject,
    parse_python_version_file,
    parse_requirements_file,
    parse_setup_cfg,
    parse_setup_py,
    poetry_constraint_to_pep440,
)


def test_requirements_options_deps_and_hashes(tmp_path: Path) -> None:
    req = tmp_path / "requirements.txt"
    req.write_text(
        "--index-url https://download.pytorch.org/whl/cu121\n"
        "--extra-index-url=https://mirror.example/simple\n"
        "-f https://files.example/wheels\n"
        "torch==2.4.1+cu121\n"
        "numpy>=1.26 ; python_version >= '3.10'\n"
        "tokenizers[fast]==0.20.0 --hash=sha256:abc123\n"
        "requests  # inline comment\n",
        encoding="utf-8",
    )
    parsed = parse_requirements_file(req)
    names = {dep.name for dep in parsed.dependencies}
    assert {"torch", "numpy", "tokenizers", "requests"} <= names
    assert parsed.index_urls == ["https://download.pytorch.org/whl/cu121", "https://mirror.example/simple"]
    assert parsed.find_links == ["https://files.example/wheels"]

    torch = next(dep for dep in parsed.dependencies if dep.name == "torch")
    assert torch.specifier == "==2.4.1+cu121"
    numpy = next(dep for dep in parsed.dependencies if dep.name == "numpy")
    assert numpy.marker is not None
    tokenizers = next(dep for dep in parsed.dependencies if dep.name == "tokenizers")
    assert tokenizers.extras == ["fast"]


def test_requirements_recursion_and_cycle(tmp_path: Path) -> None:
    (tmp_path / "base.txt").write_text("-r shared.txt\nrequests\n", encoding="utf-8")
    (tmp_path / "shared.txt").write_text("-r base.txt\nnumpy\n", encoding="utf-8")
    parsed = parse_requirements_file(tmp_path / "base.txt")
    names = {dep.name for dep in parsed.dependencies}
    assert {"requests", "numpy"} <= names
    assert any(note.code == "REQUIREMENTS_CYCLE" for note in parsed.notes)


def test_requirements_editable_and_bad_lines(tmp_path: Path) -> None:
    req = tmp_path / "requirements.txt"
    req.write_text("-e git+https://github.com/x/y.git#egg=z\nthis is not a requirement\n", encoding="utf-8")
    parsed = parse_requirements_file(req)
    assert parsed.dependencies == []
    codes = {note.code for note in parsed.notes}
    assert "EDITABLE_SKIPPED" in codes
    assert "REQUIREMENT_UNPARSEABLE" in codes


def test_requirements_line_continuation(tmp_path: Path) -> None:
    req = tmp_path / "requirements.txt"
    req.write_text("torch==2.4.1+cu121 \\\n    --hash=sha256:deadbeef\n", encoding="utf-8")
    parsed = parse_requirements_file(req)
    assert [dep.name for dep in parsed.dependencies] == ["torch"]


def test_pyproject_pep621(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text(
        "[project]\n"
        'name = "demo"\n'
        'requires-python = ">=3.10,<3.13"\n'
        'dependencies = ["torch>=2.4", "numpy"]\n'
        "\n[project.optional-dependencies]\n"
        'dev = ["pytest>=8"]\n'
        "\n[project.scripts]\n"
        'demo = "demo:main"\n',
        encoding="utf-8",
    )
    parsed = parse_pyproject(path)
    assert parsed.python_requires == (">=3.10,<3.13", "pyproject.toml:project.requires-python")
    assert parsed.scripts == ["demo"]
    by_name = {dep.name: dep for dep in parsed.dependencies}
    assert by_name["torch"].specifier == ">=2.4"
    assert by_name["pytest"].group == "dev"


def test_pyproject_poetry(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text(
        "[tool.poetry]\n"
        'name = "demo"\nversion = "0.1.0"\n'
        "\n[tool.poetry.dependencies]\n"
        'python = "^3.10"\n'
        'torch = "^2.4"\n'
        'numpy = { version = ">=1.26", extras = ["test"] }\n'
        "\n[tool.poetry.group.dev.dependencies]\n"
        'pytest = "*"\n',
        encoding="utf-8",
    )
    parsed = parse_pyproject(path)
    assert parsed.package_managers == {"poetry"}
    assert parsed.python_requires == (">=3.10,<4.0", "pyproject.toml:tool.poetry.dependencies.python")
    by_name = {dep.name: dep for dep in parsed.dependencies}
    assert by_name["torch"].specifier == ">=2.4,<3.0"
    assert by_name["numpy"].extras == ["test"]
    assert by_name["pytest"].group == "dev"


def test_poetry_constraint_translation() -> None:
    assert poetry_constraint_to_pep440("^3.10") == ">=3.10,<4.0"
    assert poetry_constraint_to_pep440("^3") == ">=3,<4"
    assert poetry_constraint_to_pep440("~3.11") == ">=3.11,<4.0"
    assert poetry_constraint_to_pep440(">=1.26") == ">=1.26"
    assert poetry_constraint_to_pep440("*") is None


def test_setup_cfg(tmp_path: Path) -> None:
    path = tmp_path / "setup.cfg"
    path.write_text(
        "[options]\n"
        "python_requires = >=3.9\n"
        "install_requires =\n"
        "    requests\n"
        "    torch>=2.0\n"
        "\n[options.extras_require]\n"
        "test =\n"
        "    pytest\n",
        encoding="utf-8",
    )
    parsed = parse_setup_cfg(path)
    assert parsed.python_requires == (">=3.9", "setup.cfg:options.python_requires")
    by_name = {dep.name: dep for dep in parsed.dependencies}
    assert by_name["torch"].specifier == ">=2.0"
    assert by_name["pytest"].group == "test"


def test_setup_py_regex_only(tmp_path: Path) -> None:
    path = tmp_path / "setup.py"
    path.write_text(
        "from setuptools import setup\n"
        "setup(\n"
        "    name='demo',\n"
        "    python_requires='>=3.9',\n"
        "    install_requires=['requests', 'torch>=2.0'],\n"
        ")\n",
        encoding="utf-8",
    )
    parsed = parse_setup_py(path)
    assert parsed.python_requires == (">=3.9", "setup.py:python_requires")
    assert {dep.name for dep in parsed.dependencies} == {"requests", "torch"}


def test_environment_yml(tmp_path: Path) -> None:
    path = tmp_path / "environment.yml"
    path.write_text(
        "name: demo\n"
        "channels: [pytorch]\n"
        "dependencies:\n"
        "  - python=3.11\n"
        "  - pytorch-cuda=12.1\n"
        "  - pip:\n"
        "      - torch==2.4.0\n",
        encoding="utf-8",
    )
    parsed = parse_environment_yml(path)
    assert parsed.python_requires == ("==3.11.*", "environment.yml:python")
    by_name = {dep.name: dep for dep in parsed.dependencies}
    assert by_name["pytorch-cuda"].specifier == "==12.1"
    assert by_name["torch"].source == "environment.yml:pip"


def test_pipfile(tmp_path: Path) -> None:
    path = tmp_path / "Pipfile"
    path.write_text(
        "[packages]\n"
        'requests = "*"\n'
        'torch = ">=2.0"\n'
        "\n[dev-packages]\n"
        'pytest = "*"\n'
        "\n[requires]\n"
        'python_version = "3.11"\n',
        encoding="utf-8",
    )
    parsed = parse_pipfile(path)
    assert parsed.python_requires == ("==3.11.*", "Pipfile:requires.python_version")
    by_name = {dep.name: dep for dep in parsed.dependencies}
    assert by_name["torch"].specifier == ">=2.0"
    assert by_name["pytest"].group == "dev"


def test_python_version_file(tmp_path: Path) -> None:
    path = tmp_path / ".python-version"
    path.write_text("3.12.4\n", encoding="utf-8")
    assert parse_python_version_file(path) == ("==3.12.4.*", ".python-version")
