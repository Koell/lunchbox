"""Per-machine state in `local/`: the project folder in `gui.json`, and a record of every save run
in `runs/`. Neither is committed.

Nothing here matters for correctness: a missing or damaged `gui.json` just means asking for the
project folder again. Ticks are never kept: every start saves everything.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

LOCAL = Path(__file__).resolve().parents[1] / "local"


class State:
    def __init__(self, folder: Path = LOCAL) -> None:
        self.local = Path(folder)
        self.path = self.local / "gui.json"

    @property
    def runs(self) -> Path:
        return self.local / "runs"

    def folder(self) -> Path | None:
        """The project folder chosen last, or None if nothing (readable) is remembered."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        folder = data.get("folder") if isinstance(data, dict) else None
        return Path(folder) if isinstance(folder, str) and folder else None

    def remember_folder(self, folder: Path) -> None:
        try:
            self.local.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.local, prefix=".gui.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    file.write(json.dumps({"folder": str(folder)}, indent=2) + "\n")
                os.replace(tmp, self.path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        except OSError:
            pass  # only a convenience: the choice just doesn't survive a restart
