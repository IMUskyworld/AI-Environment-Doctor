"""Child-process execution with the Windows reality baked in.

Two lessons from LabBox (DEVELOPMENT.md §24) are encoded here:

* child output is decoded with UTF-8 first and GBK second — never assume UTF-8;
* a missing executable or a timeout is data (``CommandResult``), not an exception.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field


def decode_bytes(data: bytes) -> str:
    """Decode child output: UTF-8, then GBK (cp936 consoles), then lossy UTF-8."""
    for encoding in ("utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


@dataclass
class CommandResult:
    argv: list[str]
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    seconds: float = 0.0
    error: str | None = None
    env_extra: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and self.error is None

    def tail(self, lines: int = 40) -> str:
        chunks: list[str] = []
        for name, text in (("stdout", self.stdout), ("stderr", self.stderr)):
            if text.strip():
                kept = "\n".join(text.strip().splitlines()[-lines:])
                chunks.append(f"[{name}]\n{kept}")
        if self.error:
            chunks.append(f"[error] {self.error}")
        return "\n".join(chunks)


def run_command(
    argv: list[str],
    *,
    timeout_s: float = 30.0,
    cwd: str | None = None,
    env_extra: dict[str, str] | None = None,
    stdin_data: str | None = None,
) -> CommandResult:
    """Run a command, capturing bytes. Never raises for non-zero exits/timeouts."""
    env = dict(os.environ)
    env.update(env_extra or {})
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            input=stdin_data.encode("utf-8") if stdin_data is not None else None,
            capture_output=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            argv=argv,
            timed_out=True,
            stdout=decode_bytes(exc.stdout or b""),
            stderr=decode_bytes(exc.stderr or b""),
            seconds=time.perf_counter() - started,
        )
    except OSError as exc:  # missing executable, permission denied, ...
        return CommandResult(
            argv=argv,
            error=f"{type(exc).__name__}: {exc}",
            seconds=time.perf_counter() - started,
        )
    return CommandResult(
        argv=argv,
        returncode=proc.returncode,
        stdout=decode_bytes(proc.stdout),
        stderr=decode_bytes(proc.stderr),
        seconds=time.perf_counter() - started,
    )
