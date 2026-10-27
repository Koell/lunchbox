"""The GUI's model, without Tk: the tree both tabs show, its ticks, and what an action needs.

Node ids say where a node is: `<module>` for a module, `<module>/<path>/` for a folder and
`<module>/<path>` for a file, with paths relative to the module's target. Module names never hold
a `/`, so the ids can't collide, and a file's id is the `<module>/<path>` name ops uses.

A tree belongs to one action. In Deploy a tick means "write this file in this run"; in Capture it
means "this file is in the bento", and the ticks become the stored selection.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from .. import guards, ops, picks, risk, sync
from ..archive import Bento
from ..manifest import Module
from ..picks import ModulePicks, Picks
from ..selection import matches

DEPLOY, CAPTURE = "deploy", "capture"
MODULE, FOLDER, FILE = "module", "folder", "file"

ADDED, UPDATED, UNCHANGED = "added", "updated", "unchanged"
ONLY_IN_BENTO = "only in bento"
REMOVED = "removed"
EXCLUDED = "excluded"
LEFT_OUT = "left out"
NOT_MANAGED = "not managed"
SKIPPED = "skipped"
REFUSED = "refused"

ON, OFF, PARTIAL = "on", "off", "partial"

DIFF_LIMIT = 2000

_QUIET = (UNCHANGED, NOT_MANAGED, EXCLUDED, LEFT_OUT, SKIPPED)
_HUNK = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass(frozen=True)
class FileEntry:
    """One file of a module: its state before any ticks, and its contents here and in the bento
    (None: not there). `findings` are the guard findings a capture would refuse it for,
    `high_risk` why it is a key or credential (None: it isn't)."""

    path: str
    state: str
    machine: bytes | None = None
    bento: bytes | None = None
    findings: tuple[guards.Finding, ...] = ()
    high_risk: str | None = None


@dataclass(frozen=True)
class ModuleEntry:
    """One module with its files.

    A `skipped` module has no target directory here; `refused` says why capture refuses it;
    an `unknown` module is in the bento but no longer in `bento.toml`.
    """

    module: Module
    files: tuple[FileEntry, ...] = ()
    skipped: bool = False
    refused: str | None = None
    unknown: bool = False


@dataclass(eq=False)
class Node:
    id: str
    kind: str
    entry: ModuleEntry
    path: str
    parent: str | None
    file: FileEntry | None = None
    children: list[str] = field(default_factory=list)

    @property
    def module(self) -> Module:
        return self.entry.module

    @property
    def name(self) -> str:
        if self.kind == MODULE:
            return self.module.name
        return self.path.rstrip("/").rsplit("/", 1)[-1]

    @property
    def inactive(self) -> bool:
        """The whole module takes no part in the action."""
        return self.entry.skipped or self.entry.refused is not None or self.entry.unknown


@dataclass(frozen=True)
class Confirmation:
    """What an action asks before it runs: a question and the cases, each with its items."""

    question: str
    sections: tuple[tuple[str, tuple[str, ...]], ...]
    action: str


@dataclass(frozen=True)
class Diff:
    """A rendered diff: `lines` are (text, kind, highlighted) with kind one of header, hunk,
    add, remove, context. `note` replaces or follows the lines; `hidden` waits for Show."""

    lines: tuple[tuple[str, str, bool], ...] = ()
    note: str = ""
    hidden: bool = False


class Tree:
    """Modules in manifest order; below each, folders before files, both sorted by name.

    `ticks` holds the tick of every file that can take part in the action, `defaults` the ticks
    it started with. `allowed` are the files captured despite guard findings, for one run.
    """

    def __init__(
        self,
        entries: Sequence[ModuleEntry] = (),
        action: str = DEPLOY,
        *,
        stored: Picks = Picks(),
        home: Path | None = None,
    ) -> None:
        self.action = action
        self.stored = stored
        self.home = Path.home() if home is None else home
        self.nodes: dict[str, Node] = {}
        self.roots: list[str] = []
        self.ticks: dict[str, bool] = {}
        self.allowed: set[str] = set()
        self.drop_unmanaged = False
        for entry in entries:
            self._add(entry)
        self.defaults = dict(self.ticks)

    def _add(self, entry: ModuleEntry) -> None:
        name = entry.module.name
        self.roots.append(name)
        self.nodes[name] = Node(name, MODULE, entry, "", None)
        for file in entry.files:
            parent = name
            parts = file.path.split("/")
            for depth in range(1, len(parts)):
                folder = "/".join(parts[:depth]) + "/"
                folder_id = f"{name}/{folder}"
                if folder_id not in self.nodes:
                    self.nodes[folder_id] = Node(folder_id, FOLDER, entry, folder, parent)
                    self.nodes[parent].children.append(folder_id)
                parent = folder_id
            file_id = f"{name}/{file.path}"
            self.nodes[file_id] = Node(file_id, FILE, entry, file.path, parent, file)
            self.nodes[parent].children.append(file_id)
            if not entry.skipped and not entry.unknown and entry.refused is None:
                if file.state != NOT_MANAGED:
                    self.ticks[file_id] = not (
                        self.action == CAPTURE and self.stored.excludes(name, file.path)
                    )
        for node in self.nodes.values():
            if node.entry is entry:
                node.children.sort(key=lambda i: (self.nodes[i].kind != FOLDER, i.casefold()))

    # Structure

    def node(self, id: str) -> Node:
        return self.nodes[id]

    def files(self, id: str) -> Iterator[Node]:
        """Every file at or below `id`."""
        node = self.nodes[id]
        if node.kind == FILE:
            yield node
        for child in node.children:
            yield from self.files(child)

    def children(self, id: str, show_unchanged: bool = False) -> list[str]:
        """The children of `id` the tree shows: unchanged files and folders with nothing else
        to show are hidden unless `show_unchanged`."""
        return [c for c in self.nodes[id].children if self.visible(c, show_unchanged)]

    def visible(self, id: str, show_unchanged: bool = False) -> bool:
        node = self.nodes[id]
        if node.kind == MODULE:
            return True
        if node.kind == FILE:
            return show_unchanged or self.state(id) != UNCHANGED
        return any(self.visible(c, show_unchanged) for c in node.children)

    # Ticks

    def check(self, id: str) -> str | None:
        """ON, OFF or PARTIAL; None if nothing at or below `id` can be ticked."""
        ticks = {self.ticks[f.id] for f in self.files(id) if f.id in self.ticks}
        if not ticks:
            return None
        if len(ticks) == 2:
            return PARTIAL
        return ON if ticks == {True} else OFF

    def toggle(self, id: str) -> None:
        """Untick everything at or below a fully ticked `id`, otherwise tick everything."""
        value = self.check(id) != ON
        for file in self.files(id):
            if file.id in self.ticks:
                self.ticks[file.id] = value

    def carry(self, old: Tree) -> None:
        """Keep the ticks the user changed in `old`, for the files that are still here."""
        for id, tick in old.ticks.items():
            if id in self.ticks and tick != old.defaults.get(id):
                self.ticks[id] = tick

    def ticked(self, id: str) -> bool:
        return self.ticks.get(id, False)

    # States

    def state(self, id: str) -> str:
        """A file's state given the ticks: what the action does with it."""
        node = self.nodes[id]
        file = node.file
        assert file is not None
        if node.entry.skipped:
            return SKIPPED
        if node.entry.refused is not None:
            return REFUSED
        if file.state == NOT_MANAGED or node.entry.unknown:
            return REMOVED if self.action == CAPTURE and self.drop_unmanaged else NOT_MANAGED
        if self.action == DEPLOY:
            return file.state if self.ticked(id) or file.state == UNCHANGED else LEFT_OUT
        if not self.ticked(id):
            return EXCLUDED if file.bento is None else REMOVED
        if self.refused(id):
            return REFUSED
        return file.state

    def can_allow(self, id: str) -> bool:
        """Whether capture would refuse the file for its guard findings unless it is allowed."""
        node = self.nodes[id]
        file = node.file
        return bool(
            self.action == CAPTURE
            and file
            and file.findings
            and file.state in (ADDED, UPDATED)
            and not node.inactive
        )

    def refused(self, id: str) -> bool:
        """Whether capture refuses the file for its guard findings: it isn't allowed."""
        return self.can_allow(id) and id not in self.allowed

    def high_risk(self, id: str) -> str | None:
        """Why the file is a key or credential, or None."""
        file = self.nodes[id].file
        return file.high_risk if file else None

    def changes(self, id: str) -> int:
        """How many files at or below `id` the action writes or removes."""
        writes = (ADDED, UPDATED, REMOVED) if self.action == CAPTURE else (ADDED, UPDATED)
        return sum(1 for file in self.files(id) if self.state(file.id) in writes)

    def total_changes(self) -> int:
        return sum(self.changes(id) for id in self.roots)

    def muted(self, id: str) -> bool:
        """Whether the node is shown greyed out: nothing in it takes part in the action."""
        node = self.nodes[id]
        if node.inactive and not (node.entry.unknown and self.drop_unmanaged):
            return True
        return node.kind == FILE and self.state(id) in _QUIET

    def label(self, id: str) -> str:
        node = self.nodes[id]
        if node.kind != MODULE:
            return node.name
        if node.entry.unknown:
            return f"{node.name}  ·  not in bento.toml"
        return f"{node.name}  ·  {short_path(node.module.target, self.home)}"

    def status(self, id: str) -> str:
        """The text of the state column."""
        node = self.nodes[id]
        if node.kind == FILE:
            state = self.state(id)
            if state in (ADDED, UPDATED) and id in self.allowed:
                state = f"{state}, allowed"
            return f"{state}, high risk" if self.high_risk(id) else state
        if node.kind == MODULE:
            if node.entry.skipped:
                return SKIPPED
            if node.entry.refused is not None:
                return REFUSED
            if node.entry.unknown:
                return REMOVED if self.drop_unmanaged else NOT_MANAGED
        count = self.changes(id)
        if count:
            return count_text(count, "change")
        return "no changes" if node.kind == MODULE else ""

    def details(self, id: str) -> list[tuple[str, str]]:
        """What the details pane shows for `id`, as label and value pairs."""
        node = self.nodes[id]
        module = node.module
        if node.kind == FILE:
            file = node.file
            assert file is not None
            state = self.state(id)
            rows = [("File", node.path), ("Module", module.name), ("State", state)]
            why = self._why(node, state)
            if why:
                rows.append(("Why", why))
            if file.high_risk:
                rows.append(("High risk", file.high_risk))
            rows.append(("On this machine", size_text(_size(file.machine))))
            rows.append(("In the bento", size_text(_size(file.bento))))
            for finding in file.findings:
                rows.append((f"Line {finding.line}", f"{finding.kind}: {finding.excerpt}"))
            return rows
        rows = [("Module", module.name)]
        if not node.entry.unknown:
            rows.append(("Target", str(module.target)))
        if node.kind == FOLDER:
            rows.insert(0, ("Folder", node.path))
        if node.inactive:
            rows.append(("State", self._why(node, self.status(node.id)) or ""))
        else:
            rows.append(("Changes", self._breakdown(id)))
        return rows

    def _why(self, node: Node, state: str) -> str | None:
        entry = node.entry
        if entry.skipped:
            return "skipped: the target directory does not exist on this machine"
        if entry.refused is not None:
            return f"refused: {entry.refused}"
        if entry.unknown:
            return "in the bento, but bento.toml no longer has this module"
        if node.kind != FILE:
            return None
        if node.file and node.file.state == NOT_MANAGED:
            return "in the bento, but bento.toml does not manage this path"
        if state == REFUSED:
            return "the guards found something below; capture leaves it out unless allowed"
        return {
            LEFT_OUT: "unticked: this deploy leaves it alone",
            EXCLUDED: "unticked: kept out of the bento",
            REMOVED: "unticked: capture removes it from the bento",
            ONLY_IN_BENTO: "not on this machine; capture keeps it in the bento",
        }.get(state)

    def _breakdown(self, id: str) -> str:
        counts: dict[str, int] = {}
        for file in self.files(id):
            state = self.state(file.id)
            counts[state] = counts.get(state, 0) + 1
        order = (ADDED, UPDATED, REMOVED, REFUSED, UNCHANGED, ONLY_IN_BENTO, EXCLUDED, LEFT_OUT,
                 NOT_MANAGED)
        parts = [f"{counts[state]} {state}" for state in order if counts.get(state)]
        return ", ".join(parts) or "no files"

    # Actions

    def skips(self) -> dict[str, set[str]]:
        """Per module, the paths a deploy leaves out because they are unticked."""
        skips: dict[str, set[str]] = {}
        for id, tick in self.ticks.items():
            if not tick:
                name, path = id.split("/", 1)
                skips.setdefault(name, set()).add(path)
        return skips

    def picks(self) -> Picks:
        """The ticks as the stored selection: unticked folders as folder excludes, files one by
        one. A folder that was excluded and now has single files ticked stays excluded, with
        those files re-included. Excludes of files this tree doesn't show are kept."""
        modules = dict(self.stored.modules)
        for root in self.roots:
            entry = self.nodes[root].entry
            if entry.skipped or entry.unknown or entry.refused is not None:
                continue
            old = self.stored.module(root)
            exclude: list[str] = []
            include: list[str] = []
            self._collect(root, old, exclude, include)
            for path in old.exclude:
                if f"{root}/{path}" not in self.nodes:
                    exclude.append(path)
            for path in old.include:
                if f"{root}/{path}" not in self.nodes and ModulePicks(tuple(exclude)).excludes(path):
                    include.append(path)
            modules[root] = ModulePicks(tuple(sorted(set(exclude))), tuple(sorted(set(include))))
        return Picks({name: p for name, p in modules.items() if p.exclude})

    def _collect(self, id: str, old: ModulePicks, exclude: list[str], include: list[str]) -> None:
        for child in self.nodes[id].children:
            node = self.nodes[child]
            if node.kind == FILE:
                if child in self.ticks and not self.ticks[child]:
                    exclude.append(node.path)
                continue
            check = self.check(child)
            if check == OFF:
                exclude.append(node.path)
            elif check == PARTIAL and node.path in old.exclude:
                exclude.append(node.path)
                include.extend(f.path for f in self.files(child) if self.ticked(f.id))
            elif check == PARTIAL:
                self._collect(child, old, exclude, include)

    def stale(self, fresh: Tree) -> list[str]:
        """The files whose state or machine contents differ in `fresh`, a tree built again
        just now, among those the action takes part with: ticked here, or ticked by default
        and new in `fresh`."""
        changed = []
        for id in sorted(set(self.ticks) | set(fresh.ticks)):
            relevant = self.ticked(id) if id in self.ticks else fresh.ticked(id)
            if not relevant:
                continue
            old, new = self.nodes.get(id), fresh.nodes.get(id)
            if old is None or new is None or old.file != new.file:
                changed.append(id)
        return changed

    def confirm_deploy(self) -> Confirmation | None:
        """Deploy keeps no backup, so overwriting asks first; so does writing a key or credential,
        even as a new file. Adding other files doesn't."""
        files = [f.id for root in self.roots for f in self.files(root)]
        updated = tuple(id for id in files if self.state(id) == UPDATED)
        secrets = tuple(id for id in files if self.state(id) in (ADDED, UPDATED) and self.high_risk(id))
        sections = []
        if updated:
            sections.append(("Overwritten", updated))
        if secrets:
            sections.append(("Secrets written to this machine", secrets))
        if not sections:
            return None
        question = (
            "Deploy overwrites files on this machine and keeps no backup of them."
            if updated
            else "Deploy writes keys or credentials onto this machine."
        )
        return Confirmation(question, tuple(sections), "Deploy")

    # Details

    def diff(self, id: str, *, reveal: bool = False) -> Diff | None:
        """The change the action makes to the file, `+` being what it writes: the bento's
        version in Deploy, this machine's in Capture. None if the file isn't added or updated."""
        node = self.nodes[id]
        file = node.file
        if file is None or file.state not in (ADDED, UPDATED) or node.inactive:
            return None
        if file.high_risk:
            # Key material never shows, not even after Show.
            sizes = f"{size_text(_size(file.machine))} here, {size_text(_size(file.bento))} in the bento"
            change = "differs" if file.state == UPDATED else "new"
            return Diff(note=f"High risk ({file.high_risk}), so no diff: {change}; {sizes}.")
        if node.module.secrets and not reveal:
            return Diff(note="This module holds secrets: the diff stays hidden until Show.",
                        hidden=True)
        if self.action == DEPLOY:
            old, new, names = file.machine, file.bento, ("this machine", "bento")
            highlight: set[int] = set()
        else:
            old, new, names = file.bento, file.machine, ("bento", "this machine")
            highlight = {finding.line for finding in file.findings}
        return render_diff(old, new, names, highlight, file.path)


def render_diff(
    old: bytes | None,
    new: bytes | None,
    names: tuple[str, str],
    highlight: Iterable[int] = (),
    path: str = "",
    limit: int = DIFF_LIMIT,
) -> Diff:
    """A unified diff from `old` to `new`; lines of `new` numbered in `highlight` are marked."""
    try:
        old_text = (old or b"").decode("utf-8")
        new_text = (new or b"").decode("utf-8")
    except UnicodeDecodeError:
        return Diff(
            note=f"binary, differs: {size_text(_size(old))} in {names[0]},"
            f" {size_text(_size(new))} in {names[1]}"
        )
    marked = set(highlight)
    lines = []
    number = 0
    raw = difflib.unified_diff(
        old_text.splitlines(),
        new_text.splitlines(),
        f"{names[0]}/{path}" if old is not None else "nothing",
        f"{names[1]}/{path}",
        lineterm="",
    )
    for index, text in enumerate(raw):
        if index < 2:
            lines.append((text, "header", False))
            continue
        hunk = _HUNK.match(text)
        if hunk:
            number = int(hunk.group(1))
            lines.append((text, "hunk", False))
        elif text.startswith("-"):
            lines.append((text, "remove", False))
        else:
            kind = "add" if text.startswith("+") else "context"
            lines.append((text, kind, number in marked))
            number += 1
    note = ""
    if len(lines) > limit:
        note = f"Cut off after {limit:,} lines; {len(lines) - limit:,} more not shown."
        lines = lines[:limit]
    return Diff(tuple(lines), note)


# Building the trees


def deploy_tree(
    contents: Bento,
    modules: Sequence[Module],
    home: Path | None = None,
    *,
    planned: Sequence[ops.DeployModule] | None = None,
) -> Tree:
    """The Deploy tab's tree: the bento's files and their state on this machine.

    Files the stored selection excludes are not in the bento for deploy, so they don't appear.
    `planned` is `ops.plan_deploy(contents, modules)` if already at hand.
    """
    entries = []
    if planned is None:
        planned = ops.plan_deploy(contents, modules)
    for item in planned:
        name = item.module.name
        files = [
            FileEntry(
                path, state, item.machine.get(path), item.files[path],
                high_risk=item.high_risk.get(path),
            )
            for state, paths in (
                (ADDED, item.plan.added),
                (UPDATED, item.plan.updated),
                (UNCHANGED, item.plan.unchanged),
            )
            for path in paths
        ]
        files += [
            FileEntry(
                path, NOT_MANAGED, None, contents.files.get(f"{name}/{path}"),
                high_risk=item.high_risk.get(path),
            )
            for path in item.ignored
        ]
        entries.append(ModuleEntry(item.module, tuple(files), not item.module.target.is_dir()))
    return Tree(entries, DEPLOY, home=home)


def capture_tree(
    path: Path,
    previous: Bento | None,
    modules: Sequence[Module],
    *,
    encrypt: bool = False,
    home: Path | None = None,
) -> Tree:
    """The Capture tab's tree: every file this machine has under `bento.toml`, including those
    the stored selection keeps out, and every file of the bento, with their state.

    `encrypt` says the capture will write an encrypted bento, which a module with secrets needs.
    """
    stored = picks.stored(previous) if previous is not None else Picks()
    plan = ops.plan_capture(path, previous, modules, selection=Picks(), encrypt=encrypt)
    base = previous or Bento()
    entries = []
    for result in plan.modules:
        module = result.module
        packed = base.module(module.name)
        if result.skipped:
            high_risk = risk.reasons(module, packed)
            files = [
                FileEntry(p, _kept(module, p), None, data, high_risk=high_risk.get(p))
                for p, data in packed.items()
            ]
            entries.append(ModuleEntry(module, tuple(files), skipped=True))
            continue
        refused = None
        if result.refused:
            refused = "this module holds secrets, and a plain bento can't take it; encrypt the bento"
            result = ops.capture_module(module, packed, selection=ModulePicks())
        # The plan leaves out refused files; the tree shows them, so compare everything again.
        source, full = result.source, sync.plan(result.source, packed)
        high_risk = result.high_risk
        files = [
            FileEntry(
                p, state, source[p], packed.get(p), tuple(result.findings.get(p, ())),
                high_risk.get(p),
            )
            for state, paths in ((ADDED, full.added), (UPDATED, full.updated), (UNCHANGED, full.unchanged))
            for p in paths
        ]
        files += [
            FileEntry(p, _kept(module, p), None, packed[p], high_risk=high_risk.get(p))
            for p in result.only_in_bento
        ]
        entries.append(ModuleEntry(module, tuple(files), refused=refused))
    for name in plan.unknown:
        module, packed = Module(name, Path(), (), ()), base.module(name)
        high_risk = risk.reasons(module, packed)
        files = tuple(
            FileEntry(p, NOT_MANAGED, None, data, high_risk=high_risk.get(p))
            for p, data in packed.items()
        )
        entries.append(ModuleEntry(module, files, unknown=True))
    return Tree(entries, CAPTURE, stored=stored, home=home)


def _kept(module: Module, path: str) -> str:
    return ONLY_IN_BENTO if matches(path, module.include, module.exclude) else NOT_MANAGED


def confirm_capture(plan: ops.CapturePlan) -> Confirmation | None:
    """Capture asks first when files leave the bento, a module or file is refused, files are
    allowed despite findings, a key or credential is written, or the encryption changes; adding
    and updating anything else runs directly."""
    sections = []
    leaving = tuple(f"{m.module.name}/{p}" for m in plan.modules for p in m.removed)
    if plan.pruned:
        leaving += tuple(f"module {name}" for name in plan.unknown)
    if leaving:
        sections.append(("Leave the bento", leaving))
    refused = tuple(f"module {m.module.name}" for m in plan.modules if m.refused)
    refused += tuple(f"{m.module.name}/{p}" for m in plan.modules for p in m.refused_files)
    if refused:
        sections.append(("Refused, not captured", refused))
    allowed = tuple(
        f"{m.module.name}/{p}" for m in plan.modules for p in m.plan.changes if p in m.allowed
    )
    if allowed:
        sections.append(("Captured despite guard findings", allowed))
    secrets = tuple(
        f"{m.module.name}/{p}" for m in plan.modules for p in m.plan.changes if p in m.high_risk
    )
    if secrets:
        sections.append(("Secrets written to the bento", secrets))
    previous = plan.previous
    if plan.encrypt:
        change = "changes the passphrase" if previous and previous.encrypted else "encrypts it"
        sections.append(("Encryption", (f"this capture writes the bento encrypted: {change}",)))
    elif previous is not None and previous.encrypted and not plan.bento.encrypted:
        sections.append(("Encryption", ("this capture writes the bento unencrypted",)))
    if not sections:
        return None
    return Confirmation("This capture does more than add and update files.", tuple(sections),
                        "Capture")


def confirm_encryption(contents: Bento | None, encrypt: bool) -> Confirmation:
    """The question Encrypt… and Decrypt ask before rewriting the bento."""
    if not encrypt:
        return Confirmation(
            "Decrypt rewrites the bento unencrypted: anyone with the file can read it.",
            (),
            "Decrypt",
        )
    if contents is not None and contents.encrypted:
        return Confirmation("Change the bento's passphrase? The old one stops working.", (),
                            "Change passphrase")
    return Confirmation(
        "Encrypt rewrites the bento with the new passphrase and removes its plain backups.",
        (),
        "Encrypt",
    )


def decrypt_refusal(path: Path, contents: Bento, known: Sequence[Module]) -> str | None:
    """Why the bento can't be decrypted, or None: a plain bento can't hold secrets."""
    return ops.plan_capture(path, contents, [], known, decrypt=True).blocked


def deploy_report(planned: Sequence[ops.DeployModule]) -> list[str]:
    """What a deploy wrote, module by module, in the CLI's words."""
    lines = []
    for item in planned:
        lines.append(f"deploy {item.module.name} -> {item.module.target}")
        lines += [f"  added    {p}" for p in item.plan.added]
        lines += [f"  updated  {p}" for p in item.plan.updated]
        if not item.plan.changes:
            lines.append("  nothing written")
    return lines


def capture_report(plan: ops.CapturePlan, result: ops.CaptureResult | None, path: Path) -> list[str]:
    """What a capture did, in the CLI's words; `result` is None if the bento was unchanged."""
    lines = []
    for item in plan.modules:
        name = item.module.name
        lines += [f"  added    {name}/{p}" for p in item.plan.added]
        lines += [f"  updated  {name}/{p}" for p in item.plan.updated]
        lines += [f"  removed  {name}/{p}" for p in item.removed]
        if item.refused:
            lines.append(f"  refused  module {name}: holds secrets, a plain bento can't take it")
        for p in item.refused_files:
            lines.append(f"  refused  {name}/{p}  (guard findings)")
        for p in item.allowed:
            lines.append(f"  warning  {name}/{p}  captured despite guard findings")
    if plan.pruned:
        lines += [f"  removed  module {name}" for name in plan.unknown]
    if result is None:
        return ["bento unchanged", *lines]
    head = [f"bento written: {path}" + (" (encrypted)" if plan.bento.encrypted else "")]
    if result.backup:
        head.append(f"  previous kept as {result.backup.name}")
    head += [f"  removed  plain backup {p.name}" for p in result.dropped]
    return head + lines


def with_skips(planned: Sequence[ops.DeployModule], skips: Mapping[str, set[str]]) -> list[ops.DeployModule]:
    """The deploy plans without the files unticked for this run and without modules whose
    target directory doesn't exist here."""
    return [
        replace(item, plan=item.plan.without(skips.get(item.module.name, set())))
        for item in planned
        if item.module.target.is_dir()
    ]


def _size(data: bytes | None) -> int | None:
    return None if data is None else len(data)


def count_text(count: int, noun: str) -> str:
    return f"{count} {noun}{'s' * (count != 1)}"


def size_text(size: int | None) -> str:
    if size is None:
        return "not there"
    if size < 1024:
        return count_text(size, "byte")
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def short_path(path: Path, home: Path) -> str:
    """`path` with the home directory written as `~`."""
    try:
        relative = Path(path).relative_to(home)
    except ValueError:
        return str(path)
    return str(Path("~") / relative) if relative.parts else "~"
