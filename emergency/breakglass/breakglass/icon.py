"""The icon: a fire alarm box, a red button behind cracked glass, drawn as pixel art so no image
file is needed. Each character of `PIXELS` is one pixel of the 16×16 design; larger sizes scale it
without smoothing, which keeps the edges crisp. The window draws it with Tk; the desktop shortcut
gets it as an .ico written by `ico_bytes`, which needs no Tk.
"""

from __future__ import annotations

import struct
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import tkinter as tk

PIXELS = (
    "................",
    ".KKKKKKKKKKKKKK.",
    ".KRRRRRRRRRRRRK.",
    ".KRGGGGGWGGGGRK.",
    ".KRGGGGWGGGGGRK.",
    ".KRGGGWGBBGGGRK.",
    ".KRGWWGBBBBGGRK.",
    ".KRGGGWBBBBWGRK.",
    ".KRGGGGWBBWGGRK.",
    ".KRGGGGGWWGGGRK.",
    ".KRGGGGWGGWGGRK.",
    ".KRGGGGWGGGWGRK.",
    ".KRRRRRRRRRRRRK.",
    ".KKKKKKKKKKKKKK.",
    "....KK....KK....",
    "................",
)

COLORS = {
    "K": "#5a1712",  # box edge
    "R": "#d63a2f",  # red box
    "G": "#b9d3df",  # glass
    "W": "#ffffff",  # cracks
    "B": "#a3160f",  # the button behind the glass
}

SCALES = (1, 2, 3, 4, 8)


def images(master: tk.Misc, scales: tuple[int, ...] = SCALES) -> list[tk.PhotoImage]:
    """The icon at 16 px times each of `scales`, largest first."""
    import tkinter as tk

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


def apply(root: tk.Misc) -> list[tk.PhotoImage]:
    """Use the icon for `root` and every later window; keep the result, or Tk drops the images."""
    if sys.platform == "win32":
        # Without its own app id, Windows shows python.exe's icon in the taskbar.
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("breakglass.gui")
        except (AttributeError, OSError):
            pass
    drawn = images(root)
    root.iconphoto(True, *drawn)
    return drawn


def ico_bytes(scales: tuple[int, ...] = (1, 2, 3)) -> bytes:
    """The icon as a Windows .ico with 16, 32 and 48 px images (32-bit bitmaps)."""
    images = []
    for scale in scales:
        size = len(PIXELS) * scale
        rows = []
        for y in reversed(range(size)):  # bitmaps are stored bottom-up
            row = bytearray()
            for x in range(size):
                key = PIXELS[y // scale][x // scale]
                if key in COLORS:
                    r, g, b = (int(COLORS[key][i:i + 2], 16) for i in (1, 3, 5))
                    row += bytes((b, g, r, 255))
                else:
                    row += bytes(4)
            rows.append(bytes(row))
        mask_row = bytes(((size + 31) // 32) * 4)  # all zero: the alpha channel decides
        header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
        images.append((size, header + b"".join(rows) + mask_row * size))

    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for size, data in images:
        out += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return out + b"".join(data for _, data in images)
