import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from breakglass import cli, scan
from breakglass.state import State

from .helpers import clone, commit, git, origin, remote_branches


class SaveCommandTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.bare = origin(self.dir)
        self.projects = self.dir / "projects"
        self.dirty = clone(self.bare, self.projects / "dirty")
        (self.dirty / "file.txt").write_text("edited\n", encoding="utf-8")
        clone(self.bare, self.projects / "clean")
        self.state = State(self.dir / "local")

    def run_save(self, folder=None, dry_run=False, ask=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.run_save(folder, None, dry_run, state=self.state, ask=ask or (lambda prompt: ""))
        return code, out.getvalue()

    def test_dry_run_only_reports(self):
        code, out = self.run_save(self.projects, dry_run=True)
        self.assertEqual(code, 0)
        self.assertIn("2 repositories, 1 with unsaved work.", out)
        self.assertIn("dirty: 1 file -> origin", out)
        self.assertEqual(list(remote_branches(self.bare)), ["main"])

    def test_save_pushes_everything_and_keeps_a_record(self):
        code, out = self.run_save(self.projects)
        self.assertEqual(code, 0, out)
        self.assertIn("Saved 1 of 1 repositories.", out)
        [emergency] = [b for b in remote_branches(self.bare) if b.startswith("emergency/")]
        self.assertTrue(emergency.endswith("/wip-on-main"))
        [record] = self.state.runs.glob("*.json")
        self.assertEqual(json.loads(record.read_text(encoding="utf-8"))["folder"], str(self.projects))
        self.assertEqual(self.state.folder(), self.projects)

    def test_the_folder_chosen_last_is_used(self):
        self.state.remember_folder(self.projects)
        code, out = self.run_save()
        self.assertEqual(code, 0, out)
        self.assertIn(f"Looking for repositories in {self.projects}", out)

    def test_without_a_folder_it_searches_home_without_cloud_folders(self):
        with mock.patch.object(scan, "home_fallback", return_value=(self.projects, set())):
            code, out = self.run_save(dry_run=True)
        self.assertIn("No project folder chosen yet", out)
        self.assertIsNone(self.state.folder())  # the fallback is not remembered

    def test_a_repository_without_remote_fails_the_run(self):
        lost = self.projects / "lost"
        git(self.dir, "init", "-q", "-b", "main", str(lost))
        commit(lost, "only here")
        code, out = self.run_save(self.projects)
        self.assertEqual(code, 1)
        self.assertIn("lost: 1 branch -> NO REMOTE", out)
        self.assertIn("no remote to save to", out)

    def test_parse_args(self):
        self.assertEqual(cli.parse_args([]).command, "gui")
        self.assertEqual(cli.parse_args(["--path", "x"]).path, Path("x"))
        self.assertTrue(cli.parse_args(["save", "--dry-run"]).dry_run)
