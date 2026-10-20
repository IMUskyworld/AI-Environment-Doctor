# Local quality gate: run this before pushing.
# Usage:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1 [-WithGpu] [-WithNetwork]
# （本机默认执行策略会拦截 -File；-ExecutionPolicy Bypass 是必须的——faillab 事故 F-0001）

param(
    [switch]$WithGpu,
    [switch]$WithNetwork
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    $py = Join-Path $root ".venv/bin/python"
}
if (-not (Test-Path $py)) {
    Write-Host "no .venv found; run:  uv venv && uv pip install -e '.[dev]' (see DEVELOPMENT.md 24)" -ForegroundColor Red
    exit 2
}

function Invoke-Step($name, $argv) {
    Write-Host "`n=== $name" -ForegroundColor Cyan
    & $py @argv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $name" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

Invoke-Step "ruff check" @("-m", "ruff", "check", "src", "tests", "scripts")
Invoke-Step "ruff format --check" @("-m", "ruff", "format", "--check", "src", "tests", "scripts")
Invoke-Step "pyright (src)" @("-m", "pyright")
Invoke-Step "schemas are current" @("scripts/export_schemas.py", "--check")

$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($uv) {
    Write-Host "`n=== uv.lock is up to date" -ForegroundColor Cyan
    & uv lock --check
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: uv.lock is up to date (run: uv lock)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
} else {
    Write-Host "`n=== uv.lock is up to date" -ForegroundColor Yellow
    Write-Host "uv not found: skipping the lockfile check" -ForegroundColor Yellow
}

if ($WithGpu) {
    Invoke-Step "pytest (all incl. gpu)" @("-m", "pytest", "-q")
} else {
    Invoke-Step "pytest (no gpu)" @("-m", "pytest", "-q", "-m", "not gpu")
}

if ($WithNetwork -or $env:ENVDOCTOR_TEST_INDEX) {
    # 真装真跑：需要一个可达的安装源（ENVDOCTOR_TEST_INDEX）
    Invoke-Step "pytest (network: real uv venv + install)" @("-m", "pytest", "-q", "-m", "network")
}

Invoke-Step "MVP acceptance audit (DEVELOPMENT.md 22)" @("scripts/verify_mvp.py", "--skip-tests")

Write-Host "`nall checks passed" -ForegroundColor Green
