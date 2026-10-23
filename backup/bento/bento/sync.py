"""Direction-agnostic sync engine: plan which files to copy, then apply the plan."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class Plan:
    added: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()

    @property
    def changes(self) -> tuple[str, ...]:
        return self.added + self.updated

    def without(self, paths: Iterable[str]) -> Plan:
        """The same plan with `paths` dropped from the files to add or update."""
        skip = set(paths)
        return replace(
            self,
            added=tuple(p for p in self.added if p not in skip),
            updated=tuple(p for p in self.updated if p not in skip),
        )


def plan(source: Mapping[str, bytes], destination: Mapping[str, bytes]) -> Plan:
    """Classify each source path as added, updated or unchanged by byte comparison."""
    added, updated, unchanged = [], [], []
    for path in sorted(source):
        if path not in destination:
            added.append(path)
        elif source[path] != destination[path]:
            updated.append(path)
        else:
            unchanged.append(path)
    return Plan(tuple(added), tuple(updated), tuple(unchanged))


def read(root: Path, paths: Iterable[str]) -> dict[str, bytes]:
    """The contents of those relative `paths` that are files under `root`."""
    root = Path(root)
    return {path: (root / path).read_bytes() for path in paths if (root / path).is_file()}


def apply(
    plan: Plan,
    source: Mapping[str, bytes],
    root: Path,
    *,
    executable_scripts: bool = False,
    private: bool = False,
) -> None:
    """Write added and updated files from `source` under `root` byte for byte; never deletes.

    On POSIX, with `executable_scripts` every `.sh` file in the plan gets the executable bit;
    with `private` every file in the plan becomes 0600 and `root` and the directories between
    it and those files 0700, the way ssh and gpg demand. New private files are created with
    that mode, so they are never readable by others, not even for a moment.
    """
    root = Path(root)
    private = private and os.name == "posix"
    for path in plan.changes:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if private:
            _write_private(target, source[path])
        else:
            target.write_bytes(source[path])
    if os.name != "posix":
        return
    for path in plan.changes + plan.unchanged:
        executable = executable_scripts and path.endswith(".sh")
        if private:
            (root / path).chmod(0o700 if executable else 0o600)
            for directory in (root, *[root / p for p in _parents(path)]):
                directory.chmod(0o700)
        elif executable:
            _make_executable(root / path)


def _parents(path: str) -> list[str]:
    parts = path.split("/")[:-1]
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


def _write_private(target: Path, data: bytes) -> None:
    if target.exists():
        target.chmod(0o600)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as file:
        file.write(data)


def _make_executable(path: Path) -> None:
    mode = path.stat().st_mode
    executable = mode | (mode & 0o444) >> 2
    if executable != mode:
        path.chmod(stat.S_IMODE(executable))
