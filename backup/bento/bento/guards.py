"""Pure content scanners that keep non-portable paths and secrets out of the repo."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

_EXCERPT_WIDTH = 80
_EXCERPT_LEAD = 20
_MASK_KEEP = 6
_MIN_SECRET_VALUE = 16

_SEP = r"[\\/]{1,2}"
_DRIVE_HOME = re.compile(r"([A-Za-z]):(.*)", re.S)

_SECRET_TOKENS = (
    re.compile(r"(?<![A-Za-z0-9_-])sk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?<![A-Za-z0-9_])ghp_[A-Za-z0-9]{36}(?![A-Za-z0-9_])"),
    re.compile(r"(?<![A-Za-z0-9_])github_pat_[A-Za-z0-9_]{40,}"),
    re.compile(r"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}(?![A-Za-z0-9])"),
)
_PEM = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")
_JSON_PAIR = re.compile(r'"(?P<key>[^"\n]+)"\s*:\s*"(?P<value>(?:[^"\\\n]|\\.)*)"')
_TOML_PAIR = re.compile(r"""^\s*(?P<key>[\w.-]+|"[^"\n]+")\s*=\s*(?P<q>["'])(?P<value>.*?)(?P=q)""")
_SECRET_KEY = re.compile(r"api[_-]?key|token|secret|passw(?:or)?d", re.I)
# Values that are references, templates, paths, URLs or placeholders rather than literals.
_NOT_LITERAL = re.compile(
    r"[\s$]|%\w+%|\{\{|^(?:~|\.{0,2}/|[a-z]:[\\/])|^<.*>$|^[a-z][a-z0-9+.-]*://[^@]*$", re.I
)


@dataclass(frozen=True)
class Finding:
    """One guard hit: ``kind`` is "portability" or "secret", ``line`` is 1-based."""

    kind: str
    line: int
    excerpt: str


def scan(
    relpath: str,
    data: bytes,
    home: str | os.PathLike | None = None,
    *,
    windows: bool | None = None,
) -> list[Finding]:
    """Run both scanners and return their findings ordered by line."""
    home = Path.home() if home is None else home
    windows = os.name == "nt" if windows is None else windows
    findings = scan_portability(relpath, data, home, windows=windows) + scan_secrets(relpath, data)
    return sorted(findings, key=lambda f: f.line)


def scan_portability(
    relpath: str, data: bytes, home: str | os.PathLike, *, windows: bool
) -> list[Finding]:
    """Flag each line containing the home directory in any common spelling."""
    pattern = _home_pattern(home, windows)
    if pattern is None:
        return []
    findings = []
    for number, line in _lines(data):
        match = pattern.search(line)
        if match:
            findings.append(Finding("portability", number, _excerpt(line, match.start(), match.end())))
    return findings


def scan_secrets(relpath: str, data: bytes) -> list[Finding]:
    """Flag each line containing credential-like content, with the value masked."""
    findings = []
    for number, line in _lines(data):
        hits = [(m.start(), _mask(m.group())) for p in _SECRET_TOKENS for m in p.finditer(line)]
        hits += [(m.start(), m.group()) for m in _PEM.finditer(line)]
        for pair in (*_JSON_PAIR.finditer(line), *_TOML_PAIR.finditer(line)):
            key, value = pair["key"].strip('"'), pair["value"]
            if _SECRET_KEY.search(key) and _is_literal_secret(value):
                hits.append((pair.start(), f"{key}: {_mask(value)}"))
        if hits:
            findings.append(Finding("secret", number, min(hits)[1]))
    return findings


def _lines(data: bytes) -> list[tuple[int, str]]:
    text = data.decode("utf-8", errors="replace")
    return [(n, line.removesuffix("\r")) for n, line in enumerate(text.split("\n"), start=1)]


def _home_pattern(home: str | os.PathLike, windows: bool) -> re.Pattern[str] | None:
    raw = os.fspath(home)
    drive = _DRIVE_HOME.fullmatch(raw)
    parts = [p for p in re.split(r"[\\/]+", drive.group(2) if drive else raw) if p]
    if not parts:
        return None
    tail = _SEP + _SEP.join(map(re.escape, parts))
    if drive:
        letter = drive.group(1)
        spellings = [
            rf"(?<![A-Za-z0-9]){re.escape(letter)}:{tail}",
            rf"/[{letter.lower()}{letter.upper()}]{tail}",
        ]
    else:
        spellings = [tail]
    flags = re.IGNORECASE if windows else 0
    return re.compile(rf"(?:{'|'.join(spellings)})(?![\w-])", flags)


def _is_literal_secret(value: str) -> bool:
    return len(value) >= _MIN_SECRET_VALUE and not _NOT_LITERAL.search(value)


def _mask(secret: str) -> str:
    return secret[:_MASK_KEEP] + "…"


def _excerpt(line: str, start: int, end: int) -> str:
    lo = max(0, start - _EXCERPT_LEAD)
    hi = min(len(line), max(end, lo + _EXCERPT_WIDTH))
    text = line[lo:hi].strip()
    return ("…" if lo else "") + text + ("…" if hi < len(line) else "")
