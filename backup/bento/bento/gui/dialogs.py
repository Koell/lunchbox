"""Modal dialogs."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from ..cli import MIN_PASSPHRASE
from . import model, theme


def ask_passphrase(parent: tk.Misc, bento: Path, error: str | None = None) -> str | None:
    """Ask for the passphrase of `bento` with hidden input; None if the user cancels.

    `error` is shown above the field, for example after a wrong passphrase.
    """
    dialog = tk.Toplevel(parent, background=theme.BG)
    dialog.title("Passphrase")
    dialog.resizable(False, False)
    dialog.transient(parent.winfo_toplevel())
    theme.dark_title_bar(dialog)

    frame = ttk.Frame(dialog, padding=16)
    frame.grid(sticky="nsew")
    ttk.Label(frame, text="This bento is encrypted.").grid(row=0, column=0, columnspan=2, sticky="w")
    ttk.Label(frame, text=str(bento), style=theme.MUTED_LABEL).grid(
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

    result: list[str | None] = [None]

    def ok(_event: object = None) -> None:
        if value.get():
            result[0] = value.get()
            dialog.destroy()

    def cancel(_event: object = None) -> None:
        dialog.destroy()

    buttons = ttk.Frame(frame)
    buttons.grid(row=4, column=0, columnspan=2, sticky="e", pady=(12, 0))
    ttk.Button(buttons, text="Open", command=ok, default="active").grid(row=0, column=0, padx=(0, 6))
    ttk.Button(buttons, text="Cancel", command=cancel).grid(row=0, column=1)
    dialog.bind("<Return>", ok)
    dialog.bind("<Escape>", cancel)
    dialog.protocol("WM_DELETE_WINDOW", cancel)

    entry.focus_set()
    dialog.wait_visibility()
    dialog.grab_set()
    dialog.wait_window()
    return result[0]



def _dialog(parent: tk.Misc, title: str) -> tuple[tk.Toplevel, ttk.Frame]:
    dialog = tk.Toplevel(parent, background=theme.BG)
    dialog.title(title)
    dialog.resizable(False, False)
    dialog.transient(parent.winfo_toplevel())
    theme.dark_title_bar(dialog)
    frame = ttk.Frame(dialog, padding=16)
    frame.grid(sticky="nsew")
    return dialog, frame


def _run(dialog: tk.Toplevel, focus: tk.Misc) -> None:
    focus.focus_set()
    dialog.wait_visibility()
    dialog.grab_set()
    dialog.wait_window()


def confirm(parent: tk.Misc, confirmation: model.Confirmation) -> bool:
    """Ask `confirmation`'s question, listing its cases; True if the user goes ahead."""
    dialog, frame = _dialog(parent, f"{confirmation.action}?")
    ttk.Label(frame, text=confirmation.question, wraplength=460).grid(row=0, column=0, sticky="w")
    row = 1
    for title, items in confirmation.sections:
        ttk.Label(frame, text=title, font=theme.bold_font(frame)).grid(
            row=row, column=0, sticky="w", pady=(12, 4)
        )
        shown = list(items[:15])
        if len(items) > len(shown):
            shown.append(f"… and {len(items) - len(shown)} more")
        ttk.Label(frame, text="\n".join(shown), style=theme.MUTED_LABEL, wraplength=460).grid(
            row=row + 1, column=0, sticky="w", padx=(12, 0)
        )
        row += 2

    result = [False]

    def ok(_event: object = None) -> None:
        result[0] = True
        dialog.destroy()

    def cancel(_event: object = None) -> None:
        dialog.destroy()

    buttons = ttk.Frame(frame)
    buttons.grid(row=row, column=0, sticky="e", pady=(16, 0))
    go = ttk.Button(buttons, text=confirmation.action, command=ok, style=theme.ACCENT_BUTTON)
    go.grid(row=0, column=0, padx=(0, 6))
    cancel_button = ttk.Button(buttons, text="Cancel", command=cancel)
    cancel_button.grid(row=0, column=1)
    dialog.bind("<Escape>", cancel)
    dialog.protocol("WM_DELETE_WINDOW", cancel)
    _run(dialog, cancel_button)
    return result[0]


def ask_new_passphrase(parent: tk.Misc, bento: Path) -> str | None:
    """Ask twice for a new passphrase of at least `MIN_PASSPHRASE` characters, hidden; None if
    the user cancels."""
    dialog, frame = _dialog(parent, "New passphrase")
    ttk.Label(frame, text="New passphrase for the bento").grid(row=0, column=0, columnspan=2, sticky="w")
    ttk.Label(frame, text=str(bento), style=theme.MUTED_LABEL).grid(
        row=1, column=0, columnspan=2, sticky="w", pady=(0, 8)
    )
    error = tk.StringVar(dialog)
    ttk.Label(frame, textvariable=error, style=theme.ERROR_LABEL, wraplength=420).grid(
        row=2, column=0, columnspan=2, sticky="w"
    )
    values = []
    for row, text in ((3, "Passphrase:"), (4, "Repeat:")):
        ttk.Label(frame, text=text).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=2)
        value = tk.StringVar(dialog)
        ttk.Entry(frame, textvariable=value, show="•", width=36).grid(row=row, column=1, sticky="ew")
        values.append(value)
    first_entry = frame.grid_slaves(row=3, column=1)[0]

    result: list[str | None] = [None]

    def ok(_event: object = None) -> None:
        first, second = (v.get() for v in values)
        if len(first) < MIN_PASSPHRASE:
            error.set(f"The passphrase needs at least {MIN_PASSPHRASE} characters.")
        elif first != second:
            error.set("The passphrases do not match.")
        else:
            result[0] = first
            dialog.destroy()

    def cancel(_event: object = None) -> None:
        dialog.destroy()

    buttons = ttk.Frame(frame)
    buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(12, 0))
    ttk.Button(buttons, text="OK", command=ok, default="active").grid(row=0, column=0, padx=(0, 6))
    ttk.Button(buttons, text="Cancel", command=cancel).grid(row=0, column=1)
    dialog.bind("<Return>", ok)
    dialog.bind("<Escape>", cancel)
    dialog.protocol("WM_DELETE_WINDOW", cancel)
    _run(dialog, first_entry)
    return result[0]
