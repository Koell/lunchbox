"""The bento: a .tar.gz with a plain `label.json` and the contents, every module's files as
`<module>/<path>` in `contents.tar`, or in `contents.tar.gpg` when the bento is encrypted.

Next to the modules, the contents hold the bento's own data under `.bento/`, such as the stored
selection; a leading dot is never part of a module name, so the two can't collide."""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import gpg
from .manifest import NAME

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "export" / "bento.tar.gz"
VERSION = 1
LABEL_NAME = "label.json"
PLAIN_CONTENTS = "contents.tar"
ENCRYPTED_CONTENTS = "contents.tar.gpg"
ENCRYPTION = "gpg"
META_DIR = ".bento"
KEEP_BACKUPS = 5

_ISO = "%Y-%m-%dT%H:%M:%SZ"
_STAMP = "%Y%m%dT%H%M%SZ"
_UNSAFE_CHARS = re.compile(r"[\\:\x00]")
_META_NAME = re.compile(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*")


class BentoError(Exception):
    """The bento is missing, unreadable, unsafe, of a newer version, or can't be decrypted."""


@dataclass(frozen=True)
class Bento:
    """`files` maps `<module>/<path>` to contents; `captured` is when the bento was written.

    `warnings` lists problems with the bento's `label.json`; they never affect `files`. `meta`
    maps the names of the bento's own entries under `.bento/` to their contents.
    """

    files: Mapping[str, bytes] = field(default_factory=dict)
    captured: datetime | None = None
    encrypted: bool = False
    warnings: tuple[str, ...] = ()
    meta: Mapping[str, bytes] = field(default_factory=dict)

    def modules(self) -> list[str]:
        return sorted({name.split("/", 1)[0] for name in self.files})

    def module(self, name: str) -> dict[str, bytes]:
        """The files of module `name`, keyed by their path relative to the module."""
        prefix = name + "/"
        return {k.removeprefix(prefix): v for k, v in self.files.items() if k.startswith(prefix)}


def read(path: Path, passphrase: Callable[[], str] | None = None) -> Bento:
    """Read and validate the bento at `path`. Nothing is extracted to disk.

    `passphrase` is called only if the bento is encrypted. Unsafe entries refuse the whole
    bento; problems with `label.json` only become warnings.
    """
    path = Path(path)
    label, name, contents = _outer(path)
    encrypted = name == ENCRYPTED_CONTENTS
    captured, warnings = _label(path, label, encrypted)
    if encrypted:
        if passphrase is None:
            raise BentoError(f"{path}: the bento is encrypted")
        try:
            contents = gpg.decrypt(contents, passphrase())
        except gpg.GpgError as error:
            raise BentoError(f"{path}: {error}") from None
    files, meta = _files(path, contents)
    return Bento(files, captured, encrypted, warnings, meta)


def is_encrypted(path: Path) -> bool:
    """Whether the bento at `path` is encrypted; reads only the plain outer layer."""
    return _outer(Path(path))[1] == ENCRYPTED_CONTENTS


def write(
    path: Path, bento: Bento, previous: Bento | None = None, *, passphrase: str | None = None
) -> Path | None:
    """Atomically replace the bento at `path` with `bento`, keeping the old one as a backup.

    An encrypted bento needs `passphrase`, and is decrypted once in memory before it replaces
    anything, so a bento that can't be opened again is never written. The backup sits next to
    `path`, named after the old bento's capture time; only the newest `KEEP_BACKUPS` are kept.
    Returns the backup's path, or None if there was no bento.
    """
    path = Path(path)
    if bento.captured is None:
        raise ValueError("a bento needs a capture time")
    if bento.encrypted != (passphrase is not None):
        raise ValueError("an encrypted bento needs a passphrase, a plain one none")
    mtime = int(bento.captured.timestamp())
    meta = {f"{META_DIR}/{name}": data for name, data in bento.meta.items()}
    contents = _tar({**bento.files, **meta}, mtime)
    label = {"version": VERSION, "captured": bento.captured.strftime(_ISO)}
    name = PLAIN_CONTENTS
    if passphrase is not None:
        try:
            sealed = gpg.encrypt(contents, passphrase)
            if gpg.decrypt(sealed, passphrase) != contents:
                raise BentoError(f"{path}: the encrypted bento did not decrypt to its contents")
        except gpg.GpgError as error:
            raise BentoError(f"{path}: {error}") from None
        contents, name = sealed, ENCRYPTED_CONTENTS
        label["encryption"] = ENCRYPTION

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as tar:
            _add(tar, LABEL_NAME, json.dumps(label, indent=2).encode() + b"\n", mtime)
            _add(tar, name, contents, mtime)
        backup = None
        if path.exists():
            replaced = previous.captured if previous and previous.captured else None
            backup = _backup_path(path, replaced or _file_time(path))
            shutil.copyfile(path, backup)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    _drop_old_backups(path)
    return backup


def backups(path: Path) -> list[Path]:
    """The backups of the bento at `path`, newest first."""
    path = Path(path)
    stem, suffix = _split(path.name)
    pattern = re.compile(re.escape(stem) + r"\.\d{8}T\d{6}Z" + re.escape(suffix))
    if not path.parent.is_dir():
        return []
    found = [p for p in path.parent.iterdir() if pattern.fullmatch(p.name) and p.is_file()]
    return sorted(found, key=lambda p: p.name, reverse=True)


def drop_plain_backups(path: Path) -> list[Path]:
    """Delete the backups of `path` that are not encrypted, and return them.

    A backup whose outer layer can't be read may hold plain files too, so it goes as well.
    """
    dropped = []
    for backup in backups(path):
        try:
            encrypted = is_encrypted(backup)
        except BentoError:
            encrypted = False
        if not encrypted:
            backup.unlink()
            dropped.append(backup)
    return dropped


def _outer(path: Path) -> tuple[bytes | None, str, bytes]:
    """The label (if any), the name of the contents entry and the contents' raw bytes."""
    if not path.is_file():
        raise BentoError(f"no bento at {path}; pass --bento or run capture first")
    entries: dict[str, bytes] = {}
    try:
        with tarfile.open(path, "r:gz") as tar:
            for member in tar:
                name = member.name
                if not member.isreg():
                    raise BentoError(f"{path}: refusing entry that is not a regular file: {name}")
                if name not in (LABEL_NAME, PLAIN_CONTENTS, ENCRYPTED_CONTENTS):
                    raise BentoError(f"{path}: unexpected entry: {name}")
                if name in entries:
                    raise BentoError(f"{path}: duplicate entry: {name}")
                entries[name] = tar.extractfile(member).read()
    except (tarfile.TarError, OSError, EOFError, zlib.error) as error:
        raise BentoError(f"{path}: not a readable .tar.gz: {error}") from None
    names = [n for n in (PLAIN_CONTENTS, ENCRYPTED_CONTENTS) if n in entries]
    if len(names) != 1:
        raise BentoError(
            f"{path}: needs exactly one of {PLAIN_CONTENTS} and {ENCRYPTED_CONTENTS}"
        )
    return entries.get(LABEL_NAME), names[0], entries[names[0]]


def _files(path: Path, contents: bytes) -> tuple[dict[str, bytes], dict[str, bytes]]:
    """The module files, and the `.bento/` entries keyed by their name inside `.bento/`."""
    files: dict[str, bytes] = {}
    meta: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(contents), mode="r:") as tar:
            for member in tar:
                name = member.name
                if not member.isreg():
                    raise BentoError(f"{path}: refusing entry that is not a regular file: {name}")
                if _is_meta(name):
                    into, key = meta, name.removeprefix(META_DIR + "/")
                elif _is_safe(name):
                    into, key = files, name
                else:
                    raise BentoError(f"{path}: refusing entry with an unsafe path: {name}")
                if key in into:
                    raise BentoError(f"{path}: duplicate entry: {name}")
                into[key] = tar.extractfile(member).read()
    except (tarfile.TarError, EOFError) as error:
        raise BentoError(f"{path}: the contents are not a readable tar: {error}") from None
    return files, meta


