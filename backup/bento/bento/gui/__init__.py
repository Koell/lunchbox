"""The GUI: `python -m bento` without a command. tkinter is imported only here, so the CLI works without it."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from ..manifest import Manifest

NO_TK = (
    "bento: error: the GUI needs tkinter, which this Python lacks"
    " (Manjaro/Arch: pacman -S tk; Debian/Ubuntu: apt install python3-tk);"
    " the CLI works without it"
)


def run(manifest: Callable[[], Manifest], requested: Path | None = None) -> int:
    """Open the window and return the exit code once it is closed; `manifest` reads
    `bento.toml`, again on every Refresh."""
    try:
        import tkinter
    except ImportError:
        print(NO_TK, file=sys.stderr)
        return 1
    try:
        root = tkinter.Tk()
    except tkinter.TclError as error:
        print(
            f"bento: error: no display for the GUI ({error});"
            " use the CLI: python -m bento status, deploy or capture",
            file=sys.stderr,
        )
        return 1

    from .app import App

    App(root, manifest, requested)
    root.mainloop()
    return 0
