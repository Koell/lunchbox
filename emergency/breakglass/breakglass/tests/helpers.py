"""Throwaway repositories for the tests: a bare origin (the "server") and clones of it, all in a
temp folder, so nothing touches the network or a real repository."""

from __future__ import annotations

import subprocess
from pathlib import Path

IDENTITY = ["-c", "user.name=breakglass", "-c", "user.email=tester@example.invalid",
            "-c", "commit.gpgsign=false"]


def git(cwd: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(
        ["git", *IDENTITY, *args], cwd=cwd, check=check, capture_output=True, text=True
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
    git(target, "config", "user.email", "tester@example.invalid")
    git(target, "config", "user.name", "breakglass")
    return target


def commit(repo: Path, message: str, name: str = "file.txt") -> None:
    (repo / name).write_text(message + "\n", encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", message)


def remote_branches(bare: Path) -> dict[str, str]:
    """Branch name -> commit on the "server"."""
    out = git(bare, "for-each-ref", "--format=%(refname:lstrip=2) %(objectname)", "refs/heads")
    return dict(line.split(" ", 1) for line in out.splitlines())


def show(bare: Path, rev: str, path: str) -> str | None:
    """A file's content at `rev` on the "server", None if it isn't there."""
    proc = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=bare, capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


def local_state(repo: Path) -> tuple:
    """Everything breakglass must leave alone: index bytes, HEAD, branches, stashes, config, files."""
    git_dir = repo / ".git"
    files = sorted(
        (str(p.relative_to(repo)), p.read_bytes())
        for p in repo.rglob("*") if p.is_file() and ".git" not in p.relative_to(repo).parts
    )
    return (
        (git_dir / "index").read_bytes() if (git_dir / "index").exists() else b"",
        git(repo, "rev-parse", "HEAD", check=False),
        git(repo, "for-each-ref", "refs/heads", "refs/stash"),
        git(repo, "stash", "list"),
        (git_dir / "config").read_text(encoding="utf-8"),
        files,
    )
