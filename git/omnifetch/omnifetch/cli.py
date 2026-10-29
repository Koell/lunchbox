"""`python -m omnifetch [--path DIR] [--depth N]`: open the window. tkinter is imported only once the
arguments are fine, so a missing Tk gets one clear line instead of a traceback."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

NO_TK = (
    "omnifetch: error: the window needs tkinter, which this Python lacks"
    " (Manjaro/Arch: pacman -S tk; Debian/Ubuntu: apt install python3-tk)"
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="omnifetch", description="Tick git repositories below a folder in a window and fetch them."
    )
    parser.add_argument("--path", type=Path, default=None,
                        help="folder to look for repositories in, remembered for the next start"
                             " (default: the one chosen last; the first start asks)")
    parser.add_argument("--depth", type=int, default=None,
                        help="how many folder levels below --path to search (default: all)")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    folder = args.path.expanduser().resolve() if args.path else None
    if folder is not None and not folder.is_dir():
        print(f"omnifetch: error: no such folder: {folder}", file=sys.stderr)
        return 1
    try:
        import tkinter
    except ImportError:
        print(NO_TK, file=sys.stderr)
        return 1
    try:
        root = tkinter.Tk()
    except tkinter.TclError as error:
        print(f"omnifetch: error: no display for the window ({error})", file=sys.stderr)
        return 1

    from .app import App

    App(root, folder, args.depth)
    root.mainloop()
    return 0
