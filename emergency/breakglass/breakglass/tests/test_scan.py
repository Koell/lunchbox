import tempfile
import unittest
from pathlib import Path
from unittest import mock

from breakglass import scan

from .helpers import clone, commit, git, local_state, origin


class ScanTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.bare = origin(self.dir)
        self.projects = self.dir / "projects"
        self.repo = clone(self.bare, self.projects / "app")

    def inspect(self, path=None):
        repo = scan.Repo(path or self.repo, "app")
        scan.inspect(repo)
        return repo

    def test_a_clean_repository_has_nothing_unsaved(self):
        repo = self.inspect()
        self.assertFalse(repo.unsaved)
        self.assertEqual((repo.head_branch, repo.remote), ("main", "origin"))
        self.assertEqual(repo.email, "tester@example.invalid")

    def test_uncommitted_changes_untracked_too_but_never_ignored(self):
        (self.repo / ".gitignore").write_text(".env\n", encoding="utf-8")
        git(self.repo, "add", ".gitignore")
        (self.repo / "file.txt").write_text("edited\n", encoding="utf-8")
        (self.repo / "new").mkdir()
        (self.repo / "new" / "draft.sql").write_text("select 1;\n", encoding="utf-8")
        (self.repo / ".env").write_text("SECRET=1\n", encoding="utf-8")
        repo = self.inspect()
        self.assertEqual(sorted(repo.changes), [".gitignore", "file.txt", "new/draft.sql"])
        self.assertEqual(repo.summary(), "3 files")

    def test_large_untracked_files_are_left_out(self):
        (self.repo / "dump.sql").write_bytes(b"x" * 2000)
        (self.repo / "small.txt").write_text("x", encoding="utf-8")
        with mock.patch.object(scan, "LARGE", 1000):
            repo = self.inspect()
        self.assertEqual(repo.changes, ["small.txt"])
        self.assertEqual(repo.large, [("dump.sql", 2000)])

    def test_branches_on_no_remote(self):
        git(self.repo, "switch", "-q", "-c", "local-only")
        commit(self.repo, "local work")
        git(self.repo, "switch", "-q", "-c", "pointer-only", "main")  # on the remote already
        repo = self.inspect()
        self.assertEqual([b.name for b in repo.branches], ["local-only"])
        self.assertEqual(repo.summary(), "1 branch")

    def test_a_branch_behind_its_remote_is_saved_already(self):
        other = clone(self.bare, self.dir / "other")
        commit(other, "later")
        git(other, "push", "-q", "origin", "main")
        git(self.repo, "fetch", "-q")
        self.assertFalse(self.inspect().unsaved)

    def test_unpushed_commits_and_their_upstream(self):
        commit(self.repo, "ahead")
        [branch] = self.inspect().branches
        self.assertEqual((branch.name, branch.upstream_remote, branch.upstream_ref),
                         ("main", "origin", "refs/heads/main"))

    def test_a_branch_deleted_on_the_server_is_gone(self):
        git(self.repo, "switch", "-q", "-c", "feature/squashed")
        commit(self.repo, "work that got squash-merged")
        git(self.repo, "push", "-q", "-u", "origin", "feature/squashed")
        git(self.repo, "push", "-q", "origin", "--delete", "feature/squashed")
        git(self.repo, "fetch", "-q", "--prune")
        [branch] = self.inspect().branches
        self.assertEqual((branch.name, branch.gone), ("feature/squashed", True))

    def test_stashes(self):
        for n in range(2):
            (self.repo / "file.txt").write_text(f"stash {n}\n", encoding="utf-8")
            git(self.repo, "stash", "-q")
        repo = self.inspect()
        self.assertEqual(len(repo.stashes), 2)
        self.assertEqual(repo.summary(), "2 stashes")

    def test_detached_head(self):
        git(self.repo, "switch", "-q", "--detach")
        repo = self.inspect()
        self.assertIsNone(repo.head_branch)
        self.assertEqual(len(repo.head_sha), 40)

    def test_reading_changes_nothing(self):
        (self.repo / "file.txt").write_text("edited\n", encoding="utf-8")
        (self.repo / "untracked.txt").write_text("new\n", encoding="utf-8")
        before = local_state(self.repo)
        self.inspect()
        self.assertEqual(local_state(self.repo), before)

    def test_choose_remote(self):
        repo = scan.Repo(self.repo, "app", head_branch="main")
        self.assertIsNone(scan.choose_remote(repo))
        repo.remotes = {"zeta": "z", "alpha": "a"}
        self.assertEqual(scan.choose_remote(repo), "alpha")
        repo.branches = [scan.Branch("main", "0" * 40, "zeta", "refs/heads/main")]
        self.assertEqual(scan.choose_remote(repo), "zeta")
        repo.remotes["origin"] = "o"
        self.assertEqual(scan.choose_remote(repo), "origin")

    def test_find_repositories_skips_folders_and_does_not_look_inside_repositories(self):
        clone(self.bare, self.projects / "deep" / "er" / "lib")
        clone(self.bare, self.projects / "app" / "vendored")
        clone(self.bare, self.projects / "synced" / "old")
        clone(self.bare, self.projects / "web" / "node_modules" / "pkg")
        found = scan.find_repositories(self.projects, skip={self.projects / "synced"})
        self.assertEqual([p.relative_to(self.projects).as_posix() for p in found], ["app", "deep/er/lib"])

    def test_home_fallback_skips_appdata_and_cloud_folders(self):
        with mock.patch.object(Path, "home", return_value=self.dir), \
                mock.patch.object(scan, "cloud_roots", return_value={self.dir / "Company - Documents"}):
            for name in ("OneDrive", "OneDrive - Company", "Dropbox", "projects"):
                (self.dir / name).mkdir(exist_ok=True)
            home, skip = scan.home_fallback()
        self.assertEqual(home, self.dir)
        names = {p.name for p in skip}
        self.assertTrue({"AppData", "OneDrive", "OneDrive - Company", "Dropbox", "Company - Documents"} <= names)
        self.assertNotIn("projects", names)
