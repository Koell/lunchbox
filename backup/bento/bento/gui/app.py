"""The main window: the bento bar, the Capture and Deploy tabs, and the result panel.

Slow work (reading a bento, scanning the machine, gpg, writing) runs in a worker thread; its
result comes back to the Tk thread through a queue polled with `after`, since Tk may only be used
from the thread that created it.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from collections.abc import Callable, Sequence
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Any

from .. import archive, ops
from ..archive import Bento
from ..manifest import Module
from . import dialogs, icon, model, theme
from .state import State, initial_bento
from .tree import FileTree

AskPassphrase = Callable[[tk.Misc, Path, str | None], str | None]
AskNewPassphrase = Callable[[tk.Misc, Path], str | None]
Confirm = Callable[[tk.Misc, model.Confirmation], bool]
Stamp = tuple[int, int] | None

TITLE = "Backup, Encrypt 'n' Take Out"
_POLL_MS = 50
_FILETYPES = [("Bento", "*.tar.gz"), ("All files", "*")]
_STALE_BENTO = (
    "The bento changed on disk since it was read, perhaps by a capture in a terminal."
    " Nothing was written; it has been read again and your ticks are kept."
)


def _stamp(path: Path) -> Stamp:
    """The bento file's modification time and size, None if there is none."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_mtime_ns, stat.st_size


