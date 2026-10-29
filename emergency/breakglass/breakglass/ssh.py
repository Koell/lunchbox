"""SSH for the pushes: Git for Windows' own ssh tools, the keys a remote needs, and a private
ssh-agent per save run. No tkinter here.

Keys with a passphrase are unlocked into an ssh-agent that lives for one run only (`Agent`): the
passphrase goes to a single ssh-add call through its environment and is never written anywhere. The
agent and its folder are removed when the run ends, and on Windows the agent is also killed if
Python dies, so unlocked keys never outlive the run.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .proc import run

# Called by ssh-add instead of a terminal prompt. The passphrase comes from the environment of that
# one ssh-add call; a second prompt means it was wrong, so give up instead of looping.
ASKPASS = """#!/bin/sh
case "$1" in
    Bad*) exit 1 ;;
esac
printf '%s\\n' "$BREAKGLASS_PASSPHRASE"
"""


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
        return run([str(self.cygpath), "-u", str(path)]).stdout.strip()

    def native(self, path: str) -> Path:
        if self.cygpath is None or not path.startswith("/"):
            return Path(path)
        return Path(run([str(self.cygpath), "-w", path]).stdout.strip())


def find_ssh_tools() -> SshTools:
    if sys.platform == "win32":
        exec_path = run(["git", "--exec-path"]).stdout.strip()  # <Git>/mingw64/libexec/git-core
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
    for line in run(args).stdout.splitlines():
        name, _, value = line.partition(" ")
        if name.lower() != "identityfile":
            continue
        key = Path.home() / value[2:] if value.startswith("~/") else tools.native(value)
        if key.is_file() and _has_passphrase(tools, key):
            keys.append(key)
    return keys


def _has_passphrase(tools: SshTools, key: Path) -> bool:
    return run([str(tools.keygen), "-y", "-P", "", "-f", str(key)]).returncode != 0


class Agent:
    """A private ssh-agent for one save run; `close` stops it, which forgets every key."""

    def __init__(self, tools: SshTools) -> None:
        self.tools = tools
        self.folder = Path(tempfile.mkdtemp(prefix="breakglass-"))
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
            BREAKGLASS_PASSPHRASE=passphrase,
        )
        try:
            proc = run([str(self.tools.add), str(key)], env=env, timeout=20)
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


def ssh_command(tools: SshTools) -> str:
    """GIT_SSH_COMMAND: the same ssh the agent belongs to, never asking anything."""
    return f'"{tools.ssh.as_posix()}" -o BatchMode=yes'
