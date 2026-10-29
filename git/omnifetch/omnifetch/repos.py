"""Finding repositories, reading their remotes and fetching them. No tkinter here.

SSH keys with a passphrase are unlocked into a private ssh-agent that lives for one fetch run only
(`Agent`): the passphrase goes from the dialog to a single ssh-add call through its environment and
is never written anywhere. The agent and its folder are removed when the run ends, so the next run
asks again.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

WORKERS = 8
TIMEOUT = 120

# Folders that never contain repositories worth fetching and are expensive to walk
SKIP_DIRS = {"node_modules", ".gradle", ".idea", ".venv", "venv", "__pycache__", "target", "build", "dist", "out"}

# Called by ssh-add instead of a terminal prompt. The passphrase comes from the environment of that
# one ssh-add call; a second prompt means it was wrong, so give up instead of looping.
ASKPASS = """#!/bin/sh
case "$1" in
    Bad*) exit 1 ;;
esac
printf '%s\\n' "$OMNIFETCH_PASSPHRASE"
"""


# ---------------------------------------------------------------------------
# SSH tools
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SshTools:
    """The ssh programs git uses. On Windows these are Git for Windows' own (MSYS) builds, which
    take POSIX paths for the agent socket and cannot reach the Windows ssh-agent service."""

    ssh: Path
    agent: Path
    add: Path
    keygen: Path
    cygpath: Path | None

    def posix(self, path: Path) -> str:
        if self.cygpath is None:
            return str(path)
        return _run([str(self.cygpath), "-u", str(path)]).stdout.strip()

    def native(self, path: str) -> Path:
        if self.cygpath is None or not path.startswith("/"):
            return Path(path)
        return Path(_run([str(self.cygpath), "-w", path]).stdout.strip())


def find_ssh_tools() -> SshTools:
    if sys.platform == "win32":
        exec_path = _run(["git", "--exec-path"]).stdout.strip()  # <Git>/mingw64/libexec/git-core
        bin_dir = Path(exec_path).parents[2] / "usr" / "bin"
        names = ["ssh.exe", "ssh-agent.exe", "ssh-add.exe", "ssh-keygen.exe", "cygpath.exe"]
        paths = [bin_dir / name for name in names]
        missing = [p for p in paths if not p.exists()]
        if missing:
            raise RuntimeError(f"Git for Windows' ssh not found: {missing[0]}")
        return SshTools(*paths)

    paths = [shutil.which(name) for name in ("ssh", "ssh-agent", "ssh-add", "ssh-keygen")]
    if not all(paths):
        raise RuntimeError("ssh, ssh-agent, ssh-add and ssh-keygen must be on the PATH")
    return SshTools(*(Path(p) for p in paths), cygpath=None)


def ssh_destination(url: str) -> tuple[str, str | None] | None:
    """`[user@]host` and port of an SSH remote URL, None for HTTPS and local remotes."""
    if url.startswith("ssh://"):
        parts = urlsplit(url)
        host = f"{parts.username}@{parts.hostname}" if parts.username else parts.hostname
        return (host, str(parts.port) if parts.port else None) if host else None
    if "://" in url:
        return None
    # scp-like syntax, user@host:path; a single letter before the colon is a Windows drive
    match = re.match(r"^([^/\\:]{2,}):", url)
    return (match.group(1), None) if match else None


def host_of(url: str) -> str:
    """The host name of a remote URL, for display."""
    destination = ssh_destination(url)
    if destination:
        return destination[0].split("@")[-1]
    return urlsplit(url).hostname or url


def locked_keys(tools: SshTools, destination: tuple[str, str | None]) -> list[Path]:
    """The key files ssh would try for `destination` that have a passphrase."""
    host, port = destination
    args = [str(tools.ssh), "-G", *(["-p", port] if port else []), host]
    keys = []
    for line in _run(args).stdout.splitlines():
        name, _, value = line.partition(" ")
        if name.lower() != "identityfile":
            continue
        key = Path.home() / value[2:] if value.startswith("~/") else tools.native(value)
        if key.is_file() and _has_passphrase(tools, key):
            keys.append(key)
    return keys


def _has_passphrase(tools: SshTools, key: Path) -> bool:
    return _run([str(tools.keygen), "-y", "-P", "", "-f", str(key)]).returncode != 0


class Agent:
    """A private ssh-agent for one fetch run; `close` stops it, which forgets every key."""

    def __init__(self, tools: SshTools) -> None:
        self.tools = tools
        self.folder = Path(tempfile.mkdtemp(prefix="omnifetch-"))
        self.socket = self.folder / "agent.sock"
        askpass = self.folder / "askpass.sh"
        askpass.write_text(ASKPASS, encoding="utf-8", newline="\n")
        askpass.chmod(0o700)
        self._askpass = tools.posix(askpass)
        self._socket = tools.posix(self.socket)
        self._process = subprocess.Popen(
            [str(tools.agent), "-D", "-a", self._socket],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._job = _die_with_us(self._process)
        for _ in range(50):
            if self.socket.exists():
                return
            time.sleep(0.1)
        self.close()
        raise RuntimeError("ssh-agent did not start")

    @property
    def env(self) -> dict[str, str]:
        return {"SSH_AUTH_SOCK": self._socket}

    def add(self, key: Path, passphrase: str) -> str | None:
        """Unlock `key` into the agent; None on success, otherwise ssh-add's message."""
        env = dict(
            os.environ,
            **self.env,
            SSH_ASKPASS=self._askpass,
            SSH_ASKPASS_REQUIRE="force",
            DISPLAY=os.environ.get("DISPLAY", "none"),
            OMNIFETCH_PASSPHRASE=passphrase,
        )
        try:
            proc = _run([str(self.tools.add), str(key)], env=env, timeout=20)
        except subprocess.TimeoutExpired:
            return "ssh-add did not answer"
        if proc.returncode == 0:
            return None
        if "passphrase" in proc.stderr.lower() or not proc.stderr.strip():
            return "Wrong passphrase."
        return proc.stderr.strip()

    def close(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        shutil.rmtree(self.folder, ignore_errors=True)


def _die_with_us(process: subprocess.Popen) -> object | None:
    """On Windows, tie `process` to a job that is killed when this Python exits, so the agent and
    its unlocked keys never outlive the app, even after a crash. Keep the returned handle."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_ulonglong * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    info = Extended()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    kernel32.SetInformationJobObject(wintypes.HANDLE(job), 9,  # JobObjectExtendedLimitInformation
                                     ctypes.byref(info), ctypes.sizeof(info))
    kernel32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(int(process._handle)))
    return job


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------
@dataclass
class Repo:
    path: Path
    name: str  # relative to the scanned folder, with forward slashes
    branch: str | None = None
    remotes: dict[str, str] = field(default_factory=dict)  # name -> fetch URL
    behind: int | None = None
    keys: list[Path] = field(default_factory=list)  # keys with a passphrase its SSH remotes need


@dataclass
class FetchResult:
    repo: Repo
    ok: bool
    output: str


def find_repositories(root: Path, max_depth: int | None = None) -> list[Path]:
    """Every folder below root that contains a .git directory or file; does not look inside one."""
    repos = []
    root_depth = len(root.parts)

    for current, dirs, _ in os.walk(root):
        current_path = Path(current)

        if (current_path / ".git").exists():
            repos.append(current_path)
            dirs.clear()  # a repository's own subfolders are not separate projects
            continue

        if max_depth is not None and len(current_path.parts) - root_depth >= max_depth:
            dirs.clear()
            continue

        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]

    return sorted(repos)


def scan(root: Path, max_depth: int | None = None) -> list[Repo]:
    """The repositories below `root`, not read yet; fast, so the window can show them at once."""
    return [Repo(path, path.relative_to(root).as_posix()) for path in find_repositories(root, max_depth)]


def inspect_all(repos: list[Repo], tools: SshTools, done: Callable[[Repo], None]) -> None:
    """Read every repository's branch and remotes, then the keys with a passphrase it needs.

    `done` runs on a worker thread after each repository. Reading takes about a quarter second
    per repository: Windows starts the git processes one after another, threads or not.
    """
    def one(repo: Repo) -> None:
        _inspect(repo)
        done(repo)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(one, repos))

    destinations = {d for repo in repos for url in repo.remotes.values() if (d := ssh_destination(url))}
    keys = {d: locked_keys(tools, d) for d in destinations}
    for repo in repos:
        needed = [k for url in repo.remotes.values() if (d := ssh_destination(url)) for k in keys[d]]
        repo.keys = list(dict.fromkeys(needed))


def _inspect(repo: Repo) -> None:
    # Two git calls per repository: starting processes is what makes a scan slow on Windows
    _read_branch(repo)
    for line in _git(repo.path, ["config", "--get-regexp", r"^remote\..*\.url$"]).stdout.splitlines():
        key, _, url = line.partition(" ")
        repo.remotes[key.removeprefix("remote.").removesuffix(".url")] = url


def _read_branch(repo: Repo) -> None:
    """The checked-out branch and how far it is behind its upstream (None without one)."""
    fields = "%(HEAD)|%(refname:short)|%(upstream)|%(upstream:track,nobracket)"
    proc = _git(repo.path, ["for-each-ref", f"--format={fields}", "refs/heads"])
    repo.branch, repo.behind = None, None
    for line in proc.stdout.splitlines():
        head, branch, upstream, track = line.split("|", 3)
        if head == "*":
            behind = re.search(r"behind (\d+)", track)
            repo.branch = branch
            repo.behind = int(behind.group(1)) if behind else (0 if upstream and track != "gone" else None)


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------
def fetch_env(tools: SshTools, agent: Agent | None) -> dict[str, str]:
    """Never prompt: a repository whose key is not unlocked fails instead of hanging."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    env["GIT_SSH_COMMAND"] = f'"{tools.ssh.as_posix()}" -o BatchMode=yes'
    if agent is not None:
        env.update(agent.env)
    return env


def fetch(repo: Repo, prune: bool, env: dict[str, str]) -> FetchResult:
    args = ["fetch", "--all", *(["--prune"] if prune else [])]
    try:
        proc = _git(repo.path, args, env=env, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return FetchResult(repo, False, f"timed out after {TIMEOUT}s")
    output = (proc.stdout + proc.stderr).strip()
    if proc.returncode == 0:
        _read_branch(repo)
    return FetchResult(repo, proc.returncode == 0, output)


def fetch_all(
    repos: list[Repo], prune: bool, env: dict[str, str],
    started: Callable[[Repo], None], done: Callable[[FetchResult], None],
) -> None:
    """Fetch `repos` in parallel; the callbacks run on the worker threads."""
    def one(repo: Repo) -> None:
        started(repo)
        try:
            result = fetch(repo, prune, env)
        except Exception as error:  # reported per repository, not lost in the pool
            result = FetchResult(repo, False, str(error))
        done(result)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(one, repos))


def error_line(output: str) -> str:
    """Pick the line of git's output that explains the failure."""
    lines = output.splitlines()
    if not lines:
        return "no output"
    for line in lines:
        if "denied" in line.lower() or line.startswith(("fatal:", "error:")):
            return line
    return lines[0]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _git(repo: Path, args: list[str], env: dict[str, str] | None = None,
         timeout: int = 30) -> subprocess.CompletedProcess:
    return _run(["git", "-C", str(repo), *args], env=env, timeout=timeout)


def _run(args: list[str], env: dict[str, str] | None = None, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, env=env, stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
