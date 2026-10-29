import tempfile
import unittest
from pathlib import Path

from omnifetch.state import State


class StateTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "local" / "gui.json"
        self.state = State(self.path)

    def test_nothing_remembered(self):
        self.assertIsNone(self.state.folder())
        self.assertEqual(self.state.unticked(Path("C:/projects")), set())
        self.assertFalse(self.state.prune())

    def test_remembers_each_setting_without_losing_the_others(self):
        projects, work = Path("C:/projects"), Path("D:/work")
        self.state.remember_folder(projects)
        self.state.remember_unticked(projects, {"b", "a"})
        self.state.remember_unticked(work, {"x"})
        self.state.remember_prune(True)
        again = State(self.path)
        self.assertEqual(again.folder(), projects)
        self.assertEqual(again.unticked(projects), {"a", "b"})
        self.assertEqual(again.unticked(work), {"x"})
        self.assertTrue(again.prune())

    def test_damaged_file_means_starting_fresh(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(self.state.folder())
        self.state.remember_folder(Path("C:/projects"))
        self.assertEqual(self.state.folder(), Path("C:/projects"))
