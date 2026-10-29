"""Finding repositories and what in them exists only on this machine. No tkinter here.

Nothing here writes: every git call only reads, with optional locks off, so not even the index is
refreshed. What counts as unsaved:

- changes: tracked edits, staged files and untracked files that are not ignored;
- branches: local branches whose tip is on no remote;
- stashes.

Untracked files larger than `LARGE` are listed but left out of the snapshot.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import ssh
from .proc import git

WORKERS = 8
LARGE = 50 * 1024 * 1024

# Folders that never contain repositories worth saving and are expensive to walk
SKIP_DIRS = {"node_modules", ".gradle", ".idea", ".venv", "venv", "__pycache__", "target", "build", "dist", "out"}
# Folder names of cloud sync clients, for the home folder fallback (see `home_fallback`)
CLOUD_NAMES = re.compile(r"^(onedrive|dropbox|google ?drive|icloud ?drive|box|nextcloud)\b", re.IGNORECASE)


@dataclass
class Branch:
    name: str
    sha: str
    upstream_remote: str | None = None
    upstream_ref: str | None = None  # e.g. refs/heads/main on that remote
    gone: bool = False  # its upstream was deleted on the server: never recreate it there


@dataclass
class Repo:
    path: Path
    name: str  # relative to the scanned folder, with forward slashes
    head_branch: str | None = None  # None: detached HEAD
    head_sha: str | None = None  # None: no commit yet
    remotes: dict[str, str] = field(default_factory=dict)  # name -> URL
    remote: str | None = None  # where emergency branches go; None: nowhere to save to
    email: str | None = None
    changes: list[str] = field(default_factory=list)  # paths with uncommitted changes
    large: list[tuple[str, int]] = field(default_factory=list)  # untracked files left out, with size
    branches: list[Branch] = field(default_factory=list)  # local branches on no remote
    stashes: list[str] = field(default_factory=list)  # stash commits, newest first
    keys: list[Path] = field(default_factory=list)  # SSH keys with a passphrase the pushes need
    error: str | None = None

    @property
    def unsaved(self) -> bool:
        return bool(self.changes or self.branches or self.stashes)

    def summary(self) -> str:
        """What is unsaved, e.g. "3 files · 1 branch · 2 stashes"."""
        counts = [(len(self.changes), "file", "files"), (len(self.branches), "branch", "branches"),
                  (len(self.stashes), "stash", "stashes")]
        return " · ".join(f"{n} {one if n == 1 else many}" for n, one, many in counts if n)


# ---------------------------------------------------------------------------
# Finding repositories
# ---------------------------------------------------------------------------
def find_repositories(root: Path, max_depth: int | None = None, skip: set[Path] = frozenset()) -> list[Path]:
    """Every folder below root that contains a .git directory or file; does not look inside one,
    nor into `skip`, links and junctions."""
    repos = []
    root_depth = len(root.parts)
    skip = {os.path.normcase(p) for p in skip}

    for current, dirs, _ in os.walk(root):
        current_path = Path(current)

        if (current_path / ".git").exists():
            repos.append(current_path)
            dirs.clear()  # a repository's own subfolders are not separate projects
            continue

        if max_depth is not None and len(current_path.parts) - root_depth >= max_depth:
            dirs.clear()
            continue

        dirs[:] = [
            d for d in dirs
            if d not in SKIP_DIRS and not d.startswith(".")
            and os.path.normcase(current_path / d) not in skip
            and not os.path.islink(current_path / d) and not _is_junction(current_path / d)
        ]

    return sorted(repos)


def _is_junction(path: Path) -> bool:
    is_junction = getattr(os.path, "isjunction", None)  # Python 3.12+
    return bool(is_junction and is_junction(path))


def home_fallback() -> tuple[Path, set[Path]]:
    """The folder to search when no project folder was ever chosen: the home folder, without
    AppData and cloud-synced folders. Running git in a synced folder could make the sync client
    download files on demand or upload what git writes."""
    home = Path.home()
    skip = {home / "AppData", home / "Library"} | cloud_roots()
    try:
        skip |= {p for p in home.iterdir() if p.is_dir() and CLOUD_NAMES.match(p.name)}
    except OSError:
        pass
    return home, skip


def cloud_roots() -> set[Path]:
    """The folders Windows' cloud sync engines (OneDrive, SharePoint libraries, ...) keep in sync."""
    if sys.platform != "win32":
        return set()
    import winreg

    def subkeys(key: object) -> list[str]:
        names, i = [], 0
        while True:
            try:
                names.append(winreg.EnumKey(key, i))
            except OSError:
                return names
            i += 1

    roots = set()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\SyncEngines\Providers") as providers:
            for provider in subkeys(providers):
                with winreg.OpenKey(providers, provider) as provider_key:
                    for sync in subkeys(provider_key):
                        with winreg.OpenKey(provider_key, sync) as sync_key:
                            try:
                                roots.add(Path(winreg.QueryValueEx(sync_key, "MountPoint")[0]))
                            except OSError:
                                pass
    except OSError:
        pass
    return roots


