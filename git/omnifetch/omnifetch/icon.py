"""The window icon: a git branch with an arrow pulling commits down, drawn as pixel art so no image
file is needed. Each character of `PIXELS` is one pixel of the 16×16 design; larger sizes scale it
without smoothing, which keeps the edges crisp.
"""

from __future__ import annotations

import sys
import tkinter as tk

PIXELS = (
    "................",
    "...OO......AA...",
    "..OOOO.....AA...",
    "..OOOO.....AA...",
    "...OO......AA...",
    "...LL....AAAAAA.",
    "...LL.....AAAA..",
    "...LL......AA...",
    "...LL...........",
    "...LL......OO...",
    "...LL.....OOOO..",
    "...LLL....OOOO..",
    "...OOLL...LOO...",
    "..OOOOLLLLL.....",
    "..OOOO..........",
    "...OO...........",
)

COLORS = {
    "O": "#f05133",  # git orange commit
    "L": "#c43e22",  # branch line
    "A": "#3574f0",  # fetch arrow, the app's accent
}

SCALES = (1, 2, 3, 4, 8)


def images(master: tk.Misc, scales: tuple[int, ...] = SCALES) -> list[tk.PhotoImage]:
    """The icon at 16 px times each of `scales`, largest first."""
    drawn = []
    for scale in sorted(scales, reverse=True):
        size = len(PIXELS) * scale
        image = tk.PhotoImage(master=master, width=size, height=size)
        for y, row in enumerate(PIXELS):
            for x, key in enumerate(row):
                if key in COLORS:
                    image.put(
                        COLORS[key], to=(x * scale, y * scale, (x + 1) * scale, (y + 1) * scale)
                    )
        drawn.append(image)
    return drawn


def apply(root: tk.Tk) -> list[tk.PhotoImage]:
    """Use the icon for `root` and every later window; keep the result, or Tk drops the images."""
    if sys.platform == "win32":
        # Without its own app id, Windows shows python.exe's icon in the taskbar.
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("omnifetch.gui")
        except (AttributeError, OSError):
            pass
    drawn = images(root)
    root.iconphoto(True, *drawn)
    return drawn
