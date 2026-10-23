from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass


@dataclass
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool = False


def run_command(args: list[str], timeout: float, env: dict[str, str] | None = None) -> CommandResult:
    started = time.monotonic()
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    try:
        proc = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            env=merged_env,
            check=False,
        )
        return CommandResult(args, proc.returncode, proc.stdout, proc.stderr,
                             (time.monotonic() - started) * 1000)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return CommandResult(args, 124, stdout, stderr,
                             (time.monotonic() - started) * 1000, True)

