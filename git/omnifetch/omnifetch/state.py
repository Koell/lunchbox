"""Per-machine state in `local/gui.json`: the project folder, the unticked repositories per folder,
and whether to prune.

Nothing here matters for correctness: a missing or damaged file just means asking for the project
folder again and starting with everything ticked.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

STATE_PATH = Path(__file__).resolve().parents[1] / "local" / "gui.json"


class State:
    def __init__(self, path: Path = STATE_PATH) -> None:
        self.path = Path(path)

    def folder(self) -> Path | None:
        """The project folder chosen last, or None if nothing (readable) is remembered."""
        folder = self._read().get("folder")
        return Path(folder) if isinstance(folder, str) and folder else None

    def remember_folder(self, folder: Path) -> None:
        self._update(folder=str(folder))

    def unticked(self, folder: Path) -> set[str]:
        """The repositories below `folder` the user unticked, by their name relative to it."""
        names = self._read().get("unticked", {}).get(str(folder), [])
        return {name for name in names if isinstance(name, str)}

    def remember_unticked(self, folder: Path, names: set[str]) -> None:
        unticked = self._read().get("unticked", {})
        unticked[str(folder)] = sorted(names)
        self._update(unticked=unticked)

    def prune(self) -> bool:
        return self._read().get("prune") is True

    def remember_prune(self, prune: bool) -> None:
        self._update(prune=prune)

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _update(self, **values: Any) -> None:
        data = self._read()
        data.update(values)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".gui.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    file.write(json.dumps(data, indent=2) + "\n")
                os.replace(tmp, self.path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        except OSError:
            pass  # only a convenience: the choice just doesn't survive a restart
