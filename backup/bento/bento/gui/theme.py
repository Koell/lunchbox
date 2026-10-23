"""The dark look: one palette, applied to ttk styles and the few plain Tk widgets.

The native ttk themes (vista, aqua) ignore most colours, so the styles build on `clam`, which
takes them everywhere. Colours used by the app go through the style names and constants here,
never as literals in the widgets.
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import font, ttk

BG = "#1e1f22"
SURFACE = "#2b2d30"
FIELD = "#191a1c"
RAISED = "#3c3f42"
HOVER = "#4a4d51"
PRESSED = "#56595e"
BORDER = "#43454a"
TEXT = "#dfe1e5"
MUTED = "#9a9da5"
DISABLED = "#6b6e75"
ACCENT = "#3574f0"
ACCENT_HOVER = "#4a84f5"
SELECT = "#2e436e"
ERROR = "#f2697a"
ADDED = "#6fbf73"
UPDATED = "#e5b45b"
REMOVED = ERROR
ON_ACCENT = "#ffffff"
FINDING = "#4d3a1c"

MUTED_LABEL = "Muted.TLabel"
ERROR_LABEL = "Error.TLabel"
SURFACE_FRAME = "Surface.TFrame"
SURFACE_MUTED_LABEL = "Surface.Muted.TLabel"
SURFACE_LABEL = "Surface.TLabel"
SURFACE_HEADING = "Surface.Heading.TLabel"
SURFACE_CHECKBUTTON = "Surface.TCheckbutton"
ACCENT_BUTTON = "Accent.TButton"
MONO = "TkFixedFont"


def apply(root: tk.Tk) -> None:
    """Style `root` and everything created in it afterwards."""
    root.configure(background=BG)
    for pattern, value in (
        ("*Background", BG),
        ("*Foreground", TEXT),
        ("*selectBackground", SELECT),
        ("*selectForeground", TEXT),
        ("*insertBackground", TEXT),
        ("*TCombobox*Listbox.background", FIELD),
    ):
        root.option_add(pattern, value)

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(
        ".",
        background=BG,
        foreground=TEXT,
        fieldbackground=FIELD,
        bordercolor=BORDER,
        lightcolor=BG,
        darkcolor=BG,
        troughcolor=SURFACE,
        selectbackground=SELECT,
        selectforeground=TEXT,
        insertcolor=TEXT,
        focuscolor=ACCENT,
    )
    style.map(".", foreground=[("disabled", DISABLED)])

    style.configure(MUTED_LABEL, foreground=MUTED)
    style.configure(ERROR_LABEL, foreground=ERROR)
    style.configure(SURFACE_FRAME, background=SURFACE)
    style.configure(SURFACE_MUTED_LABEL, background=SURFACE, foreground=MUTED)
    style.configure(SURFACE_LABEL, background=SURFACE)
    style.configure(SURFACE_HEADING, background=SURFACE, font=bold_font(root))

    style.configure(
        "TButton",
        background=RAISED,
        bordercolor=BORDER,
        lightcolor=RAISED,
        darkcolor=RAISED,
        padding=(12, 4),
    )
    style.map(
        "TButton",
        background=[("disabled", SURFACE), ("pressed", PRESSED), ("active", HOVER)],
        lightcolor=[("disabled", SURFACE), ("pressed", PRESSED), ("active", HOVER)],
        darkcolor=[("disabled", SURFACE), ("pressed", PRESSED), ("active", HOVER)],
        bordercolor=[("focus", ACCENT)],
        foreground=[("disabled", DISABLED)],
    )

    style.configure(
        ACCENT_BUTTON, background=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT, foreground=ON_ACCENT
    )
    style.map(
        ACCENT_BUTTON,
        background=[("disabled", SURFACE), ("pressed", SELECT), ("active", ACCENT_HOVER)],
        lightcolor=[("disabled", SURFACE), ("pressed", SELECT), ("active", ACCENT_HOVER)],
        darkcolor=[("disabled", SURFACE), ("pressed", SELECT), ("active", ACCENT_HOVER)],
        foreground=[("disabled", DISABLED)],
    )

    style.configure("TEntry", fieldbackground=FIELD, padding=(6, 3))
    style.map(
        "TEntry",
        fieldbackground=[("readonly", FIELD), ("disabled", BG)],
        bordercolor=[("focus", ACCENT)],
        lightcolor=[("focus", ACCENT)],
    )

    # Tabs: the selected one is part of the surface below it, the others are flat text.
    style.configure(
        "TNotebook", background=BG, bordercolor=BORDER, lightcolor=SURFACE, darkcolor=SURFACE,
        tabmargins=(0, 4, 0, 0),
    )
    style.configure(
        "TNotebook.Tab",
        background=BG,
        foreground=MUTED,
        bordercolor=BG,
        lightcolor=BG,
        darkcolor=BG,
        padding=(14, 6),
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", SURFACE), ("active", RAISED)],
        foreground=[("selected", TEXT), ("active", TEXT)],
        bordercolor=[("selected", BORDER)],
        lightcolor=[("selected", SURFACE), ("active", RAISED)],
        darkcolor=[("selected", SURFACE), ("active", RAISED)],
        expand=[("selected", (0, 0, 0, 0))],
    )

    style.configure("TSeparator", background=BORDER)
    style.configure("TScrollbar", background=RAISED, bordercolor=BG, arrowcolor=MUTED)
    style.map("TScrollbar", background=[("active", HOVER)])
    # Rows sized from the font, so they don't crowd on a scaled display.
    linespace = font.nametofont("TkDefaultFont", root).metrics("linespace")
    style.configure(
        "Treeview",
        background=SURFACE,
        fieldbackground=SURFACE,
        foreground=TEXT,
        bordercolor=BORDER,
        lightcolor=SURFACE,
        darkcolor=SURFACE,
        rowheight=linespace + 8,
    )
    style.map("Treeview", background=[("selected", SELECT)], foreground=[("selected", TEXT)])
    style.configure(
        "Treeview.Heading",
        background=BG,
        foreground=MUTED,
        bordercolor=BORDER,
        lightcolor=BG,
        darkcolor=BG,
        relief="flat",
        padding=(6, 3),
    )
    style.map("Treeview.Heading", background=[("active", RAISED)])
    style.configure("TPanedwindow", background=SURFACE)
    style.configure("Sash", sashthickness=8, gripcount=0, background=SURFACE, bordercolor=SURFACE,
                    lightcolor=SURFACE)
    style.configure(
        "TCheckbutton",
        indicatorbackground=FIELD,
        indicatorforeground=TEXT,
        upperbordercolor=BORDER,
        lowerbordercolor=BORDER,
    )
    style.map(
        "TCheckbutton",
        background=[("active", BG)],
        indicatorbackground=[("pressed", RAISED), ("active", FIELD)],
    )
    style.configure(SURFACE_CHECKBUTTON, background=SURFACE)
    style.map(SURFACE_CHECKBUTTON, background=[("active", SURFACE)])

    dark_title_bar(root)


def checkboxes(widget: tk.Misc) -> dict[str | None, tk.PhotoImage]:
    """Tree checkboxes sized to the font, keyed by tick state ("on", "off", "partial"); None is
    a blank of the same size, so rows without a checkbox keep their text aligned. Keep the
    result: Tk drops images nothing in Python refers to."""
    size = max(13, round(font.nametofont("TkDefaultFont", widget).metrics("linespace") * 0.75))
    gap = max(4, size // 3)
    unit = size / 13

    def near(x: float, y: float, a: tuple[float, float], b: tuple[float, float]) -> float:
        (ax, ay), (bx, by) = a, b
        dx, dy = bx - ax, by - ay
        t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
        return ((x - ax - t * dx) ** 2 + (y - ay - t * dy) ** 2) ** 0.5

    marks = {
        "on": [((3, 6.5), (5.5, 9)), ((5.5, 9), (10, 4))],
        "partial": [((3.5, 6.5), (9.5, 6.5))],
        "off": [],
    }
    images: dict[str | None, tk.PhotoImage] = {None: tk.PhotoImage(master=widget, width=size + gap, height=size)}
    for state, strokes in marks.items():
        image = tk.PhotoImage(master=widget, width=size + gap, height=size)
        filled = state != "off"
        for y in range(size):
            row = []
            for x in range(size):
                edge = x in (0, size - 1) or y in (0, size - 1)
                px, py = (x + 0.5) / unit, (y + 0.5) / unit
                if any(near(px, py, a, b) <= 1.1 for a, b in strokes):
                    row.append(ON_ACCENT)
                elif edge:
                    row.append(ACCENT if filled else MUTED)
                else:
                    row.append(ACCENT if filled else FIELD)
            image.put("{" + " ".join(row) + "}", to=(0, y))
        images[state] = image
    return images


def bold_font(widget: tk.Misc) -> tuple[str, int, str]:
    """The default font in bold, as a description: unlike a font object, it can't be
    garbage-collected while a style or tag still uses it."""
    actual = font.nametofont("TkDefaultFont", widget).actual()
    return (actual["family"], actual["size"], "bold")


def dark_title_bar(window: tk.Misc) -> None:
    """Ask Windows 10/11 for a dark title bar; elsewhere the window manager decides."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE, and its pre-20H1 number
            if not ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
            ):
                break
    except (AttributeError, OSError):
        pass
