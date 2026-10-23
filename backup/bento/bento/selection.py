"""Selection of managed files: include/exclude glob patterns over relative POSIX paths."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

_WILDCARDS = re.compile(r"[*?\[]")


def select(root: Path, includes: Iterable[str], excludes: Iterable[str] = ()) -> list[str]:
    """Return the sorted relative paths of files under `root` matching an include and no exclude.

    Patterns use `/` as separator; `*` and `?` stay within one path segment, `**` spans
    directories. A missing `root` yields an empty list.
    """
    root = Path(root)
    includes, excludes = tuple(includes), tuple(excludes)
    if not root.is_dir():
        return []
    selected = []
    for directory, dirnames, filenames in os.walk(root):
        relative_dir = Path(directory).relative_to(root).as_posix()
        prefix = "" if relative_dir == "." else relative_dir + "/"
        dirnames[:] = sorted(
            name
            for name in dirnames
            if _may_contain(prefix + name, includes) and not _excludes_all(prefix + name, excludes)
        )
        selected.extend(
            prefix + name for name in filenames if matches(prefix + name, includes, excludes)
        )
    return sorted(selected)


def matches(path: str, includes: Iterable[str], excludes: Iterable[str] = ()) -> bool:
    """Whether the relative POSIX `path` matches at least one include and no exclude."""
    return any(_regex(p).fullmatch(path) for p in includes) and not any(
        _regex(p).fullmatch(path) for p in excludes
    )


@lru_cache(maxsize=None)
def _regex(pattern: str) -> re.Pattern[str]:
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append(r"(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(r".*")
            i += 2
        elif pattern[i] == "*":
            out.append(r"[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append(r"[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def _literal_segments(pattern: str) -> list[str]:
    segments = []
    for segment in pattern.split("/"):
        if _WILDCARDS.search(segment):
            break
        segments.append(segment)
    return segments


def _may_contain(directory: str, includes: tuple[str, ...]) -> bool:
    """Whether some include pattern could match a file below `directory`."""
    parts = directory.split("/")
    for pattern in includes:
        segments = pattern.split("/")
        literal = _literal_segments(pattern)
        if len(literal) == len(segments):
            if len(parts) < len(segments) and parts == segments[: len(parts)]:
                return True
        elif parts[: len(literal)] == literal[: len(parts)]:
            return True
    return False


def _excludes_all(directory: str, excludes: tuple[str, ...]) -> bool:
    """Whether an exclude pattern of the form `<dir>/**` covers everything below `directory`."""
    return any(
        pattern.endswith("/**") and _regex(pattern[:-3]).fullmatch(directory)
        for pattern in excludes
    )
