"""Symmetric encryption through the `gpg` binary; the standard library has no ciphers.

The passphrase is the first line on gpg's stdin, the data follows it, so nothing touches the disk.
gpg writes decrypted output before it has checked the integrity of the input, so its output is
only trusted when it exits with 0.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

_BASE = (
    "--batch",
    "--quiet",
    "--no-tty",
    "--no-options",
    "--pinentry-mode",
    "loopback",
    "--passphrase-fd",
    "0",
    "--no-symkey-cache",
)
_ENCRYPT = (
    "--symmetric",
    "--cipher-algo",
    "AES256",
    "--s2k-mode",
    "3",
    "--s2k-digest-algo",
    "SHA512",
    "--s2k-count",
    "65011712",
)


class GpgError(Exception):
    """gpg is missing, or it failed to encrypt or decrypt."""


def encrypt(data: bytes, passphrase: str) -> bytes:
    return _run(_ENCRYPT, data, passphrase, "could not encrypt")


def decrypt(data: bytes, passphrase: str) -> bytes:
    return _run(("--decrypt",), data, passphrase, "wrong passphrase, or the bento is damaged")


def available() -> bool:
    return shutil.which("gpg") is not None


def _run(arguments: tuple[str, ...], data: bytes, passphrase: str, failure: str) -> bytes:
    if "\n" in passphrase or "\r" in passphrase:
        raise GpgError("a passphrase cannot contain line breaks")
    executable = shutil.which("gpg")
    if executable is None:
        raise GpgError("gpg not found; an encrypted bento needs GnuPG on the PATH")
    _ensure_home(executable)
    result = subprocess.run(
        [executable, *_BASE, *arguments, "--output", "-"],
        input=passphrase.encode("utf-8") + b"\n" + data,
        capture_output=True,
    )
    if result.returncode != 0:
        lines = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise GpgError(f"{failure}" + (f" (gpg: {lines[-1].removeprefix('gpg: ')})" if lines else ""))
    return result.stdout


def _ensure_home(executable: str) -> None:
    """Create gpg's home folder if a fresh machine has none: gpg needs it to start its agent, and
    `--no-options` stops gpg from creating it. Only the folder itself, in a parent that exists."""
    home = _home(executable)
    if home is None:
        return
    try:
        home.mkdir(mode=0o700, exist_ok=True)
    except OSError:
        pass  # gpg reports what it misses


def _home(executable: str) -> Path | None:
    """gpg's home folder as gpg itself reports it: `$GNUPGHOME`, `~/.gnupg`, or for Gpg4win
    `%APPDATA%\\gnupg`. None if gpg doesn't say."""
    result = subprocess.run(
        [executable, "--no-options", "--batch", "--version"], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    home = next((line.removeprefix("Home:").strip() for line in result.stdout.splitlines()
                 if line.startswith("Home:")), "")
    if os.name == "nt" and home.startswith("/"):  # Git for Windows' gpg answers /c/Users/...
        cygpath = Path(executable).with_name("cygpath.exe")
        if not cygpath.exists():
            return None
        home = subprocess.run(
            [str(cygpath), "-w", home], capture_output=True, text=True, encoding="utf-8",
            errors="replace",
        ).stdout.strip()
    return Path(home) if home else None