def _is_meta(name: str) -> bool:
    directory, slash, rest = name.partition("/")
    return directory == META_DIR and slash == "/" and _META_NAME.fullmatch(rest) is not None


def _is_safe(name: str) -> bool:
    parts = name.split("/")
    return (
        len(parts) >= 2
        and NAME.fullmatch(parts[0]) is not None
        and all(part not in ("", ".", "..") for part in parts)
        and not _UNSAFE_CHARS.search(name)
    )


def _label(path: Path, raw: bytes | None, encrypted: bool) -> tuple[datetime | None, tuple[str, ...]]:
    """The capture time from `label.json`, and warnings about anything wrong with it.

    The label only describes the bento, so a missing or damaged label is a warning, never a
    reason to refuse the files. Only a newer version is refused: its layout may differ, and
    misreading it before capture writes a merged bento would lose data.
    """
    if raw is None:
        return None, (f"no {LABEL_NAME}; capture time unknown",)
    try:
        data = json.loads(raw)
    except ValueError:
        return None, (f"{LABEL_NAME} is not valid JSON; ignored",)
    if not isinstance(data, dict):
        return None, (f"{LABEL_NAME} is not a JSON object; ignored",)

    warnings = []
    version = data.get("version")
    if type(version) is int and version > VERSION:
        raise BentoError(
            f"{path}: bento version {version} is newer than this bento tool supports ({VERSION});"
            " update the repository"
        )
    if type(version) is not int or version != VERSION:
        found = "no version" if version is None else f"unknown version {version!r}"
        warnings.append(f"{LABEL_NAME} has {found}; read as version {VERSION}")
    try:
        captured = datetime.strptime(data.get("captured"), _ISO).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        captured = None
        warnings.append(f"{LABEL_NAME} has no valid capture time")
    encryption = data.get("encryption")
    if encryption != (ENCRYPTION if encrypted else None):
        actual = "encrypted" if encrypted else "plain"
        warnings.append(f"{LABEL_NAME} says encryption {encryption!r}, but the contents are {actual}")
    return captured, tuple(warnings)


def _tar(files: Mapping[str, bytes], mtime: int) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name in sorted(files):
            _add(tar, name, files[name], mtime)
    return buffer.getvalue()


def _add(tar: tarfile.TarFile, name: str, data: bytes, mtime: int) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = mtime
    info.mode = 0o755 if name.endswith(".sh") else 0o644
    tar.addfile(info, io.BytesIO(data))


def _split(name: str) -> tuple[str, str]:
    if name.endswith(".tar.gz"):
        return name.removesuffix(".tar.gz"), ".tar.gz"
    stem, dot, extension = name.rpartition(".")
    return (stem, dot + extension) if stem else (name, "")


def _backup_path(path: Path, when: datetime) -> Path:
    stem, suffix = _split(path.name)
    return path.with_name(f"{stem}.{when.astimezone(timezone.utc).strftime(_STAMP)}{suffix}")


def _file_time(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)


def _drop_old_backups(path: Path) -> None:
    for old in backups(path)[KEEP_BACKUPS:]:
        old.unlink()
