import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from breakglass import save, scan
from breakglass.proc import quiet_env

from .helpers import clone, commit, git, local_state, origin, remote_branches, show

STAMP = "2026-10-29-1412"
PREFIX = f"emergency/tester/{STAMP}"


class SaveTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.bare = origin(self.dir)
        self.repo = clone(self.bare, self.dir / "projects" / "app")

    def save(self, run_stamp=STAMP):
        repo = scan.Repo(self.repo, "app")
        scan.inspect(repo)
        return save.save(repo, run_stamp, quiet_env())

    def test_uncommitted_changes_go_to_an_emergency_branch_and_nothing_local_changes(self):
        (self.repo / ".gitignore").write_text(".env\n", encoding="utf-8")
        git(self.repo, "add", ".gitignore")
        (self.repo / "staged.txt").write_text("staged\n", encoding="utf-8")
        git(self.repo, "add", "staged.txt")
        (self.repo / "file.txt").write_text("edited, not staged\n", encoding="utf-8")
        (self.repo / "draft.sql").write_text("select 1;\n", encoding="utf-8")
        (self.repo / ".env").write_text("SECRET=1\n", encoding="utf-8")
        before = local_state(self.repo)

        result = self.save()

        self.assertTrue(result.ok, result)
        [outcome] = result.outcomes
        self.assertEqual((outcome.kind, outcome.ref), ("snapshot", f"refs/heads/{PREFIX}/wip-on-main"))
        self.assertEqual(local_state(self.repo), before)
        wip = f"{PREFIX}/wip-on-main"
        self.assertEqual(show(self.bare, wip, "file.txt"), "edited, not staged\n")
        self.assertEqual(show(self.bare, wip, "draft.sql"), "select 1;\n")
        self.assertEqual(show(self.bare, wip, "staged.txt"), "staged\n")
        self.assertIsNone(show(self.bare, wip, ".env"))
        # Like git stash: the second parent holds only what was staged
        self.assertEqual(show(self.bare, f"{wip}^2", "staged.txt"), "staged\n")
        self.assertEqual(show(self.bare, f"{wip}^2", "file.txt"), "first\n")
        self.assertIsNone(show(self.bare, f"{wip}^2", "draft.sql"))
        self.assertEqual(git(self.bare, "rev-parse", f"{wip}^1"), git(self.repo, "rev-parse", "HEAD"))

    def test_large_untracked_files_stay_out_of_the_snapshot(self):
        (self.repo / "dump.sql").write_bytes(b"x" * 2000)
        (self.repo / "small.txt").write_text("small\n", encoding="utf-8")
        repo = scan.Repo(self.repo, "app")
        scan.inspect(repo)
        repo.changes.remove("dump.sql")
        repo.large = [("dump.sql", 2000)]
        self.assertTrue(save.save(repo, STAMP, quiet_env()).ok)
        self.assertIsNone(show(self.bare, f"{PREFIX}/wip-on-main", "dump.sql"))
        self.assertEqual(show(self.bare, f"{PREFIX}/wip-on-main", "small.txt"), "small\n")

    def test_a_local_only_branch_gets_a_normal_push_without_setting_an_upstream(self):
        git(self.repo, "switch", "-q", "-c", "feature/x")
        commit(self.repo, "feature work")
        before = local_state(self.repo)
        result = self.save()
        self.assertTrue(result.ok, result)
        self.assertEqual(remote_branches(self.bare)["feature/x"], git(self.repo, "rev-parse", "feature/x"))
        self.assertEqual(local_state(self.repo), before)  # no upstream configured, nothing else

    def test_unpushed_commits_go_to_their_upstream(self):
        commit(self.repo, "ahead")
        self.assertTrue(self.save().ok)
        self.assertEqual(remote_branches(self.bare)["main"], git(self.repo, "rev-parse", "main"))

    def test_a_rejected_push_goes_to_an_emergency_branch_and_never_forces(self):
        other = clone(self.bare, self.dir / "other")
        commit(other, "someone else's work")
        git(other, "push", "-q", "origin", "main")
        server_main = remote_branches(self.bare)["main"]
        commit(self.repo, "my work")  # diverged: a normal push is rejected

        result = self.save()

        self.assertTrue(result.ok, result)
        [outcome] = result.outcomes
        self.assertEqual(outcome.ref, f"refs/heads/{PREFIX}/main")
        self.assertIn("normal push rejected", outcome.message)
        self.assertEqual(remote_branches(self.bare)["main"], server_main)
        self.assertEqual(remote_branches(self.bare)[f"{PREFIX}/main"], git(self.repo, "rev-parse", "main"))

    def test_a_branch_deleted_on_the_server_is_not_brought_back(self):
        git(self.repo, "switch", "-q", "-c", "feature/squashed")
        commit(self.repo, "work that got squash-merged")
        git(self.repo, "push", "-q", "-u", "origin", "feature/squashed")
        git(self.repo, "push", "-q", "origin", "--delete", "feature/squashed")
        git(self.repo, "fetch", "-q", "--prune")

        result = self.save()

        self.assertTrue(result.ok, result)
        [outcome] = result.outcomes
        self.assertEqual(outcome.ref, f"refs/heads/{PREFIX}/feature/squashed")
        self.assertEqual(outcome.message, "its branch on the server was deleted")
        branches = remote_branches(self.bare)
        self.assertNotIn("feature/squashed", branches)
        self.assertEqual(branches[f"{PREFIX}/feature/squashed"], git(self.repo, "rev-parse", "feature/squashed"))

    def test_stashes_get_emergency_branches(self):
        for n in range(2):
            (self.repo / "file.txt").write_text(f"stash {n}\n", encoding="utf-8")
            git(self.repo, "stash", "-q")
        stashes = git(self.repo, "stash", "list", "--format=%H").split()
        self.assertTrue(self.save().ok)
        branches = remote_branches(self.bare)
        self.assertEqual([branches[f"{PREFIX}/stash-0"], branches[f"{PREFIX}/stash-1"]], stashes)

    def test_a_second_run_in_the_same_minute_gets_new_names(self):
        (self.repo / "file.txt").write_text("one\n", encoding="utf-8")
        self.assertTrue(self.save().ok)
        (self.repo / "file.txt").write_text("two\n", encoding="utf-8")
        result = self.save()
        self.assertEqual(result.outcomes[0].ref, f"refs/heads/{PREFIX}/wip-on-main-2")
        self.assertEqual(show(self.bare, f"{PREFIX}/wip-on-main", "file.txt"), "one\n")
        self.assertEqual(show(self.bare, f"{PREFIX}/wip-on-main-2", "file.txt"), "two\n")

    def test_detached_head_and_a_repository_without_commits(self):
        git(self.repo, "switch", "-q", "--detach")
        (self.repo / "file.txt").write_text("edited\n", encoding="utf-8")
        sha = git(self.repo, "rev-parse", "--short=7", "HEAD")
        self.assertEqual(self.save().outcomes[0].ref, f"refs/heads/{PREFIX}/wip-on-detached-{sha}")

        empty = self.dir / "empty"
        git(self.dir, "init", "-q", "-b", "main", str(empty))
        git(empty, "remote", "add", "origin", str(self.bare))
        git(empty, "config", "user.email", "tester@example.invalid")
        (empty / "first.txt").write_text("first\n", encoding="utf-8")
        repo = scan.Repo(empty, "empty")
        scan.inspect(repo)
        self.assertTrue(save.save(repo, "2026-10-29-1500", quiet_env()).ok)
        self.assertEqual(show(self.bare, "emergency/tester/2026-10-29-1500/wip-on-main", "first.txt"), "first\n")

    def test_no_identity_configured_still_saves(self):
        git(self.repo, "config", "--unset", "user.name")
        git(self.repo, "config", "user.useConfigOnly", "true")
        git(self.repo, "config", "user.email", "")  # nothing to fall back on
        (self.repo / "file.txt").write_text("edited\n", encoding="utf-8")
        no_global = {"GIT_CONFIG_GLOBAL": str(self.dir / "none"), "GIT_CONFIG_NOSYSTEM": "1"}
        with mock.patch.dict(os.environ, no_global):
            repo = scan.Repo(self.repo, "app")
            scan.inspect(repo)
            result = save.save(repo, STAMP, quiet_env())
        self.assertTrue(result.ok, result)
        ref = result.outcomes[0].ref
        self.assertIn(f"/{save.who(None)}/", ref)  # no email: the login name
        self.assertEqual(git(self.bare, "log", "-1", "--format=%an <%ae>", ref), "breakglass <breakglass@localhost>")

    def test_no_remote(self):
        git(self.repo, "remote", "remove", "origin")
        (self.repo / "file.txt").write_text("edited\n", encoding="utf-8")
        result = self.save()
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "no remote to save to")

    def test_who(self):
        self.assertEqual(save.who("jane.doe@example.com"), "jane.doe")
        self.assertEqual(save.who("weird name+tag@example.com"), "weird-name-tag")

    def test_record(self):
        (self.repo / "file.txt").write_text("edited\n", encoding="utf-8")
        repo = scan.Repo(self.repo, "app")
        scan.inspect(repo)
        repo.large = [("dump.sql", 99)]
        result = save.save(repo, STAMP, quiet_env())
        records = self.dir / "runs"
        first = save.write_record(records, STAMP, self.dir, [result], [repo])
        second = save.write_record(records, STAMP, self.dir, [result], [repo])
        self.assertEqual((first.name, second.name), (f"{STAMP}.json", f"{STAMP}-2.json"))
        data = json.loads(first.read_text(encoding="utf-8"))
        [entry] = data["repositories"]
        self.assertEqual(entry["outcomes"][0]["ref"], f"refs/heads/{PREFIX}/wip-on-main")
        self.assertEqual(entry["skipped_large_files"], [{"path": "dump.sql", "bytes": 99}])
