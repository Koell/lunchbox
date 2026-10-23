"""Per-machine GUI state in `local/gui.json` next to `export/`: the bento opened last.

Nothing here matters for correctness: a missing or damaged file just means starting from the
default bento. The CLI never reads it.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .. import archive

STATE_PATH = Path(__file__).resolve().parents[2] / "local" / "gui.json"


class State:
    def __init__(self, path: Path = STATE_PATH) -> None:
        self.path = Path(path)

    def remembered(self) -> Path | None:
        """The bento opened last, or None if nothing (readable) is remembered."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        bento = data.get("bento") if isinstance(data, dict) else None
        return Path(bento) if isinstance(bento, str) and bento else None

    def remember(self, bento: Path) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps({"bento": str(bento)}, indent=2) + "\n"
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".gui.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(data)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def initial_bento(
    state: State, requested: Path | None = None, default: Path = archive.DEFAULT_PATH
) -> tuple[Path, str | None]:
    """The bento to open at start, and a note if the remembered one is gone.

    An explicitly `requested` bento wins; otherwise the remembered one, if it still exists;
    otherwise the default.
    """
    if requested is not None:
        return requested, None
    remembered = state.remembered()
    if remembered is None or remembered == default:
        return default, None
    if remembered.is_file():
        return remembered, None
    return default, f"The bento opened last, {remembered}, is gone; showing the default instead."
