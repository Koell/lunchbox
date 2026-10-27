"""A tab's file tree: a toolbar, the tree of modules, folders and files, and the details pane.

It shows a `model.Tree` and lets the user tick, allow and look at files; what the tree holds and
what the action button does is up to the tab.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from . import model, theme

HIGH_RISK = "high risk"
_TAGS = {
    model.ADDED: {"foreground": theme.ADDED},
    model.UPDATED: {"foreground": theme.UPDATED},
    model.REMOVED: {"foreground": theme.REMOVED},
    model.REFUSED: {"foreground": theme.ERROR},
    HIGH_RISK: {"foreground": theme.HIGH_RISK},
    "muted": {"foreground": theme.MUTED},
}
_DIFF_TAGS = {
    "header": {"foreground": theme.MUTED},
    "hunk": {"foreground": theme.MUTED},
    "add": {"foreground": theme.ADDED},
    "remove": {"foreground": theme.REMOVED},
    "finding": {"background": theme.FINDING},
    "note": {"foreground": theme.MUTED},
}


class FileTree:
    """`frame` holds the toolbar, the tree and the details pane; `show` replaces what it shows.

    Which folders are open and which node is selected survive `show`, so a refresh keeps the
    user's place; `show(..., reset=True)` starts over, for another bento.
    """

    def __init__(
        self,
        parent: tk.Misc,
        refresh: Callable[[], None],
        action: str,
        run: Callable[[], None],
    ) -> None:
        self.tree: model.Tree | None = None
        self.message = ""
        self.drop_unmanaged: tk.BooleanVar | None = None
        self._busy = False
        self._blocked = False
        self._open: set[str] = set()
        self._closed: set[str] = set()
        self._revealed: str | None = None
        self._checks = theme.checkboxes(parent)

        self.frame = ttk.Frame(parent, padding=(12, 10, 12, 12), style=theme.SURFACE_FRAME)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)

        self.toolbar = ttk.Frame(self.frame, style=theme.SURFACE_FRAME)
        self.toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.toolbar.columnconfigure(10, weight=1)
        self.refresh_button = ttk.Button(self.toolbar, text="Refresh", command=refresh)
        self.refresh_button.grid(row=0, column=0)
        self.show_unchanged = tk.BooleanVar(self.frame, value=False)
        self.checkbutton("Show unchanged", self.show_unchanged, column=1)
        self.summary = tk.StringVar(self.frame)
        ttk.Label(self.toolbar, textvariable=self.summary, style=theme.SURFACE_MUTED_LABEL).grid(
            row=0, column=10, sticky="e", padx=(12, 12)
        )
        self.action_button = ttk.Button(
            self.toolbar, text=action, command=run, style=theme.ACCENT_BUTTON, state="disabled"
        )
        self.action_button.grid(row=0, column=11)

        panes = ttk.Panedwindow(self.frame, orient="horizontal")
        panes.grid(row=1, column=0, sticky="nsew")

        left = ttk.Frame(panes, style=theme.SURFACE_FRAME)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        self.view = ttk.Treeview(left, columns=("state",), selectmode="browse")
        self.view.heading("#0", text="Name", anchor="w")
        self.view.heading("state", text="State", anchor="w")
        self.view.column("#0", width=280, minwidth=180, stretch=True)
        self.view.column("state", width=120, minwidth=90, stretch=False)
        for tag, options in _TAGS.items():
            self.view.tag_configure(tag, **options)
        self.view.tag_configure(model.MODULE, font=theme.bold_font(self.view))
        self.view.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.view.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.view.configure(yscrollcommand=scroll.set)
        self.view.bind("<<TreeviewSelect>>", self._selected)
        self.view.bind("<<TreeviewOpen>>", lambda e: self._toggled(True))
        self.view.bind("<<TreeviewClose>>", lambda e: self._toggled(False))
        self.view.bind("<Button-1>", self._clicked)
        self.view.bind("<space>", lambda e: self.toggle(self.selected) or "break")
        panes.add(left, weight=1)

        self.details = ttk.Frame(panes, padding=(14, 2, 0, 0), style=theme.SURFACE_FRAME)
        self.details.columnconfigure(0, weight=1)
        self.details.rowconfigure(2, weight=1)
        self.info = ttk.Frame(self.details, style=theme.SURFACE_FRAME)
        self.info.grid(row=0, column=0, sticky="new")
        self.info.columnconfigure(1, weight=1)
        self.info.bind("<Configure>", lambda e: self._wrap())
        self._values: list[ttk.Label] = []
        self.controls = ttk.Frame(self.details, style=theme.SURFACE_FRAME)
        self.controls.grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.allow = tk.BooleanVar(self.frame, value=False)
        self.allow_button = ttk.Checkbutton(
            self.controls,
            text="Allow this file",
            variable=self.allow,
            command=self._allow_changed,
            style=theme.SURFACE_CHECKBUTTON,
        )
        self.show_button = ttk.Button(self.controls, text="Show", command=self._reveal)

        diff = ttk.Frame(self.details, style=theme.SURFACE_FRAME)
        diff.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        diff.columnconfigure(0, weight=1)
        diff.rowconfigure(0, weight=1)
        self.diff = tk.Text(
            diff,
            wrap="none",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            background=theme.FIELD,
            foreground=theme.TEXT,
            font=theme.MONO,
            state="disabled",
            padx=6,
            pady=4,
        )
        for tag, options in _DIFF_TAGS.items():
            self.diff.tag_configure(tag, **options)
        self.diff.grid(row=0, column=0, sticky="nsew")
        ys = ttk.Scrollbar(diff, orient="vertical", command=self.diff.yview)
        xs = ttk.Scrollbar(diff, orient="horizontal", command=self.diff.xview)
        self.diff.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        self._diff_frame = diff
        diff.grid_remove()
        panes.add(self.details, weight=1)

    def checkbutton(self, text: str, variable: tk.BooleanVar, column: int) -> None:
        """A toolbar checkbox that redraws the tree when it changes."""
        ttk.Checkbutton(
            self.toolbar, text=text, variable=variable, command=self._render,
            style=theme.SURFACE_CHECKBUTTON,
        ).grid(row=0, column=column, padx=(12, 0))

    def offer_drop_unmanaged(self) -> None:
        """Add the checkbox that drops what `bento.toml` no longer manages (off by default)."""
        self.drop_unmanaged = tk.BooleanVar(self.frame, value=False)
        self.checkbutton("Drop entries bento.toml no longer manages", self.drop_unmanaged, column=2)

    def set_busy(self, busy: bool) -> None:
        """While background work runs, Refresh and the action wait."""
        self._busy = busy
        self.refresh_button.configure(state="disabled" if busy else "normal")
        self._update_action()

    def set_blocked(self, blocked: bool) -> None:
        """While `bento.toml` is broken, the action waits; Refresh stays available."""
        self._blocked = blocked
        self._update_action()

    def _update_action(self) -> None:
        idle = self.tree is not None and not self._busy and not self._blocked
        self.action_button.configure(state="normal" if idle else "disabled")

    def show(self, tree: model.Tree | None, message: str = "", *, reset: bool = False) -> None:
        """Show `tree`, or `message` when there is nothing to show."""
        if reset:
            self._open.clear()
            self._closed.clear()
        self.tree = tree
        self.message = message
        self._render()

    @property
    def selected(self) -> str | None:
        selection = self.view.selection()
        return selection[0] if selection else None

    def toggle(self, id: str | None) -> None:
        """Tick or untick `id` and everything below it."""
        if self.tree is None or id is None or self.tree.check(id) is None:
            return
        self.tree.toggle(id)
        self._render()

    def _clicked(self, event: tk.Event) -> str | None:
        id = self.view.identify_row(event.y)
        if id and self.view.identify_element(event.x, event.y) == "image":
            self.view.selection_set(id)
            self.toggle(id)
            return "break"
        return None

    def _render(self) -> None:
        selected = self.selected
        self.view.delete(*self.view.get_children())
        tree = self.tree
        self._update_action()
        if tree is None:
            self.summary.set(self.message)
            self._show_details(None)
            return
        if self.drop_unmanaged is not None:
            tree.drop_unmanaged = self.drop_unmanaged.get()
        show_unchanged = self.show_unchanged.get()

        def insert(id: str, parent: str) -> None:
            node = tree.node(id)
            tags = [node.kind]
            state = tree.state(id) if node.kind == model.FILE else None
            if tree.muted(id):
                tags.append("muted")
            elif node.kind == model.FILE and tree.high_risk(id):
                tags.append(HIGH_RISK)
            elif state in _TAGS:
                tags.append(state)
            self.view.insert(
                parent,
                "end",
                iid=id,
                text=tree.label(id),
                image=self._checks[tree.check(id)],
                values=(tree.status(id),),
                open=self._is_open(tree, id),
                tags=tags,
            )
            for child in tree.children(id, show_unchanged):
                insert(child, id)

        for root in tree.roots:
            insert(root, "")
        total = tree.total_changes()
        self.summary.set(model.count_text(total, "change") if total else "no changes")
        if selected and self.view.exists(selected):
            self.view.selection_set(selected)
            self.view.see(selected)
            self._show_details(selected)
        else:
            self._show_details(None)

    def _is_open(self, tree: model.Tree, id: str) -> bool:
        """Modules start open, folders open if they hold changes; the user's choice wins."""
        if id in self._open or id in self._closed:
            return id in self._open
        node = tree.node(id)
        return node.kind == model.MODULE or tree.changes(id) > 0

    def _toggled(self, opened: bool) -> None:
        id = self.view.focus()
        if not id:
            return
        (self._open if opened else self._closed).add(id)
        (self._closed if opened else self._open).discard(id)

    def _selected(self, _event: object = None) -> None:
        if self.selected != self._revealed:
            self._revealed = None
        self._show_details(self.selected)

    def _reveal(self) -> None:
        self._revealed = self.selected
        self._show_details(self.selected)

    def _allow_changed(self) -> None:
        id = self.selected
        if self.tree is None or id is None:
            return
        if self.allow.get():
            self.tree.allowed.add(id)
        else:
            self.tree.allowed.discard(id)
        self._render()

    def _show_details(self, id: str | None) -> None:
        for child in self.info.winfo_children():
            child.destroy()
        self._values = []
        self.allow_button.grid_remove()
        self.show_button.grid_remove()
        tree = self.tree
        if tree is None or id is None or id not in tree.nodes:
            text = self.message if tree is None else "Select a module, folder or file."
            label = ttk.Label(self.info, text=text, style=theme.SURFACE_MUTED_LABEL)
            label.grid(row=0, column=0, columnspan=2, sticky="nw")
            self._values.append(label)
            self._wrap()
            self._show_diff(None)
            return
        ttk.Label(self.info, text=tree.node(id).name, style=theme.SURFACE_HEADING).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        for row, (label, value) in enumerate(tree.details(id), start=1):
            ttk.Label(self.info, text=label, style=theme.SURFACE_MUTED_LABEL).grid(
                row=row, column=0, sticky="nw", padx=(0, 12), pady=2
            )
            label = ttk.Label(self.info, text=value, style=theme.SURFACE_LABEL)
            label.grid(row=row, column=1, sticky="nw", pady=2)
            self._values.append(label)
        self.info.update_idletasks()
        self._wrap()

        node = tree.node(id)
        if tree.can_allow(id):
            self.allow.set(id in tree.allowed)
            self.allow_button.grid(row=0, column=1, sticky="w")
        diff = tree.diff(id, reveal=self._revealed == id) if node.kind == model.FILE else None
        if diff is not None and diff.hidden:
            self.show_button.grid(row=0, column=0, sticky="w", padx=(0, 12))
        self._show_diff(diff)

    def _show_diff(self, diff: model.Diff | None) -> None:
        if diff is None:
            self._diff_frame.grid_remove()
            return
        self._diff_frame.grid()
        text = self.diff
        text.configure(state="normal")
        text.delete("1.0", "end")
        for line, kind, highlighted in diff.lines:
            text.insert("end", line + "\n", (kind, "finding") if highlighted else (kind,))
        if diff.note:
            text.insert("end", diff.note + "\n", ("note",))
        text.configure(state="disabled")

    def _wrap(self) -> None:
        """Wrap the values to the width the details pane has."""
        for label in self._values:
            column = int(label.grid_info().get("column", 0))
            left = self.info.grid_bbox(0, 0)[2] if column else 0
            label.configure(wraplength=max(self.info.winfo_width() - left - 30, 120))

    def rows(self, parent: str = "") -> list[tuple[str, str, str]]:
        """The shown rows below `parent` as (id, label, state), depth first; for tests."""
        rows = []
        for id in self.view.get_children(parent):
            rows.append((id, self.view.item(id, "text"), self.view.set(id, "state")))
            rows.extend(self.rows(id))
        return rows

    def diff_text(self) -> str:
        """What the diff view shows, empty if it is hidden; for tests."""
        if not self._diff_frame.winfo_ismapped() and not self._diff_frame.grid_info():
            return ""
        return self.diff.get("1.0", "end-1c")
