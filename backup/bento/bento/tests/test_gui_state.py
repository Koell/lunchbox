import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bento import gui
from bento.gui.state import STATE_PATH, State, initial_bento

PROJECT = Path(__file__).resolve().parents[2]


class StateTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.state = State(self.dir / "local" / "gui.json")
        self.default = self.dir / "export" / "bento.tar.gz"

    def bento(self, name):
        path = self.dir / name
        path.write_bytes(b"x")
        return path

    def test_state_lives_in_local_next_to_export(self):
        self.assertEqual(STATE_PATH, PROJECT / "local" / "gui.json")

    def test_remember_round_trip(self):
        self.assertIsNone(self.state.remembered())
        self.state.remember(self.dir / "usb" / "bento.tar.gz")
        self.assertEqual(self.state.remembered(), self.dir / "usb" / "bento.tar.gz")
        self.assertEqual([p.name for p in self.state.path.parent.iterdir()], ["gui.json"])

    def test_damaged_state_is_ignored(self):
        self.state.path.parent.mkdir()
        for raw in ("{broken", "[]", '{"bento": 3}', '{"bento": ""}'):
            with self.subTest(raw):
                self.state.path.write_text(raw, encoding="utf-8")
                self.assertIsNone(self.state.remembered())

    def test_initial_bento(self):
        self.assertEqual(initial_bento(self.state, default=self.default), (self.default, None))
        other = self.bento("other.tar.gz")
        self.state.remember(other)
        self.assertEqual(initial_bento(self.state, default=self.default), (other, None))
        requested = self.dir / "requested.tar.gz"
        self.assertEqual(initial_bento(self.state, requested, self.default), (requested, None))

    def test_a_remembered_bento_that_is_gone_falls_back_with_a_note(self):
        self.state.remember(self.dir / "gone.tar.gz")
        path, note = initial_bento(self.state, default=self.default)
        self.assertEqual(path, self.default)
        self.assertIn("gone.tar.gz", note)


class RunTest(unittest.TestCase):
    def run_gui(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = gui.run([])
        return code, err.getvalue()

    def test_without_tkinter_one_line_names_the_package(self):
        with mock.patch.dict(sys.modules, {"tkinter": None}):
            code, err = self.run_gui()
        self.assertEqual(code, 1)
        self.assertEqual(err.count("\n"), 1)
        self.assertIn("pacman -S tk", err)
        self.assertIn("python3-tk", err)
        self.assertIn("the CLI works without it", err)

    def test_without_a_display_one_line_points_to_the_cli(self):
        try:
            import tkinter
        except ImportError:
            self.skipTest("no tkinter")
        error = tkinter.TclError("no display name and no $DISPLAY environment variable")
        with mock.patch.object(tkinter, "Tk", side_effect=error):
            code, err = self.run_gui()
        self.assertEqual(code, 1)
        self.assertEqual(err.count("\n"), 1)
        self.assertIn("no display", err)
        self.assertIn("python -m bento status", err)


class ImportTest(unittest.TestCase):
    def test_the_cli_does_not_import_tkinter(self):
        code = (
            "import sys; from bento import cli; cli._parser().parse_args(['status']);"
            " sys.exit('tkinter' in sys.modules)"
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=PROJECT)
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
