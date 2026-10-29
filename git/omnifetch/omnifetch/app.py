"""The window: the project folder bar, a toolbar, the checkbox tree of repositories, and the
details pane.

Slow work (reading repositories, fetching) runs in worker threads; their results come back to the
Tk thread through a queue polled with `after`, since Tk may only be used from the thread that
created it.

Passphrases are asked in the Tk thread right before a fetch, handed to the run's private
ssh-agent and dropped; the agent is stopped when the run ends or the window closes.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, ttk

from . import dialogs, icon, repos, theme
from .repos import FetchResult, Repo
from .state import State

AskPassphrase = Callable[[tk.Misc, Path, int, str | None], str | None]
ChooseFolder = Callable[[tk.Misc, Path | None], Path | None]

TITLE = "Omnifetch: fetch all git repositories"
ROOT = "."  # tree id of the scanned folder; others are paths relative to it
_POLL_MS = 50
_TAGS = {
    "ok": {"foreground": theme.OK},
    "behind": {"foreground": theme.BEHIND},
    "failed": {"foreground": theme.ERROR},
    "muted": {"foreground": theme.MUTED},
}


def choose_folder(parent: tk.Misc, current: Path | None) -> Path | None:
    """Ask for the project folder with the system's folder picker; None if the user cancels."""
    chosen = filedialog.askdirectory(
        parent=parent, title="Folder that holds your projects", mustexist=True,
        initialdir=str(current or Path.home()),
    )
    return Path(chosen) if chosen else None


