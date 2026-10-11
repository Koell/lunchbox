"""Direction-agnostic sync engine: plan which files to copy, then apply the plan."""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class Plan:
    source: Path
    destination: Path
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


def plan(source: Path, destination: Path, paths: Iterable[str]) -> Plan:
    """Classify each relative path as added, updated or unchanged by byte comparison."""
    source, destination = Path(source), Path(destination)
    added, updated, unchanged = [], [], []
    for path in sorted(paths):
        target = destination / path
        if not target.is_file():
            added.append(path)
        elif (source / path).read_bytes() != target.read_bytes():
            updated.append(path)
        else:
            unchanged.append(path)
    return Plan(source, destination, tuple(added), tuple(updated), tuple(unchanged))


def apply(plan: Plan, *, executable_scripts: bool = False) -> None:
    """Copy added and updated files byte for byte; never deletes anything.

    With `executable_scripts`, every `.sh` file in the plan gets the executable bit on POSIX.
    """
    for path in plan.changes:
        target = plan.destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(plan.source / path, target)
    if executable_scripts and os.name == "posix":
        for path in plan.changes + plan.unchanged:
            if path.endswith(".sh"):
                _make_executable(plan.destination / path)


def _make_executable(path: Path) -> None:
    mode = path.stat().st_mode
    executable = mode | (mode & 0o444) >> 2
    if executable != mode:
        path.chmod(stat.S_IMODE(executable))
