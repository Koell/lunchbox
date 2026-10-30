"""Smoke tests of the Tk window; skipped without tkinter or a display (CI uses xvfb on Linux).

With BREAKGLASS_GUI_TESTS=required, as in CI, they run anyway and fail instead of being skipped.
"""

import gc
import os
import tempfile
import time
import unittest
from pathlib import Path

from breakglass import ssh
from breakglass.state import State

from .helpers import clone, commit, origin, remote_branches

# One Tk for the whole module; each test's window is a Toplevel on it. Creating a Tk again after
# destroying one crashes Tk on macOS (seen with Python 3.14), so this one is only destroyed at the end.
try:
    import tkinter

    ROOT = tkinter.Tk()
    ROOT.withdraw()
except Exception:  # no tkinter, or no display
    ROOT = None
DISPLAY = ROOT is not None
REQUIRED = os.environ.get("BREAKGLASS_GUI_TESTS") == "required"


def tearDownModule():
    global ROOT
    if ROOT is not None:
        ROOT.destroy()
        ROOT = None
        gc.collect()


def answers(*replies):
    """An ask_passphrase stand-in giving `replies` in order and recording the errors it was shown."""
    errors = []

    def ask(parent, key, used_by, error=None):
        errors.append(error)
        return replies[len(errors) - 1]

    ask.errors = errors
    return ask


@unittest.skipUnless(DISPLAY or REQUIRED, "no tkinter or no display")
class AppTest(unittest.TestCase):
    def setUp(self):
        import tkinter as tk

        self.assertIsNotNone(ROOT, "no tkinter or no display")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.bare = origin(self.dir)
        self.projects = self.dir / "projects"
        self.dirty = clone(self.bare, self.projects / "priv" / "dirty")
        (self.dirty / "file.txt").write_text("edited\n", encoding="utf-8")
        self.ahead = clone(self.bare, self.projects / "ahead")
        commit(self.ahead, "unpushed")
        clone(self.bare, self.projects / "clean")
        self.state = State(self.dir / "local")
        self.root = tk.Toplevel(ROOT)
        self.root.withdraw()
        self.addCleanup(self.close)

    def close(self):
        """Destroy the test's window and free it and every App on it here, in the main thread. Left
        to the cycle collector, they could be freed in a worker thread of the next test, and Tk
        aborts the whole process when its objects are freed in the wrong thread."""
        self.root.destroy()
        self.root = None
        gc.collect()

    def open(self, ask=None, requested="projects", choose=None):
        from breakglass.app import App

        if requested == "projects":
            requested = self.projects
        self.chosen = []

        def choose_folder(parent, current):
            self.chosen.append(current)
            return choose

        app = App(self.root, requested, ask_passphrase=ask or answers(), choose_folder=choose_folder,
                  state=self.state)
        self.pump(lambda: app.folder is None and self.chosen or app.folder and not app.reading)
        return app

    def pump(self, done, limit=60):
        end = time.monotonic() + limit
        while not done():
            self.assertLess(time.monotonic(), end, "timed out")
            self.root.update()
            time.sleep(0.01)

    def save(self, app):
        app.save()
        self.pump(lambda: not app.saving)

    def test_only_repositories_with_unsaved_work_are_listed_all_ticked(self):
        app = self.open()
        self.assertEqual(sorted(app.repos), ["ahead", "priv/dirty"])
        self.assertEqual(app.view.set("priv/dirty", "unsaved"), "1 file")
        self.assertEqual(app.view.set("ahead", "unsaved"), "1 branch")
        self.assertEqual(app.check("."), "on")
        self.assertEqual(app.summary.get(), "2 of 3 repositories with unsaved work · 2 ticked")

    def test_sash_is_placed_once_the_window_shows(self):
        app = self.open()
        time.sleep(0.2)  # a slow first start can show the window this late (seen on Manjaro)
        self.root.update()
        self.root.deiconify()
        self.pump(lambda: app.panes.winfo_viewable() and app.panes.winfo_width() > 1)
        self.root.update()
        self.assertAlmostEqual(app.panes.sashpos(0) / app.panes.winfo_width(), 0.62, delta=0.01)

    def test_save_only_the_ticked_and_refresh_ticks_everything_again(self):
        app = self.open()
        app.toggle("ahead")
        self.save(app)
        self.assertEqual(app.status["priv/dirty"], ("saved", "ok"))
        self.assertNotIn("ahead", app.results)
        self.assertTrue(app.summary.get().startswith("Saved 1 of 1"))
        self.assertTrue(any(b.startswith("emergency/") for b in remote_branches(self.bare)))
        self.assertEqual(len(list(self.state.runs.glob("*.json"))), 1)

        app.refresh()
        self.pump(lambda: not app.reading)
        self.assertEqual(app.unticked, set())

    def test_a_repository_without_remote_is_red_and_not_saved(self):
        from breakglass.proc import run

        lost = self.projects / "lost"
        run(["git", "init", "-q", "-b", "main", str(lost)])
        (lost / "notes.txt").write_text("only here\n", encoding="utf-8")
        app = self.open()
        self.assertEqual(app.status["lost"], ("no remote", "failed"))
        self.save(app)
        self.assertNotIn("lost", app.results)

    def test_skipping_a_key_skips_its_repositories(self):
        app = self.open(ask=answers("wrong", None))
        app.repos["ahead"].keys = [self.dir / "key"]
        added = []

        class FakeAgent:
            env = {}

            def __init__(self, tools):
                pass

            def add(self, key, passphrase):
                added.append(passphrase)
                return "Wrong passphrase."

            def close(self):
                added.append("closed")

        real = ssh.Agent
        ssh.Agent = FakeAgent
        self.addCleanup(setattr, ssh, "Agent", real)
        app.tools = app.tools or object()
        self.save(app)
        self.assertEqual(app.ask_passphrase.errors, [None, "Wrong passphrase."])
        self.assertEqual(added, ["wrong", "closed"])
        self.assertEqual(app.status["ahead"], ("skipped", "muted"))
        self.assertEqual(app.status["priv/dirty"], ("saved", "ok"))
        self.assertIsNone(app.agent)

    def test_first_start_asks_for_the_folder(self):
        app = self.open(requested=None, choose=self.projects)
        self.assertEqual(self.chosen, [None])
        self.assertEqual(app.folder, self.projects)
        self.assertEqual(self.state.folder(), self.projects)
