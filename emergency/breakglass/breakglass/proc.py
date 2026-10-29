"""Running git and its helpers: captured output, no console window, never a prompt."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def run(args: list[str], env: dict[str, str] | None = None, timeout: int = 60,
        cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, env=env, cwd=cwd, stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def git(repo: Path, *args: str, env: dict[str, str] | None = None,
        timeout: int = 60) -> subprocess.CompletedProcess:
    """`git -C repo args`. Optional locks are off, so even reading commands like status never
    refresh and rewrite the repository's index."""
    return run(["git", "--no-optional-locks", "-C", str(repo), *args], env=env, timeout=timeout)


def quiet_env(**extra: str) -> dict[str, str]:
    """The environment for git calls that must never stop to ask anything."""
    return dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", **extra)
