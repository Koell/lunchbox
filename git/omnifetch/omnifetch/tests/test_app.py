"""Smoke tests of the Tk window; skipped without tkinter or a display (CI uses xvfb on Linux).

With OMNIFETCH_GUI_TESTS=required, as in CI, they run anyway and fail instead of being skipped.
"""

import gc
import os
import tempfile
import time
import unittest
from pathlib import Path

from omnifetch import repos
from omnifetch.state import State

from .helpers import clone, origin, push_new_commit

# One Tk for the whole module; each test's window is a Toplevel on it. Creating a Tk again after
# destroying one crashes Tk on macOS (seen with Python 3.14), so this one is only destroyed at the end.
try:
    import tkinter

    ROOT = tkinter.Tk()
    ROOT.withdraw()
except Exception:  # no tkinter, or no display
    ROOT = None
DISPLAY = ROOT is not None
REQUIRED = os.environ.get("OMNIFETCH_GUI_TESTS") == "required"


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
        for name in ("a", "priv/b", "priv/c"):
            clone(self.bare, self.projects / name)
        push_new_commit(self.bare, self.dir / "other")
        self.state = State(self.dir / "local" / "gui.json")
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
        """A window on `requested` (the test projects by default; None: the remembered folder)."""
        from omnifetch.app import App

        if requested == "projects":
            requested = self.projects
        self.chosen = []

        def choose_folder(parent, current):
            self.chosen.append(current)
            return choose

        app = App(self.root, requested, ask_passphrase=ask or answers(), choose_folder=choose_folder,
                  state=self.state)
        self.pump(lambda: app.folder is None and self.chosen or app.repos and not app.reading)
        return app

    def pump(self, done, limit=60):
        end = time.monotonic() + limit
        while not done():
            self.assertLess(time.monotonic(), end, "timed out")
            self.root.update()
            time.sleep(0.01)

    def fetch(self, app):
        app.fetch()
        self.pump(lambda: not app.fetching)

    def test_tree_shows_folders_and_repositories(self):
        app = self.open()
        self.assertEqual(app.view.get_children("."), ("a", "priv"))
        self.assertEqual(app.view.get_children("priv"), ("priv/b", "priv/c"))
        self.assertEqual(app.view.set("a", "branch"), "main")
        self.assertEqual(app.summary.get(), "3 repositories · 3 ticked")

    def test_sash_is_placed_once_the_window_shows(self):
        app = self.open()
        time.sleep(0.2)  # a slow first start can show the window this late (seen on Manjaro)
        self.root.update()
        self.root.deiconify()
        self.pump(lambda: app.panes.winfo_viewable() and app.panes.winfo_width() > 1)
        self.root.update()
        self.assertAlmostEqual(app.panes.sashpos(0) / app.panes.winfo_width(), 0.62, delta=0.01)

    def test_ticks_cascade_and_are_remembered(self):
        app = self.open()
        app.toggle("priv/b")
        self.assertEqual((app.check("priv"), app.check(".")), ("partial", "partial"))
        app.toggle("priv")
        self.assertEqual(app.check("priv"), "on")
        app.toggle(".")
        self.assertEqual(app.check("."), "off")
        self.assertEqual(str(app.fetch_button["state"]), "disabled")
        app.toggle("a")
        self.assertEqual(self.state.unticked(self.projects), {"priv/b", "priv/c"})

        again = self.open()
        self.assertEqual(again.unticked, {"priv/b", "priv/c"})

    def test_fetch_only_the_ticked(self):
        app = self.open()
        app.toggle("priv")
        self.fetch(app)
        self.assertEqual(app.status["a"], ("1 behind", "behind"))
        self.assertNotIn("priv/b", app.output)
        self.assertEqual(app.summary.get(), "Fetched 1 · 1 behind")

    def test_skipping_a_key_skips_its_repositories(self):
        app = self.open(ask=answers("wrong", None))
        key = self.dir / "key"
        app.repos["priv/b"].keys = [key]
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

        real = repos.Agent
        repos.Agent = FakeAgent
        self.addCleanup(setattr, repos, "Agent", real)
        self.fetch(app)
        self.assertEqual(app.ask_passphrase.errors, [None, "Wrong passphrase."])
        self.assertEqual(added, ["wrong", "closed"])
        self.assertEqual(app.status["priv/b"], ("skipped", "muted"))
        self.assertEqual(app.status["priv/c"], ("1 behind", "behind"))
        self.assertIsNone(app.agent)

    def test_first_start_asks_for_the_folder_and_remembers_it(self):
        app = self.open(requested=None, choose=self.projects)
        self.assertEqual(self.chosen, [None])
        self.assertEqual((app.folder, app.path_var.get()), (self.projects, str(self.projects)))
        self.assertEqual(self.state.folder(), self.projects)

        again = self.open(requested=None)
        self.assertEqual((self.chosen, again.folder), ([], self.projects))

    def test_cancelled_first_start_waits_for_a_folder(self):
        app = self.open(requested=None, choose=None)
        self.assertIsNone(app.folder)
        self.assertEqual(app.summary.get(), "Choose the folder that holds your projects.")
        self.assertEqual(str(app.fetch_button["state"]), "disabled")
        self.assertEqual(str(app.change_button["state"]), "normal")

    def test_a_remembered_folder_that_is_gone_asks_again(self):
        self.state.remember_folder(self.dir / "gone")
        app = self.open(requested=None, choose=self.projects)
        self.assertEqual(self.chosen, [None])
        self.assertEqual(app.folder, self.projects)

    def test_change_switches_folder_and_keeps_ticks_per_folder(self):
        app = self.open()
        app.toggle("a")
        other = self.dir / "other-projects"
        clone(self.bare, other / "x")
        app.choose_folder = lambda parent, current: other
        app.change_folder()
        self.pump(lambda: app.repos and not app.reading)
        self.assertEqual((app.folder, list(app.repos)), (other, ["x"]))
        self.assertEqual(app.check("."), "on")
        self.assertEqual(self.state.folder(), other)
        self.assertEqual(self.state.unticked(self.projects), {"a"})