class App:
    """One window working on one bento at a time.

    `contents` is the open bento once it has been read, `passphrase` the one it was opened with,
    `pending` the passphrase a first capture encrypts a new bento with. They are only ever kept
    in memory and are dropped when another bento is opened. `stamp` is the bento file as it was
    when read; nothing is written over a bento that changed since.
    """

    def __init__(
        self,
        root: tk.Tk,
        modules: Sequence[Module],
        requested: Path | None = None,
        *,
        state: State | None = None,
        ask_passphrase: AskPassphrase = dialogs.ask_passphrase,
        ask_new_passphrase: AskNewPassphrase = dialogs.ask_new_passphrase,
        confirm: Confirm = dialogs.confirm,
    ) -> None:
        self.root = root
        self.modules = list(modules)
        self.state = state or State()
        self.ask_passphrase = ask_passphrase
        self.ask_new_passphrase = ask_new_passphrase
        self.confirm = confirm
        self.path: Path = archive.DEFAULT_PATH
        self.contents: Bento | None = None
        self.passphrase: str | None = None
        self.pending: str | None = None
        self.stamp: Stamp = None
        self.locked = False
        self._generation = 0
        self._busy = 0
        self._results: queue.Queue[tuple[int, bool, Any, Callable, Callable | None]] = queue.Queue()

        root.title(TITLE)
        theme.apply(root)
        self._icons = icon.apply(root)
        root.geometry("1100x720")
        root.minsize(760, 500)
        self._build()
        self._after = root.after(_POLL_MS, self._poll)
        root.bind("<Destroy>", self._destroyed, add="+")

        path, note = initial_bento(self.state, requested)
        self.open(path)
        if note:
            self.show(note)

    # Bento

    def open(self, path: Path) -> None:
        """Switch to the bento at `path`; a missing file is fine, a first capture creates it."""
        self.path = Path(path)
        self.passphrase = None
        self.pending = None
        self.path_var.set(str(self.path))
        self._remember()
        for tab in (self.capture, self.deploy):
            tab.show(None, "Reading the bento…", reset=True)
        self._read(carry=False)

    def refresh(self) -> None:
        """Read the bento and this machine again, keeping the passphrase and the ticks."""
        if self.locked:
            self.unlock()
        else:
            self._read()

    def unlock(self) -> None:
        if self.locked:
            self.locked = False
            self._probed(True)

    def _read(self, *, carry: bool = True, carry_capture: bool | None = None) -> None:
        """Read the bento and plan both tabs; `carry` keeps the ticks the user changed."""
        self._generation += 1
        self.contents = None
        self.locked = False
        self.stamp = _stamp(self.path)
        self._carry = (carry, carry if carry_capture is None else carry_capture)
        self._update_bar()
        if self.stamp is None:
            self._describe(
                "not found: the first capture writes the bento here"
                + (", encrypted" if self.pending else "")
            )
            self._plan(None)
            return
        self._describe("reading…")
        path = self.path
        self._in_background(
            lambda: archive.is_encrypted(path), self._probed, lambda e: self._load_failed(e, False)
        )

    def _probed(self, encrypted: bool, error: str | None = None) -> None:
        if encrypted and self.passphrase is None:
            passphrase = self.ask_passphrase(self.root, self.path, error)
            if passphrase is None:
                self.locked = True
                self._describe("encrypted, locked: no passphrase entered")
                self.unlock_button.grid()
                self._unavailable("Locked: unlock the bento to see its files.")
                return
            self.passphrase = passphrase
        self.unlock_button.grid_remove()
        path, passphrase = self.path, self.passphrase
        self._in_background(
            lambda: ops.load(path, lambda: passphrase),
            self._loaded,
            lambda e: self._load_failed(e, encrypted),
        )

    def _loaded(self, contents: Bento | None) -> None:
        if contents is None:
            self._describe("not found: the first capture writes the bento here")
            self._plan(None)
            return
        self.contents = contents
        when = (
            "capture time unknown"
            if contents.captured is None
            else f"captured {contents.captured.astimezone():%Y-%m-%d %H:%M}"
        )
        modules = contents.modules()
        self._describe(
            f"{when}  ·  {'encrypted' if contents.encrypted else 'not encrypted'}"
            f"  ·  {model.count_text(len(modules), 'module')},"
            f" {model.count_text(len(contents.files), 'file')}"
        )
        for warning in contents.warnings:
            self.show(f"Warning: {warning}")
        self._update_bar()
        self._plan(contents)

    def _plan(self, contents: Bento | None) -> None:
        """Build both tabs' trees in the background."""
        path, modules, encrypt = self.path, self.modules, self.pending is not None

        def work() -> tuple[model.Tree | None, model.Tree]:
            deploy = model.deploy_tree(contents, modules) if contents is not None else None
            return deploy, model.capture_tree(path, contents, modules, encrypt=encrypt)

        self._in_background(work, self._planned)

    def _planned(self, trees: tuple[model.Tree | None, model.Tree]) -> None:
        deploy, capture = trees
        carry_deploy, carry_capture = self._carry
        if deploy is None:
            self.deploy.show(None, "No bento yet: there is nothing to deploy.")
        else:
            if carry_deploy and self.deploy.tree is not None:
                deploy.carry(self.deploy.tree)
            self.deploy.show(deploy)
        if carry_capture and self.capture.tree is not None:
            capture.carry(self.capture.tree)
        self.capture.show(capture)

    def _load_failed(self, error: BaseException, encrypted: bool) -> None:
        if encrypted and "wrong passphrase" in str(error):
            self.passphrase = None
            self._probed(True, "Wrong passphrase, or the bento is damaged. Try again.")
            return
        self._describe("can't be read", error=True)
        self.show(f"Error: {error}", error=True)
        self._unavailable("The bento can't be read.")

    def _unavailable(self, message: str) -> None:
        for tab in (self.capture, self.deploy):
            tab.show(None, message)
        self._update_bar()

    def _remember(self) -> None:
        try:
            self.state.remember(self.path)
        except OSError as error:
            self.show(f"Could not remember the bento in {self.state.path}: {error}", error=True)

    # Deploy

    def run_deploy(self) -> None:
        """Write the ticked added and updated files onto this machine; overwriting asks first."""
        tree, contents, modules = self.deploy.tree, self.contents, self.modules
        if tree is None or contents is None or self.busy:
            return
        if not tree.total_changes():
            self.show("Nothing to deploy: every ticked file is already on this machine.")
            return
        confirmation = tree.confirm_deploy()
        if confirmation is not None and not self.confirm(self.root, confirmation):
            return
        skips = tree.skips()

        def work() -> tuple[list[str], list[str]]:
            planned = ops.plan_deploy(contents, modules)
            changed = tree.stale(model.deploy_tree(contents, modules, planned=planned))
            if changed:
                return changed, []
            applied = model.with_skips(planned, skips)
            ops.apply_deploy(applied)
            return [], model.deploy_report(applied)

        self._in_background(work, self._deployed, self._action_failed)

    def _deployed(self, outcome: tuple[list[str], list[str]]) -> None:
        changed, report = outcome
        if changed:
            self._stale_machine("Deploy", changed)
            return
        self.show_lines(report)
        self._read(carry=False, carry_capture=True)

    # Capture

    def run_capture(self) -> None:
        """Write the ticked files into the bento and store the unticks as its selection.

        Asks first only when files leave the bento, something is refused or allowed despite
        findings, or the encryption changes.
        """
        tree = self.capture.tree
        if tree is None or self.busy:
            return
        path, previous, modules, stamp = self.path, self.contents, self.modules, self.stamp
        options = dict(
            selection=tree.picks(),
            allowed=frozenset(tree.allowed),
            drop_unmanaged=tree.drop_unmanaged,
            encrypt=self.pending is not None,
        )

        def plan() -> ops.CapturePlan:
            return ops.plan_capture(path, previous, modules, **options)

        def prepare() -> tuple[str, Any]:
            if _stamp(path) != stamp:
                return "bento", None
            fresh = model.capture_tree(path, previous, modules, encrypt=options["encrypt"])
            changed = tree.stale(fresh)
            if changed:
                return "machine", changed
            return "plan", plan()

        def prepared(outcome: tuple[str, Any]) -> None:
            kind, value = outcome
            if kind == "bento":
                self.show(_STALE_BENTO, error=True)
                self._read()
            elif kind == "machine":
                self._stale_machine("Capture", value)
            else:
                self._confirm_capture(value, plan, stamp)

        self._in_background(prepare, prepared, self._action_failed)

    def _confirm_capture(
        self, planned: ops.CapturePlan, plan: Callable[[], ops.CapturePlan], stamp: Stamp
    ) -> None:
        if planned.blocked:
            self.show(f"Capture refused: {planned.blocked}", error=True)
            return
        confirmation = model.confirm_capture(planned)
        if confirmation is not None and not self.confirm(self.root, confirmation):
            return
        path, passphrase, pending = self.path, self.passphrase, self.pending

        def write() -> tuple[str, Any]:
            if _stamp(path) != stamp:
                return "bento", None
            again = plan()
            before, after = planned.bento, again.bento
            if before.files != after.files or before.meta != after.meta:
                names = set(before.files) | set(after.files)
                return "machine", sorted(n for n in names if before.files.get(n) != after.files.get(n))
            if not planned.changed:
                return "done", None
            return "done", ops.apply_capture(
                planned, path, passphrase=lambda: passphrase or "", new_passphrase=lambda: pending or ""
            )

        def written(outcome: tuple[str, Any]) -> None:
            kind, value = outcome
            if kind == "bento":
                self.show(_STALE_BENTO, error=True)
                self._read()
                return
            if kind == "machine":
                self._stale_machine("Capture", value)
                return
            self.show_lines(model.capture_report(planned, value, path))
            if value is not None and planned.encrypt:
                self.passphrase, self.pending = pending, None
            self._read(carry=True, carry_capture=False)

        self._in_background(write, written, self._action_failed)

    # Encryption

    def encrypt(self) -> None:
        """Set or change the passphrase: rewrite the bento encrypted, or, without a bento yet,
        have the first capture write it encrypted."""
        if self.busy or self.locked:
            return
        if self.contents is None and self.stamp is not None:
            return  # a bento that can't be read
        new = self.ask_new_passphrase(self.root, self.path)
        if new is None:
            return
        if self.contents is None:
            self.pending = new
            self.show("The first capture writes the bento encrypted with the new passphrase.")
            self._read()
            return
        if self.confirm(self.root, model.confirm_encryption(self.contents, True)):
            self._rewrite(encrypt=True, new=new)

    def decrypt(self) -> None:
        """Rewrite the bento unencrypted; refused while it holds a module with secrets."""
        if self.busy:
            return
        if self.pending is not None:
            self.pending = None
            self.show("The first capture writes the bento unencrypted.")
            self._read()
            return
        contents = self.contents
        if contents is None or not contents.encrypted:
            return
        refusal = model.decrypt_refusal(self.path, contents, self.modules)
        if refusal:
            self.show(f"Decrypt refused: {refusal}", error=True)
            return
        if self.confirm(self.root, model.confirm_encryption(contents, False)):
            self._rewrite(encrypt=False, new=None)

    def _rewrite(self, *, encrypt: bool, new: str | None) -> None:
        """Write the open bento again as it is, encrypted with `new` or unencrypted."""
        path, contents, modules, stamp = self.path, self.contents, self.modules, self.stamp
        passphrase = self.passphrase

        def work() -> tuple[ops.CapturePlan, ops.CaptureResult] | None:
            if _stamp(path) != stamp:
                return None
            plan = ops.plan_capture(path, contents, [], modules, encrypt=encrypt, decrypt=not encrypt)
            result = ops.apply_capture(
                plan, path, passphrase=lambda: passphrase or "", new_passphrase=lambda: new or ""
            )
            return plan, result

        def done(outcome: tuple[ops.CapturePlan, ops.CaptureResult] | None) -> None:
            if outcome is None:
                self.show(_STALE_BENTO, error=True)
                self._read()
                return
            plan, result = outcome
            self.show_lines(model.capture_report(plan, result, path))
            self.passphrase = new
            self._read()

        self._in_background(work, done, self._action_failed)

    # After an action

    def _stale_machine(self, action: str, changed: Sequence[str]) -> None:
        self.show_lines(
            [f"{action} stopped: these files changed since the tree was shown, so the result"
             " would differ from it. Nothing was written; check the refreshed tree and try again.",
             *(f"  {name}" for name in changed)],
            error=True,
        )
        self._read()

    def _action_failed(self, error: BaseException) -> None:
        self.show(f"Error: {error}", error=True)
        self._read()

    # Buttons

    def choose_open(self) -> None:
        chosen = filedialog.askopenfilename(
            parent=self.root,
            title="Open a bento",
            initialdir=self._dialog_dir(),
            filetypes=_FILETYPES,
        )
        if chosen:
            self.open(Path(chosen))

    def choose_new(self) -> None:
        chosen = filedialog.asksaveasfilename(
            parent=self.root,
            title="Where should the first capture write the bento?",
            initialdir=self._dialog_dir(),
            initialfile="bento.tar.gz",
            defaultextension=".tar.gz",
            filetypes=_FILETYPES,
            confirmoverwrite=False,
        )
        if not chosen:
            return
        path = Path(chosen)
        self.open(path)
        if path.is_file():
            self.show(f"{path.name} already exists; opened it instead of starting a new one.")

    def _dialog_dir(self) -> str:
        directory = self.path.parent
        while not directory.is_dir() and directory != directory.parent:
            directory = directory.parent
        return str(directory)

    def _update_bar(self) -> None:
        """Enable the buttons that can act now; nothing acts while background work runs."""
        busy = self.busy
        contents = self.contents
        can_encrypt = not busy and not self.locked and (contents is not None or self.stamp is None)
        can_decrypt = not busy and (
            self.pending is not None or (contents is not None and contents.encrypted)
        )
        self.encrypt_button.configure(state="normal" if can_encrypt else "disabled")
        self.decrypt_button.configure(state="normal" if can_decrypt else "disabled")
        for button in (self.open_button, self.new_button):
            button.configure(state="disabled" if busy else "normal")
        for tab in (self.capture, self.deploy):
            tab.set_busy(busy)

    # Layout

    def _build(self) -> None:
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        bar = ttk.Frame(root, padding=(12, 10, 12, 6))
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)
        ttk.Label(bar, text="Bento", font=theme.bold_font(bar)).grid(
            row=0, column=0, sticky="w", padx=(0, 10)
        )
        self.path_var = tk.StringVar(root)
        path = ttk.Entry(bar, textvariable=self.path_var, state="readonly", takefocus=False)
        path.grid(row=0, column=1, sticky="ew")
        buttons = ttk.Frame(bar)
        buttons.grid(row=0, column=2, sticky="e", padx=(10, 0))
        self.open_button = ttk.Button(buttons, text="Open…", command=self.choose_open)
        self.open_button.grid(row=0, column=0)
        self.new_button = ttk.Button(buttons, text="New…", command=self.choose_new)
        self.new_button.grid(row=0, column=1, padx=(6, 0))
        ttk.Separator(buttons, orient="vertical").grid(row=0, column=2, sticky="ns", padx=10)
        self.encrypt_button = ttk.Button(buttons, text="Encrypt…", command=self.encrypt)
        self.encrypt_button.grid(row=0, column=3)
        self.decrypt_button = ttk.Button(buttons, text="Decrypt", command=self.decrypt)
        self.decrypt_button.grid(row=0, column=4, padx=(6, 0))

        self.info_var = tk.StringVar(root)
        self.info = ttk.Label(bar, textvariable=self.info_var, style=theme.MUTED_LABEL)
        self.info.grid(row=1, column=1, sticky="w", pady=(4, 0))
        self.unlock_button = ttk.Button(bar, text="Unlock…", command=self.unlock)
        self.unlock_button.grid(row=1, column=2, sticky="e", pady=(4, 0))
        self.unlock_button.grid_remove()

        self.tabs = ttk.Notebook(root)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=12)
        self.capture = FileTree(self.tabs, self.refresh, "Capture", self.run_capture)
        self.capture.offer_drop_unmanaged()
        self.tabs.add(self.capture.frame, text="Capture: this machine → bento")
        self.deploy = FileTree(self.tabs, self.refresh, "Deploy", self.run_deploy)
        self.tabs.add(self.deploy.frame, text="Deploy: bento → this machine")

        panel = ttk.Frame(root, padding=(12, 8, 12, 12))
        panel.grid(row=2, column=0, sticky="ew")
        panel.columnconfigure(0, weight=1)
        self.result = tk.Text(
            panel,
            height=6,
            wrap="word",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            state="disabled",
            background=theme.BG,
            foreground=theme.TEXT,
            font="TkDefaultFont",
        )
        self.result.grid(row=0, column=0, sticky="ew")
        self.result.tag_configure("error", foreground=theme.ERROR)

    def _describe(self, text: str, *, error: bool = False) -> None:
        self.info_var.set(text)
        self.info.configure(style=theme.ERROR_LABEL if error else theme.MUTED_LABEL)

    def show(self, text: str, *, error: bool = False) -> None:
        """Add a line to the result panel."""
        self.result.configure(state="normal")
        if self.result.get("1.0", "end-1c"):
            self.result.insert("end", "\n")
        self.result.insert("end", text, ("error",) if error else ())
        self.result.see("end")
        self.result.configure(state="disabled")

    def show_lines(self, lines: Sequence[str], *, error: bool = False) -> None:
        self.show("\n".join(lines), error=error)

    # Background work

    def _in_background(
        self,
        work: Callable[[], Any],
        done: Callable[[Any], None],
        failed: Callable[[BaseException], None] | None = None,
    ) -> None:
        """Run `work` in a thread, then `done(result)` or `failed(error)` on the Tk thread.

        Results that arrive after the bento was read again or another one was opened are dropped.
        """
        generation = self._generation

        def target() -> None:
            try:
                outcome: tuple[bool, Any] = (True, work())
            except Exception as error:  # reported in the window, not lost in the thread
                outcome = (False, error)
            self._results.put((generation, *outcome, done, failed))

        self._busy += 1
        self.root.configure(cursor="watch")
        self._update_bar()
        threading.Thread(target=target, daemon=True).start()

    def _poll(self) -> None:
        while True:
            try:
                generation, ok, value, done, failed = self._results.get_nowait()
            except queue.Empty:
                break
            self._busy -= 1
            if not self._busy:
                self.root.configure(cursor="")
            self._update_bar()
            if generation != self._generation:
                continue
            if ok:
                done(value)
            elif failed is not None:
                failed(value)
            else:
                self._describe("failed", error=True)
                self.show(f"Error: {value}", error=True)
        self._after = self.root.after(_POLL_MS, self._poll)

    def _destroyed(self, event: tk.Event) -> None:
        if event.widget is self.root:
            self.root.after_cancel(self._after)

    @property
    def busy(self) -> bool:
        return self._busy > 0
