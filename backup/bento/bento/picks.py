"""The stored selection: the managed files a bento keeps out, so every capture leaves them out.

It only narrows `bento.toml`, never widens it. The bento stores it as `.bento/selection.json`:

    {"version": 1, "modules": {"claude": {"exclude": ["skills/old/", "custom/x.sh"], "include": []}}}

Paths are relative to the module's target. An exclude ending in `/` is a folder and also covers
files that appear in it later; `include` brings back single files inside an excluded folder. A
module that isn't listed keeps everything.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from .archive import Bento, BentoError
from .manifest import NAME

ENTRY = "selection.json"
VERSION = 1


@dataclass(frozen=True)
class ModulePicks:
    exclude: tuple[str, ...] = ()
    include: tuple[str, ...] = ()

    def excludes(self, path: str) -> bool:
        """Whether the relative `path` is kept out of the bento."""
        if path in self.include:
            return False
        return any(
            path == entry or (entry.endswith("/") and path.startswith(entry))
            for entry in self.exclude
        )


@dataclass(frozen=True)
class Picks:
    modules: Mapping[str, ModulePicks] = field(default_factory=dict)

    def module(self, name: str) -> ModulePicks:
        return self.modules.get(name, ModulePicks())

    def excludes(self, module: str, path: str) -> bool:
        return self.module(module).excludes(path)

    def __bool__(self) -> bool:
        return any(picks.exclude for picks in self.modules.values())


def stored(bento: Bento) -> Picks:
    """The selection stored in `bento`; a bento without one keeps everything."""
    raw = bento.meta.get(ENTRY)
    if raw is None:
        return Picks()
    try:
        return parse(raw)
    except ValueError as error:
        raise BentoError(f"the bento's stored selection is damaged: {error}") from None


def with_picks(meta: Mapping[str, bytes], picks: Picks) -> dict[str, bytes]:
    """`meta` with `picks` stored in it, or without a selection entry if nothing is kept out."""
    updated = {name: data for name, data in meta.items() if name != ENTRY}
    if picks:
        updated[ENTRY] = dump(picks)
    return updated


def dump(picks: Picks) -> bytes:
    modules = {
        name: {"exclude": sorted(p.exclude), "include": sorted(p.include)}
        for name, p in sorted(picks.modules.items())
        if p.exclude
    }
    return json.dumps({"version": VERSION, "modules": modules}, indent=2).encode() + b"\n"


def parse(raw: bytes) -> Picks:
    """Read a stored selection; raises ValueError if it is malformed."""
    try:
        data = json.loads(raw)
    except ValueError:
        raise ValueError("not valid JSON") from None
    if not isinstance(data, dict) or data.get("version") != VERSION:
        raise ValueError(f"expected an object with version {VERSION}")
    modules = data.get("modules")
    if not isinstance(modules, dict):
        raise ValueError("'modules' must be an object")
    parsed = {}
    for name, table in modules.items():
        if not NAME.fullmatch(name) or not isinstance(table, dict):
            raise ValueError(f"invalid module entry: {name!r}")
        exclude = _paths(name, table, "exclude", folders=True)
        include = _paths(name, table, "include", folders=False)
        parsed[name] = ModulePicks(exclude, include)
    return Picks(parsed)


def _paths(module: str, table: dict, key: str, *, folders: bool) -> tuple[str, ...]:
    value = table.get(key, [])
    if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
        raise ValueError(f"{module}: '{key}' must be a list of paths")
    for path in value:
        parts = path.removesuffix("/").split("/") if folders else path.split("/")
        if path.startswith("/") or any(part in ("", ".", "..") for part in parts):
            raise ValueError(f"{module}: invalid path in '{key}': {path!r}")
    return tuple(value)
