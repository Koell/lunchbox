"""`python -m breakglass install-shortcut`: a desktop icon that opens the window.

Windows gets a "Break glass" shortcut to pythonw (no console window) with the icon drawn in
`icon.py`; Linux gets a .desktop launcher in the applications menu and on the desktop.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import icon
from .state import LOCAL

TOOL = Path(__file__).resolve().parents[1]  # the folder `python -m breakglass` must run in
NAME = "Break glass"

# The values come in through the environment, so no quoting of paths is needed
_LNK = """
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($env:BG_LINK)
$link.TargetPath = $env:BG_TARGET
$link.Arguments = '-m breakglass'
$link.WorkingDirectory = $env:BG_DIR
$link.IconLocation = $env:BG_ICON
$link.Description = 'Emergency save: push all unsaved git work to the servers'
$link.Save()
"""


def install(desktop: Path | None = None) -> Path:
    """Create the shortcut on `desktop` (default: the user's desktop) and return its path."""
    if sys.platform == "win32":
        return _windows(desktop)
    if sys.platform.startswith("linux"):
        return _linux(desktop)
    raise RuntimeError("only Windows and Linux are supported; make an alias to `python3 -m breakglass`"
                       f" run in {TOOL}")


def _python(windowless: bool) -> str:
    exe = Path(sys.executable)
    if windowless:
        pythonw = exe.with_name("pythonw.exe")
        if pythonw.exists():
            return str(pythonw)
    return str(exe)


def _windows(desktop: Path | None) -> Path:
    if desktop is None:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
            capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip()
        desktop = Path(out) if out else Path.home() / "Desktop"
    LOCAL.mkdir(parents=True, exist_ok=True)
    ico = LOCAL / "breakglass.ico"
    ico.write_bytes(icon.ico_bytes())
    link = desktop / f"{NAME}.lnk"
    env = dict(os.environ, BG_LINK=str(link), BG_TARGET=_python(windowless=True), BG_DIR=str(TOOL),
               BG_ICON=str(ico))
    proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _LNK], env=env,
                          capture_output=True, text=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if proc.returncode != 0 or not link.exists():
        raise RuntimeError(f"could not create {link}: {proc.stderr.strip()}")
    return link


def _linux(desktop: Path | None) -> Path:
    entry = "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        f"Name={NAME}",
        "Comment=Emergency save: push all unsaved git work to the servers",
        f"Exec={_python(windowless=False)} -m breakglass",
        f"Path={TOOL}",
        "Terminal=false",
        "",
    ])
    menu = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "applications"
    menu.mkdir(parents=True, exist_ok=True)
    launcher = menu / "breakglass.desktop"
    launcher.write_text(entry, encoding="utf-8")
    launcher.chmod(0o755)
    desktop = desktop or Path.home() / "Desktop"
    if desktop.is_dir():
        copy = desktop / "breakglass.desktop"
        shutil.copyfile(launcher, copy)
        copy.chmod(0o755)
        return copy
    return launcher
