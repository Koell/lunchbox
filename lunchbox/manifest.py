"""Module manifests: a folder holding `manifest.toml` and a `files` folder mirroring the target."""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

MANIFEST_NAME = "manifest.toml"
FILES_NAME = "files"
MODULES_ROOT = Path(__file__).resolve().parent.parent / "modules"

_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-([^}]*)\}")


class ManifestError(Exception):
    """A module folder or its manifest is missing or malformed."""


@dataclass(frozen=True)
class Module:
    name: str
    target: Path
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    files: Path


def resolve_target(raw: str, env: Mapping[str, str], home: Path) -> Path:
    """Apply `${VAR:-default}` substitution, then expand a leading `~` to `home`."""
    value = _VARIABLE.sub(lambda m: env.get(m.group(1)) or m.group(2), raw)
    if value == "~" or value.startswith(("~/", "~\\")):
        return home / value[2:]
    return Path(value)


def load_module(
    folder: Path,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Module:
    """Load the module in `folder`; `env` and `home` default to the real environment."""
    folder = Path(folder)
    name = folder.name
    manifest = folder / MANIFEST_NAME
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ManifestError(f"module '{name}': missing {MANIFEST_NAME}") from None
    except tomllib.TOMLDecodeError as error:
        raise ManifestError(f"module '{name}': invalid {MANIFEST_NAME}: {error}") from None

    target = data.get("target")
    if not isinstance(target, str) or not target.strip():
        raise ManifestError(f"module '{name}': 'target' must be a non-empty string")
    include = _patterns(name, data, "include", required=True)
    exclude = _patterns(name, data, "exclude", required=False)
    unknown = sorted(set(data) - {"target", "include", "exclude"})
    if unknown:
        raise ManifestError(f"module '{name}': unknown field(s): {', '.join(unknown)}")

    return Module(
        name=name,
        target=resolve_target(
            target,
            os.environ if env is None else env,
            Path.home() if home is None else Path(home),
        ),
        include=include,
        exclude=exclude,
        files=folder / FILES_NAME,
    )


def discover(
    root: Path = MODULES_ROOT,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> list[Module]:
    """Load every module folder under `root`, sorted by name."""
    root = Path(root)
    if not root.is_dir():
        raise ManifestError(f"modules folder not found: {root}")
    return [
        load_module(folder, env, home)
        for folder in sorted(root.iterdir())
        if folder.is_dir() and not folder.name.startswith((".", "_"))
    ]


def _patterns(name: str, data: dict, key: str, *, required: bool) -> tuple[str, ...]:
    value = data.get(key)
    if value is None and not required:
        return ()
    if (
        not isinstance(value, list)
        or (required and not value)
        or not all(isinstance(item, str) and item for item in value)
    ):
        kind = "a non-empty list" if required else "a list"
        raise ManifestError(f"module '{name}': '{key}' must be {kind} of non-empty strings")
    return tuple(value)