class App:
    """One window on one project folder at a time.

    The folder is `requested` (from --path) or the one chosen last; on the first start, or when
    that one is gone, the window asks for it. Change… picks another one; the choice is remembered.
    """

    def __init__(
        self,
        root: tk.Tk | tk.Toplevel,
        requested: Path | None = None,
        depth: int | None = None,
        *,
        ask_passphrase: AskPassphrase = dialogs.ask_passphrase,
        choose_folder: ChooseFolder = choose_folder,
        state: State | None = None,
    ) -> None:
        self.root = root
        self.folder: Path | None = None
        self.depth = depth
        self.ask_passphrase = ask_passphrase
        self.choose_folder = choose_folder
        self.state = state or State()
        self.tools: repos.SshTools | None = None
        self.repos: dict[str, Repo] = {}
        self.children: dict[str, list[str]] = {}
        self.status: dict[str, tuple[str, str | None]] = {}  # repo -> (text, tag)
        self.output: dict[str, str] = {}  # repo -> output of its last fetch
        self.unticked: set[str] = set()
        self.reading = False
        self.fetching = False
        self.agent: repos.Agent | None = None
        self._events: queue.Queue[Callable[[], None]] = queue.Queue()

        root.title(TITLE)
        theme.apply(root)
        self._icons = icon.apply(root)
        root.geometry("1100x720")
        root.minsize(760, 480)
        self._checks = theme.checkboxes(root)
        self._build()
        self._after = root.after(_POLL_MS, self._poll)
        root.bind("<Destroy>", self._destroyed, add="+")

        folder = requested or self.state.folder()
        if folder is not None and folder.is_dir():
            self.open_folder(folder)
        else:
            if folder is not None:
                self.path_var.set(f"{folder} is gone")
            self._update_buttons()
            root.after_idle(self.change_folder)

    # Layout

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=(12, 10, 12, 12), style=theme.SURFACE_FRAME)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)

        bar = ttk.Frame(frame, style=theme.SURFACE_FRAME)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        bar.columnconfigure(1, weight=1)
        ttk.Label(bar, text="Projects", style=theme.SURFACE_MUTED_LABEL).grid(row=0, column=0, padx=(0, 8))
        self.path_var = tk.StringVar(frame, value="No project folder chosen yet")
        ttk.Entry(bar, textvariable=self.path_var, state="readonly").grid(row=0, column=1, sticky="ew")
        self.change_button = ttk.Button(bar, text="Change…", command=self.change_folder)
        self.change_button.grid(row=0, column=2, padx=(8, 0))

        toolbar = ttk.Frame(frame, style=theme.SURFACE_FRAME)
        toolbar.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        toolbar.columnconfigure(10, weight=1)
        self.refresh_button = ttk.Button(toolbar, text="Refresh", command=self.refresh)
        self.refresh_button.grid(row=0, column=0)
        self.prune = tk.BooleanVar(frame, value=self.state.prune())
        ttk.Checkbutton(
            toolbar, text="Prune deleted remote branches", variable=self.prune,
            command=lambda: self.state.remember_prune(self.prune.get()), style=theme.SURFACE_CHECKBUTTON,
        ).grid(row=0, column=1, padx=(12, 0))
        self.summary = tk.StringVar(frame, value="Choose the folder that holds your projects.")
        ttk.Label(toolbar, textvariable=self.summary, style=theme.SURFACE_MUTED_LABEL).grid(
            row=0, column=10, sticky="e", padx=(12, 12)
        )
        self.fetch_button = ttk.Button(
            toolbar, text="Fetch", command=self.fetch, style=theme.ACCENT_BUTTON, state="disabled"
        )
        self.fetch_button.grid(row=0, column=11)

        panes = ttk.Panedwindow(frame, orient="horizontal")
        panes.grid(row=2, column=0, sticky="nsew")

        left = ttk.Frame(panes, style=theme.SURFACE_FRAME)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        self.view = ttk.Treeview(left, columns=("branch", "remote", "status"), selectmode="browse")
        for column, text, width in (
            ("#0", "Repository", 260), ("branch", "Branch", 170),
            ("remote", "Remote", 130), ("status", "Status", 120),
        ):
            self.view.heading(column, text=text, anchor="w")
            self.view.column(column, width=width, minwidth=80, stretch=column == "#0")
        for tag, options in _TAGS.items():
            self.view.tag_configure(tag, **options)
        self.view.tag_configure("folder", font=theme.bold_font(self.view))
        self.view.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.view.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.view.configure(yscrollcommand=scroll.set)
        self.view.bind("<<TreeviewSelect>>", lambda e: self._show_details())
        self.view.bind("<Button-1>", self._clicked)
        self.view.bind("<space>", lambda e: self.toggle(self.selected) or "break")
        panes.add(left, weight=3)

        self.details = ttk.Frame(panes, padding=(14, 2, 0, 0), style=theme.SURFACE_FRAME)
        self.details.columnconfigure(0, weight=1)
        self.details.rowconfigure(1, weight=1)
        self.info = ttk.Frame(self.details, style=theme.SURFACE_FRAME)
        self.info.grid(row=0, column=0, sticky="new")
        self.info.columnconfigure(1, weight=1)
        out = ttk.Frame(self.details, style=theme.SURFACE_FRAME)
        out.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        out.columnconfigure(0, weight=1)
        out.rowconfigure(0, weight=1)
        self.text = tk.Text(
            out, wrap="word", relief="flat", borderwidth=0, highlightthickness=0,
            background=theme.FIELD, foreground=theme.TEXT, font=theme.MONO, state="disabled",
            padx=6, pady=4, height=8,
        )
        self.text.grid(row=0, column=0, sticky="nsew")
        ys = ttk.Scrollbar(out, orient="vertical", command=self.text.yview)
        ys.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=ys.set)
        self._out_frame = out
        out.grid_remove()
        panes.add(self.details, weight=2)
        self.info.bind("<Configure>", lambda e: self._wrap())
        # The tree asks for the width of all its columns; start with the details pane readable
        self.root.after(100, lambda: panes.sashpos(0, int(panes.winfo_width() * 0.62)))

    # Project folder

    def change_folder(self) -> None:
        """Ask for another project folder and show its repositories."""
        if self.reading or self.fetching:
            return
        chosen = self.choose_folder(self.root, self.folder)
        if chosen is not None:
            self.open_folder(chosen)

    def open_folder(self, folder: Path) -> None:
        """Show the repositories below `folder` and remember it for the next start."""
        if self.reading or self.fetching:
            return
        self.folder = folder
        self.state.remember_folder(folder)
        self.path_var.set(str(folder))
        self.unticked = self.state.unticked(folder)
        self.repos, self.children = {}, {}
        self._render()
        self.refresh()

    # Scan

    def refresh(self) -> None:
        """Find the repositories again and read their branches and remotes."""
        if self.folder is None or self.reading or self.fetching:
            return
        folder = self.folder
        self.reading = True
        self.status.clear()
        self.output.clear()
        self.summary.set("Looking for repositories…")
        self._update_buttons()

        def work() -> None:
            if self.tools is None:
                self.tools = repos.find_ssh_tools()
            found = repos.scan(folder, self.depth)
            self._call(lambda: self._found(found))
            repos.inspect_all(found, self.tools, lambda repo: self._call(lambda: self._read(repo)))

        self._in_background(work, self._scanned)

    def _found(self, found: list[Repo]) -> None:
        self.repos = {repo.name: repo for repo in found}
        self.children = _folders(list(self.repos))
        for name in self.repos:
            self.status[name] = ("reading…", "muted")
        self._render()

    def _read(self, repo: Repo) -> None:
        self.status[repo.name] = _branch_status(repo, fetched=False)
        self._update()

    def _scanned(self, error: BaseException | None) -> None:
        self.reading = False
        if error is not None:
            self.summary.set(f"Error: {error}")
        self._update()

    # Fetch

    def fetch(self) -> None:
        chosen = [repo for name, repo in self.repos.items() if name not in self.unticked]
        if not chosen or self.reading or self.fetching or self.tools is None:
            return

        needs: dict[Path, list[Repo]] = {}
        for repo in chosen:
            for key in repo.keys:
                needs.setdefault(key, []).append(repo)
        skipped_keys: set[Path] = set()
        if needs:
            try:
                self.agent = repos.Agent(self.tools)
            except (RuntimeError, OSError) as error:
                self.summary.set(f"Error: {error}")
                return
            for key, users in needs.items():
                error = None
                while True:
                    passphrase = self.ask_passphrase(self.root, key, len(users), error)
                    if passphrase is None:
                        skipped_keys.add(key)
                        break
                    error = self.agent.add(key, passphrase)
                    passphrase = None
                    if error is None:
                        break

        run = [repo for repo in chosen if not skipped_keys.intersection(repo.keys)]
        for repo in chosen:
            self.output.pop(repo.name, None)
            if repo in run:
                self.status[repo.name] = ("waiting", "muted")
            else:
                self.status[repo.name] = ("skipped", "muted")
                self.output[repo.name] = "Skipped: no passphrase for its SSH key."
        self.fetching = True
        self._update()

        env = repos.fetch_env(self.tools, self.agent)
        prune = self.prune.get()

        def work() -> None:
            try:
                repos.fetch_all(
                    run, prune, env,
                    started=lambda repo: self._call(lambda: self._started(repo)),
                    done=lambda result: self._call(lambda: self._fetched(result)),
                )
            finally:
                self._stop_agent()

        self._in_background(work, self._finished)

    def _started(self, repo: Repo) -> None:
        self.status[repo.name] = ("fetching…", None)
        self._update()

    def _fetched(self, result: FetchResult) -> None:
        name = result.repo.name
        self.output[name] = result.output or "Nothing new."
        self.status[name] = _branch_status(result.repo, fetched=True) if result.ok else ("failed", "failed")
        self._update()

    def _finished(self, error: BaseException | None) -> None:
        self.fetching = False
        self._update()
        if error is not None:
            self.summary.set(f"Error: {error}")

    def _stop_agent(self) -> None:
        agent, self.agent = self.agent, None
        if agent is not None:
            agent.close()

    # Ticks

    def repos_below(self, id: str) -> list[str]:
        if id in self.repos:
            return [id]
        return [name for child in self.children.get(id, []) for name in self.repos_below(child)]

    def check(self, id: str) -> str:
        below = self.repos_below(id)
        ticked = sum(name not in self.unticked for name in below)
        return "on" if ticked == len(below) else "off" if ticked == 0 else "partial"

    def toggle(self, id: str | None) -> None:
        """Tick or untick `id` and every repository below it."""
        if id is None or self.fetching:
            return
        below = self.repos_below(id)
        if self.check(id) == "on":
            self.unticked.update(below)
        else:
            self.unticked.difference_update(below)
        self.state.remember_unticked(self.folder, self.unticked)
        self._update()

    @property
    def selected(self) -> str | None:
        selection = self.view.selection()
        return selection[0] if selection else None

    def _clicked(self, event: tk.Event) -> str | None:
        id = self.view.identify_row(event.y)
        if id and self.view.identify_element(event.x, event.y) == "image":
            self.view.selection_set(id)
            self.toggle(id)
            return "break"
        return None

    # Rendering

    def _render(self) -> None:
        """Rebuild the tree; folders with a single subfolder and nothing else show as one row."""
        self.view.delete(*self.view.get_children())

        def insert(id: str, parent: str, prefix: str = "") -> None:
            label = prefix + (str(self.folder) if id == ROOT else id.rsplit("/", 1)[-1])
            kids = self.children.get(id)
            if id != ROOT and kids is not None and len(kids) == 1 and kids[0] in self.children:
                insert(kids[0], parent, label + "/")
                return
            self.view.insert(parent, "end", iid=id, text=label, open=True,
                             tags=("folder",) if kids is not None else ())
            for child in kids or []:
                insert(child, id)

        if self.repos:
            insert(ROOT, "")
        self._update()

    def _update(self) -> None:
        """Refresh ticks, values and colours of every row, the summary and the details."""
        def walk(id: str) -> None:
            if id in self.repos:
                repo = self.repos[id]
                text, tag = self.status.get(id, ("", None))
                remote = ", ".join(dict.fromkeys(repos.host_of(url) for url in repo.remotes.values()))
                self.view.item(id, image=self._checks[self.check(id)],
                               values=(repo.branch or "", remote or "none", text),
                               tags=(tag,) if tag else ())
            else:
                self.view.item(id, image=self._checks[self.check(id)], values=("", "", _folder_status(
                    [self.status.get(name, ("", None))[1] for name in self.repos_below(id)]
                )))
            for child in self.view.get_children(id):
                walk(child)

        for top in self.view.get_children():
            walk(top)
        self.summary.set(self._summary_text())
        self._update_buttons()
        self._show_details()

    def _summary_text(self) -> str:
        total = len(self.repos)
        ticked = sum(name not in self.unticked for name in self.repos)
        texts = [text for text, _ in self.status.values()]
        if self.reading:
            return f"Reading {total - texts.count('reading…')} of {total}…" if total else "Looking for repositories…"
        if self.fetching:
            running = texts.count("waiting") + texts.count("fetching…")
            return f"Fetching, {running} to go…"
        if self.folder is None:
            return "Choose the folder that holds your projects."
        if not self.output:
            return f"{total} repositories · {ticked} ticked"
        tags = [self.status[name][1] for name in self.output]  # the last run's repositories only
        failed, skipped = tags.count("failed"), texts.count("skipped")
        counts = [(tags.count("behind"), "behind"), (failed, "failed"), (skipped, "skipped")]
        return " · ".join([f"Fetched {len(self.output) - failed - skipped}"]
                          + [f"{n} {word}" for n, word in counts if n])

    def _update_buttons(self) -> None:
        idle = not self.reading and not self.fetching
        ticked = any(name not in self.unticked for name in self.repos)
        self.fetch_button.configure(state="normal" if idle and ticked else "disabled")
        self.refresh_button.configure(state="normal" if idle and self.folder else "disabled")
        self.change_button.configure(state="normal" if idle else "disabled")

    def _show_details(self) -> None:
        for child in self.info.winfo_children():
            child.destroy()
        id = self.selected
        if id is None or not self.view.exists(id):
            ttk.Label(self.info, text="Select a repository or folder.", style=theme.SURFACE_MUTED_LABEL).grid(
                row=0, column=0, sticky="nw"
            )
            self._show_output(None)
            return

        if id in self.repos:
            repo = self.repos[id]
            rows = [("Path", str(repo.path)), ("Branch", repo.branch or "detached HEAD")]
            rows += [(f"Remote {name}", url) for name, url in repo.remotes.items()] or [("Remote", "none")]
            rows.append(("SSH key", "\n".join(str(k) for k in repo.keys) or "no passphrase needed"))
            heading = id
            output = self.output.get(id)
        else:
            below = self.repos_below(id)
            ticked = sum(name not in self.unticked for name in below)
            rows = [("Repositories", str(len(below))), ("Ticked", str(ticked))]
            heading = self.view.item(id, "text")
            output = None

        ttk.Label(self.info, text=heading, style=theme.SURFACE_HEADING).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        for row, (label, value) in enumerate(rows, start=1):
            ttk.Label(self.info, text=label, style=theme.SURFACE_MUTED_LABEL).grid(
                row=row, column=0, sticky="nw", padx=(0, 12), pady=2
            )
            ttk.Label(self.info, text=value, style=theme.SURFACE_LABEL).grid(
                row=row, column=1, sticky="nw", pady=2
            )
        self.info.update_idletasks()
        self._wrap()
        self._show_output(output)

    def _wrap(self) -> None:
        """Wrap the values to the width the details pane has."""
        values = [w for w in self.info.grid_slaves(column=1) if isinstance(w, ttk.Label)]
        if values:
            left = self.info.grid_bbox(0, 1)[2]
            for label in values:
                label.configure(wraplength=max(self.info.winfo_width() - left - 16, 120))

    def _show_output(self, output: str | None) -> None:
        if output is None:
            self._out_frame.grid_remove()
            return
        self._out_frame.grid()
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("end", output)
        self.text.configure(state="disabled")

    # Threads

    def _call(self, fn: Callable[[], None]) -> None:
        """Run `fn` on the Tk thread; safe from any thread."""
        self._events.put(fn)

    def _in_background(self, work: Callable[[], None], done: Callable[[BaseException | None], None]) -> None:
        """Run `work` in a thread, then `done(error or None)` on the Tk thread.

        `done` waits for the thread to end first: a worker that dropped the last reference to the
        window would free Tk objects in the wrong thread, which aborts the whole process.
        """
        def target() -> None:
            try:
                work()
            except Exception as error:  # reported in the window, not lost in the thread
                outcome: BaseException | None = error
            else:
                outcome = None
            self._call(lambda: finished(outcome))

        def finished(outcome: BaseException | None) -> None:
            thread.join()
            done(outcome)

        thread = threading.Thread(target=target, daemon=True)
        thread.start()

    def _poll(self) -> None:
        while True:
            try:
                fn = self._events.get_nowait()
            except queue.Empty:
                break
            fn()
        self._after = self.root.after(_POLL_MS, self._poll)

    def _destroyed(self, event: tk.Event) -> None:
        if event.widget is self.root:
            # Every pending callback, not just the poll: one firing after the window is gone fails
            for after in self.root.tk.splitlist(self.root.tk.call("after", "info")):
                self.root.after_cancel(after)
            self._stop_agent()


def _folders(names: list[str]) -> dict[str, list[str]]:
    """Children of every folder in the tree, keyed by folder id; repositories are leaves."""
    children: dict[str, list[str]] = {ROOT: []}
    for name in names:
        parts = name.split("/")
        parent = ROOT
        for i in range(1, len(parts)):
            folder = "/".join(parts[:i])
            if folder not in children:
                children[folder] = []
                children[parent].append(folder)
            parent = folder
        children[parent].append(name)
    return children


def _branch_status(repo: Repo, fetched: bool) -> tuple[str, str | None]:
    if repo.behind:
        return f"{repo.behind} behind", "behind"
    if not fetched:
        return "", None
    return ("up to date", "ok") if repo.behind == 0 else ("fetched", "ok")


def _folder_status(tags: list[str | None]) -> str:
    counts = [(tags.count("failed"), "failed"), (tags.count("behind"), "behind")]
    return " · ".join(f"{n} {word}" for n, word in counts if n)
