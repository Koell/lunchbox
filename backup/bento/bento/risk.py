"""High-risk files: keys and credentials, which the CLI and the GUI handle with extra care.

A file is high-risk when its content holds a private key or a credential, or when it belongs to a
module with secrets and matches none of the module's `plain` patterns. `plain` only lowers that
default; it never clears what the content shows. Forgetting a pattern can therefore only make a
file more protected.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from . import guards
from .archive import Bento
from .manifest import Module
from .selection import matches

PRIVATE_KEY = "private key"
SECRET_VALUE = "secret value"
UNLISTED = "secrets module, not listed as plain"


def reason(module: Module, path: str, *versions: bytes | None) -> str | None:
    """Why the file at `path` (relative to the module) is high-risk, or None if it isn't.

    `versions` are its contents wherever it exists, here and in the bento; any of them counts.
    """
    present = [data for data in versions if data is not None]
    if any(guards.holds_private_key(data) for data in present):
        return PRIVATE_KEY
    if any(guards.scan_secrets(path, data) for data in present):
        return SECRET_VALUE
    if module.secrets and not matches(path, module.plain):
        return UNLISTED
    return None


def reasons(module: Module, *sources: Mapping[str, bytes]) -> dict[str, str]:
    """The high-risk files among every path of `sources` (each a path-to-contents mapping)."""
    found = {}
    for path in sorted(set().union(*sources)):
        why = reason(module, path, *(source.get(path) for source in sources))
        if why:
            found[path] = why
    return found


def count(contents: Bento, known: Sequence[Module]) -> int:
    """How many of the bento's files are high-risk; a module the manifest lacks is judged by
    content alone."""
    by_name = {module.name: module for module in known}
    total = 0
    for name in contents.modules():
        module = by_name.get(name) or Module(name, Path(), (), ())
        total += len(reasons(module, contents.module(name)))
    return total


def suffix(why: str | None) -> str:
    """The note a report line of a high-risk file ends in."""
    return f"  (high risk: {why})" if why else ""
