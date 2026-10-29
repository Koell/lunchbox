"""Modal dialogs."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from . import theme


def ask_passphrase(parent: tk.Misc, key: Path, used_by: int, error: str | None = None) -> str | None:
    """Ask for the passphrase of SSH key `key` with hidden input; None if the user skips it.

    `used_by` is how many of the ticked repositories need the key; `error` is shown above the
    field, for example after a wrong passphrase.
    """
    dialog = tk.Toplevel(parent, background=theme.BG)
    dialog.title("SSH key passphrase")
    dialog.resizable(False, False)
    dialog.transient(parent.winfo_toplevel())
    theme.dark_title_bar(dialog)

    frame = ttk.Frame(dialog, padding=16)
    frame.grid(sticky="nsew")
    noun = "repository needs" if used_by == 1 else "repositories need"
    ttk.Label(frame, text=f"{used_by} ticked {noun} this key.").grid(
        row=0, column=0, columnspan=2, sticky="w"
    )
    ttk.Label(frame, text=str(key), style=theme.MUTED_LABEL).grid(
        row=1, column=0, columnspan=2, sticky="w", pady=(0, 8)
    )
    if error:
        ttk.Label(frame, text=error, style=theme.ERROR_LABEL, wraplength=420).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
    ttk.Label(frame, text="Passphrase:").grid(row=3, column=0, sticky="w", padx=(0, 8))
    value = tk.StringVar(dialog)
    entry = ttk.Entry(frame, textvariable=value, show="•", width=36)
    entry.grid(row=3, column=1, sticky="ew")
    ttk.Label(
        frame,
        text="Used for this fetch only, never saved.",
        style=theme.MUTED_LABEL,
    ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))

    result: list[str | None] = [None]

    def ok(_event: object = None) -> None:
        if value.get():
            result[0] = value.get()
            dialog.destroy()

    def skip(_event: object = None) -> None:
        dialog.destroy()

    buttons = ttk.Frame(frame)
    buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(12, 0))
    ttk.Button(buttons, text="Unlock", command=ok, style=theme.ACCENT_BUTTON).grid(
        row=0, column=0, padx=(0, 6)
    )
    ttk.Button(buttons, text="Skip these", command=skip).grid(row=0, column=1)
    dialog.bind("<Return>", ok)
    dialog.bind("<Escape>", skip)
    dialog.protocol("WM_DELETE_WINDOW", skip)

    entry.focus_set()
    dialog.wait_visibility()
    dialog.grab_set()
    dialog.wait_window()
    return result[0]
