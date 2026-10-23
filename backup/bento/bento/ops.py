"""Capture, deploy and status as plans and results, without any output.

The CLI prints them and the GUI shows them; both decide nothing on their own. A plan is computed
from a bento already read into memory, so the same bento can be planned again without asking for
its passphrase or decrypting it twice.
"""

from __future__ import annotations

from collections.abc import Callable, Container, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import archive, guards, picks, sync
from .archive import Bento, BentoError
from .manifest import Module
from .picks import ModulePicks, Picks
from .selection import matches, select

Passphrase = Callable[[], str]


class _Everything:
    """A container holding every path: what `--force` allows."""

    def __contains__(self, item: object) -> bool:
        return True


ALL: Container[str] = _Everything()
"""Allow every guard finding of a capture, as `--force` does."""


@dataclass(frozen=True)
class DeployModule:
    """What deploy would do for one module.

    `files` are the packed files the manifest manages, `machine` this machine's versions of them.
    """

    module: Module
    plan: sync.Plan
    files: Mapping[str, bytes]
    ignored: tuple[str, ...] = ()
    machine: Mapping[str, bytes] = field(default_factory=dict)


@dataclass(frozen=True)
class CaptureModule:
    """What capture would do for one module.

    `plan` holds only what is packed: refused files are already dropped from it. `findings` lists
    every guard finding, `allowed` the paths captured despite theirs. `excluded` are this machine's
    files the stored selection keeps out; `removed` are the bento entries capture drops. A
    `skipped` module (no target directory here) and a `refused` one (secrets in a plain bento)
    leave the bento untouched.
    """

    module: Module
    plan: sync.Plan = sync.Plan()
    source: Mapping[str, bytes] = field(default_factory=dict)
    findings: Mapping[str, tuple[guards.Finding, ...]] = field(default_factory=dict)
    allowed: frozenset[str] = frozenset()
    removed: tuple[str, ...] = ()
    only_in_bento: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ()
    skipped: bool = False
    refused: bool = False

    @property
    def refused_files(self) -> tuple[str, ...]:
        return tuple(path for path in self.findings if path not in self.allowed)


@dataclass(frozen=True)
class CapturePlan:
    """A whole capture: the bento it starts from, every module, and the bento it would write.

    `unknown` are bento modules the manifest doesn't have; `pruned` says they are dropped.
    `encrypt` asks for a new passphrase. `blocked` explains why the bento can't be written at all.
    """

    previous: Bento | None
    modules: tuple[CaptureModule, ...]
    bento: Bento
    unknown: tuple[str, ...] = ()
    pruned: bool = False
    encrypt: bool = False
    blocked: str | None = None

    @property
    def changed(self) -> bool:
        # A bento with a damaged label is rewritten even without file changes, which repairs it.
        previous = self.previous
        return (
            previous is None
            or self.bento.files != previous.files
            or self.bento.meta != previous.meta
            or bool(previous.warnings)
            or self.bento.encrypted != previous.encrypted
            or self.encrypt
        )

    @property
    def refused(self) -> bool:
        return any(m.refused or m.refused_files for m in self.modules)


@dataclass(frozen=True)
class CaptureResult:
    """What a written capture left behind: the backup of the old bento, and dropped plain ones."""

    backup: Path | None
    dropped: tuple[Path, ...] = ()


def load(path: Path, passphrase: Passphrase | None = None) -> Bento | None:
    """The bento at `path`, or None if there is none. `passphrase` is asked only if encrypted."""
    return archive.read(path, passphrase) if Path(path).is_file() else None


def unknown_modules(contents: Bento, known: Sequence[Module]) -> list[str]:
    """The modules in the bento that the manifest doesn't have."""
    names = {module.name for module in known}
    return [name for name in contents.modules() if name not in names]


def plan_deploy(contents: Bento, modules: Sequence[Module]) -> list[DeployModule]:
    """Per module, the packed files the manifest manages and how they compare to this machine."""
    stored = picks.stored(contents)
    planned = []
    for module in modules:
        managed, ignored = _split(module, contents, stored.module(module.name))
        machine = sync.read(module.target, managed)
        plan = sync.plan(managed, machine)
        planned.append(DeployModule(module, plan, managed, tuple(ignored), machine))
    return planned


def apply_deploy(planned: Sequence[DeployModule]) -> None:
    """Write every module's added and updated files onto this machine; never deletes."""
    for item in planned:
        item.module.target.mkdir(parents=True, exist_ok=True)
        sync.apply(
            item.plan,
            item.files,
            item.module.target,
            executable_scripts=True,
            private=item.module.secrets,
        )


