"""Throwaway repositories for the tests: a bare origin and clones of it, all in a temp folder, so
nothing touches the network or a real repository."""

from __future__ import annotations

import subprocess
from pathlib import Path

IDENTITY = ["-c", "user.name=omnifetch", "-c", "user.email=omnifetch@example.invalid", "-c", "commit.gpgsign=false"]


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *IDENTITY, *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def origin(folder: Path, name: str = "origin.git") -> Path:
    """A bare repository with one commit on main."""
    bare = folder / name
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    seed = folder / f"{name}-seed"
    clone(bare, seed)
    commit(seed, "first")
    git(seed, "push", "-q", "origin", "main")
    return bare


def clone(bare: Path, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", str(bare), str(target)], check=True, capture_output=True)
    return target


def commit(repo: Path, message: str) -> None:
    (repo / "file.txt").write_text(message + "\n", encoding="utf-8")
    git(repo, "add", "file.txt")
    git(repo, "commit", "-q", "-m", message)


def push_new_commit(bare: Path, scratch: Path) -> None:
    """Advance origin's main by one commit, from a separate clone."""
    work = clone(bare, scratch)
    commit(work, "later")
    git(work, "push", "-q", "origin", "main")