def scan(root: Path, max_depth: int | None = None, skip: set[Path] = frozenset()) -> list[Repo]:
    """The repositories below `root`, not read yet; fast, so the window can show progress at once."""
    return [Repo(path, path.relative_to(root).as_posix()) for path in find_repositories(root, max_depth, skip)]


# ---------------------------------------------------------------------------
# Reading what is unsaved
# ---------------------------------------------------------------------------
def inspect_all(repos: list[Repo], tools: ssh.SshTools | None, done: Callable[[Repo], None]) -> None:
    """Read every repository, then the SSH keys with a passphrase its pushes need.

    `done` runs on a worker thread after each repository. `tools` None skips the keys.
    """
    def one(repo: Repo) -> None:
        try:
            inspect(repo)
        except Exception as error:  # one broken repository must not stop the others
            repo.error = str(error)
        done(repo)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(one, repos))
    if tools is not None:
        find_keys(repos, tools)


def inspect(repo: Repo) -> None:
    """Fill in `repo`: three git calls, a fourth only if it has stashes."""
    _read_config(repo)
    has_stash = _read_refs(repo)
    _read_status(repo)
    if has_stash:
        proc = git(repo.path, "stash", "list", "--format=%H")
        repo.stashes = proc.stdout.split()
    repo.remote = choose_remote(repo)


def choose_remote(repo: Repo) -> str | None:
    """origin; else the remote the current branch tracks; else the first one by name."""
    if not repo.remotes:
        return None
    if "origin" in repo.remotes:
        return "origin"
    tracked = next((b.upstream_remote for b in repo.branches if b.name == repo.head_branch), None)
    if tracked in repo.remotes:
        return tracked
    return sorted(repo.remotes)[0]


def _read_config(repo: Repo) -> None:
    proc = git(repo.path, "config", "--get-regexp", r"^(remote\..*\.url|user\.email)$")
    for line in proc.stdout.splitlines():
        key, _, value = line.partition(" ")
        if key == "user.email":
            repo.email = value
        else:
            repo.remotes[key.removeprefix("remote.").removesuffix(".url")] = value


def _read_refs(repo: Repo) -> bool:
    """Local branches that are on no remote; True if there are stashes."""
    fields = "%(refname)%00%(objectname)%00%(upstream:remotename)%00%(upstream:remoteref)%00%(upstream:track)"
    proc = git(repo.path, "for-each-ref", f"--format={fields}", "refs/heads", "refs/remotes", "refs/stash")
    local, on_remotes, has_stash = [], set(), False
    for line in proc.stdout.splitlines():
        ref, sha, remote, remote_ref, track = (line.split("\0") + ["", "", "", ""])[:5]
        if ref.startswith("refs/heads/"):
            local.append(Branch(ref.removeprefix("refs/heads/"), sha, remote or None, remote_ref or None,
                                gone=track == "[gone]"))
        elif ref.startswith("refs/remotes/"):
            on_remotes.add(sha)
        elif ref == "refs/stash":
            has_stash = True

    candidates = [b for b in local if b.sha not in on_remotes]
    if candidates and on_remotes:
        # A tip that is not itself a remote tip can still be behind one: ask which commits no remote has
        proc = git(repo.path, "rev-list", "--branches", "--not", "--remotes")
        unpushed = set(proc.stdout.split())
        candidates = [b for b in candidates if b.sha in unpushed]
    repo.branches = candidates
    return has_stash


def _read_status(repo: Repo) -> None:
    proc = git(repo.path, "status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git status failed")
    entries = proc.stdout.split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if entry.startswith("# branch.oid "):
            oid = entry.removeprefix("# branch.oid ")
            repo.head_sha = None if oid == "(initial)" else oid
        elif entry.startswith("# branch.head "):
            head = entry.removeprefix("# branch.head ")
            repo.head_branch = None if head == "(detached)" else head
        elif entry.startswith("1 "):
            repo.changes.append(entry.split(" ", 8)[8])
        elif entry.startswith("2 "):
            repo.changes.append(entry.split(" ", 9)[9])
            i += 1  # the original path of a rename follows
        elif entry.startswith("u "):
            repo.changes.append(entry.split(" ", 10)[10])
        elif entry.startswith("? "):
            path = entry[2:]
            try:
                size = (repo.path / path).stat().st_size
            except OSError:
                size = 0
            if size > LARGE:
                repo.large.append((path, size))
            else:
                repo.changes.append(path)


def find_keys(repos: list[Repo], tools: ssh.SshTools) -> None:
    """The SSH keys with a passphrase each repository's pushes need, asked once per host."""
    cache: dict[tuple[str, str | None], list[Path]] = {}
    for repo in repos:
        if not repo.unsaved or repo.remote is None:
            continue
        remotes = {repo.remote} | {b.upstream_remote for b in repo.branches if b.upstream_remote}
        needed = []
        for remote in sorted(remotes):
            destination = ssh.ssh_destination(repo.remotes.get(remote, ""))
            if destination is None:
                continue
            if destination not in cache:
                cache[destination] = ssh.locked_keys(tools, destination)
            needed += cache[destination]
        repo.keys = list(dict.fromkeys(needed))