def capture_module(
    module: Module,
    packed: Mapping[str, bytes],
    *,
    selection: ModulePicks = ModulePicks(),
    allowed: Container[str] = frozenset(),
    prune: bool = False,
    drop_unmanaged: bool = False,
) -> CaptureModule:
    """What capturing `module` into a bento holding `packed` (the module's files) would do.

    Files `selection` excludes are neither captured nor kept: capture drops them from the bento.
    `allowed` holds `<module>/<path>` names captured despite guard findings. A module with secrets
    is exempt from the secret guard, never from the portability guard. `prune` drops every packed
    file that isn't on this machine, `drop_unmanaged` only those `bento.toml` doesn't manage.
    """
    if not module.target.is_dir():
        return CaptureModule(module, skipped=True)
    here = select(module.target, module.include, module.exclude)
    excluded = tuple(path for path in here if selection.excludes(path))
    source = sync.read(module.target, [path for path in here if not selection.excludes(path)])
    plan = sync.plan(source, packed)
    findings = {}
    for relative in plan.changes:
        found = tuple(
            finding
            for finding in guards.scan(relative, source[relative])
            if not (module.secrets and finding.kind == "secret")
        )
        if found:
            findings[relative] = found
    passed = frozenset(path for path in findings if f"{module.name}/{path}" in allowed)
    dropped = {path for path in packed if selection.excludes(path)}
    only_in_bento = set(packed) - set(source) - dropped
    if prune:
        dropped |= only_in_bento
    elif drop_unmanaged:
        dropped |= {p for p in only_in_bento if not matches(p, module.include, module.exclude)}
    return CaptureModule(
        module,
        plan.without(set(findings) - passed),
        source,
        findings,
        passed,
        removed=tuple(sorted(dropped)),
        only_in_bento=tuple(sorted(only_in_bento - dropped)),
        excluded=excluded,
    )


def plan_capture(
    path: Path,
    previous: Bento | None,
    modules: Sequence[Module],
    known: Sequence[Module] | None = None,
    *,
    selection: Picks | None = None,
    allowed: Container[str] = frozenset(),
    prune: bool = False,
    drop_unmanaged: bool = False,
    encrypt: bool = False,
    decrypt: bool = False,
) -> CapturePlan:
    """Merge this machine's files of `modules` into `previous` (None: there is no bento yet).

    `known` is every module of the manifest (default: `modules`). `selection` replaces the bento's
    stored selection (default: keep it); what it excludes is dropped from the bento. `allowed`
    holds `<module>/<path>` names captured despite guard findings. With `prune`, bento entries of
    `modules` that are no longer on this machine are dropped, and when every known module is
    captured, so are modules the manifest no longer has. `drop_unmanaged` drops only what
    `bento.toml` no longer manages: entries outside a module's patterns and, again only when every
    known module is captured, modules the manifest no longer has. An encrypted bento stays encrypted
    unless `decrypt`; `encrypt` encrypts a plain one or changes the passphrase of an encrypted one.
    """
    if encrypt and decrypt:
        raise ValueError("encrypt and decrypt exclude each other")
    known = modules if known is None else known
    base = previous or Bento()
    selection = picks.stored(base) if selection is None else selection
    encrypted = encrypt or (base.encrypted and not decrypt)
    files = dict(base.files)
    everything = {m.name for m in modules} >= {m.name for m in known}
    unknown = unknown_modules(base, known)
    pruned = (prune or drop_unmanaged) and everything
    if pruned:
        files = {name: data for name, data in files.items() if name.split("/", 1)[0] not in unknown}

    results = []
    for module in modules:
        if module.target.is_dir() and module.secrets and not encrypted:
            results.append(CaptureModule(module, refused=True))
            continue
        result = capture_module(
            module,
            base.module(module.name),
            selection=selection.module(module.name),
            allowed=allowed,
            prune=prune,
            drop_unmanaged=drop_unmanaged,
        )
        results.append(result)
        for relative in result.plan.changes:
            files[f"{module.name}/{relative}"] = result.source[relative]
        for relative in result.removed:
            del files[f"{module.name}/{relative}"]

    blocked = None
    if not encrypted:
        secret = sorted({m.name for m in known if m.secrets} & {n.split("/", 1)[0] for n in files})
        if secret:
            blocked = (
                f"{path}: a plain bento can't hold module(s) with secrets: {', '.join(secret)};"
                " keep the bento encrypted"
            )
    meta = picks.with_picks(base.meta, selection)
    return CapturePlan(
        previous,
        tuple(results),
        Bento(files, encrypted=encrypted, meta=meta),
        tuple(unknown),
        pruned,
        encrypt,
        blocked,
    )


def apply_capture(
    plan: CapturePlan,
    path: Path,
    *,
    passphrase: Passphrase | None = None,
    new_passphrase: Passphrase | None = None,
    now: datetime | None = None,
) -> CaptureResult:
    """Write the bento `plan` describes, keeping the old one as a backup.

    `passphrase` gives the existing passphrase when an encrypted bento stays encrypted;
    `new_passphrase` is asked when the plan encrypts or changes the passphrase. After encrypting,
    plain backups are deleted: they would keep the unencrypted contents next to the bento.
    """
    if plan.blocked:
        raise BentoError(plan.blocked)
    key = None
    if plan.encrypt:
        if new_passphrase is None:
            raise ValueError("encrypting needs a new passphrase")
        key = new_passphrase()
    elif plan.bento.encrypted:
        if passphrase is None:
            raise ValueError("an encrypted bento needs its passphrase")
        key = passphrase()
    captured = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    bento = Bento(plan.bento.files, captured, plan.bento.encrypted, meta=plan.bento.meta)
    backup = archive.write(path, bento, plan.previous, passphrase=key)
    dropped = tuple(archive.drop_plain_backups(path)) if plan.encrypt else ()
    return CaptureResult(backup, dropped)


def _split(
    module: Module, contents: Bento, selection: ModulePicks
) -> tuple[dict[str, bytes], list[str]]:
    """The module's packed files that the manifest manages, and the paths of those it doesn't.

    Files the selection excludes are left out of both: they never reach this machine.
    """
    managed, ignored = {}, []
    for relative, data in contents.module(module.name).items():
        if selection.excludes(relative):
            continue
        if matches(relative, module.include, module.exclude):
            managed[relative] = data
        else:
            ignored.append(relative)
    return managed, sorted(ignored)
