"""The window icon: a bento box seen from above, drawn as pixel art so no image file is needed.

Rice with a pickled plum on the left, greens top right, salmon with egg bottom right, in a red
lacquer box. Each character of `PIXELS` is one pixel of the 16×16 design; larger sizes scale it
without smoothing, which keeps the edges crisp.
"""

from __future__ import annotations

import sys
import tkinter as tk

PIXELS = (
    "................",
    ".KRRRRRRRRRRRRK.",
    "KRRRRRRRRRRRRRRK",
    "KRWWWWWWDGGgGGRK",
    "KRWWWWWWDGgGGGRK",
    "KRWWWWWWDGGGgGRK",
    "KRWWPPWWDGGGGGRK",
    "KRWWPPWWDDDDDDRK",
    "KRWWWWWWDOOOOORK",
    "KRWWWWWWDOOOYYRK",
    "KRWWWWWWDOOOYYRK",
    "KRWWWWWWDOOOOORK",
    "KRRRRRRRRRRRRRRK",
    ".KRRRRRRRRRRRRK.",
    "..KKKKKKKKKKKK..",
    "................",
)

COLORS = {
    "K": "#5a1712",  # lacquer edge
    "R": "#b8352a",  # red lacquer
    "D": "#6e1d16",  # divider
    "W": "#f3efe4",  # rice
    "P": "#d2364a",  # pickled plum
    "G": "#5fae4a",  # greens
    "g": "#8fd16e",  # light greens
    "O": "#f2925f",  # salmon
    "Y": "#f7d154",  # egg
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

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("bento.gui")
        except (AttributeError, OSError):
            pass
    drawn = images(root)
    root.iconphoto(True, *drawn)
    return drawn
