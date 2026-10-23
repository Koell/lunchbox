"""The manifest `bento.toml`: one `[modules.<name>]` table per module, in file order."""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "bento.toml"

_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-([^}]*)\}")
NAME = re.compile(r"[A-Za-z0-9_-]+")


class ManifestError(Exception):
    """The manifest is missing or malformed."""


@dataclass(frozen=True)
class Module:
    name: str
    target: Path
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    secrets: bool = False


def resolve_target(raw: str, env: Mapping[str, str], home: Path) -> Path:
    """Apply `${VAR:-default}` substitution, then expand a leading `~` to `home`."""
    value = _VARIABLE.sub(lambda m: env.get(m.group(1)) or m.group(2), raw)
    if value == "~" or value.startswith(("~/", "~\\")):
        return home / value[2:]
    return Path(value)


def load(
    path: Path = MANIFEST_PATH,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> list[Module]:
    """Load every module in the manifest at `path`; `env` and `home` default to the real ones."""
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ManifestError(f"manifest not found: {path}") from None
    except tomllib.TOMLDecodeError as error:
        raise ManifestError(f"{path.name}: invalid TOML: {error}") from None

    unknown = sorted(set(data) - {"modules"})
    if unknown:
        raise ManifestError(f"{path.name}: unknown field(s): {', '.join(unknown)}")
    modules = data.get("modules")
    if not isinstance(modules, dict) or not modules:
        raise ManifestError(f"{path.name}: needs at least one [modules.<name>] table")
    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)
    return [_module(name, table, env, home) for name, table in modules.items()]


def _module(name: str, data: object, env: Mapping[str, str], home: Path) -> Module:
    if not NAME.fullmatch(name):
        raise ManifestError(f"module '{name}': name may only use letters, digits, '-' and '_'")
    if not isinstance(data, dict):
        raise ManifestError(f"module '{name}': must be a table")
    target = data.get("target")
    if not isinstance(target, str) or not target.strip():
        raise ManifestError(f"module '{name}': 'target' must be a non-empty string")
    include = _patterns(name, data, "include", required=True)
    exclude = _patterns(name, data, "exclude", required=False)
    secrets = data.get("secrets", False)
    if not isinstance(secrets, bool):
        raise ManifestError(f"module '{name}': 'secrets' must be true or false")
    unknown = sorted(set(data) - {"target", "include", "exclude", "secrets"})
    if unknown:
        raise ManifestError(f"module '{name}': unknown field(s): {', '.join(unknown)}")
    return Module(name, resolve_target(target, env, home), include, exclude, secrets)


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
