import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bento import sync


class SyncTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "root"

    def write(self, path, data):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)

    def files(self):
        return {
            p.relative_to(self.root).as_posix(): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    def apply(self, source, **kwargs):
        plan = sync.plan(source, sync.read(self.root, source))
        sync.apply(plan, source, self.root, **kwargs)
        return plan

    def test_classifies_added_updated_unchanged(self):
        source = {"same.txt": b"source", "new.txt": b"source", "changed.txt": b"source"}
        plan = sync.plan(source, {"changed.txt": b"destination", "same.txt": b"source"})
        self.assertEqual(plan.added, ("new.txt",))
        self.assertEqual(plan.updated, ("changed.txt",))
        self.assertEqual(plan.unchanged, ("same.txt",))

    def test_destination_only_paths_are_not_part_of_the_plan(self):
        plan = sync.plan({"a.txt": b"a"}, {"a.txt": b"a", "extra.txt": b"x"})
        self.assertEqual((plan.added, plan.updated, plan.unchanged), ((), (), ("a.txt",)))

    def test_line_ending_difference_is_an_update(self):
        self.assertEqual(sync.plan({"a.txt": b"x\r\n"}, {"a.txt": b"x\n"}).updated, ("a.txt",))

    def test_read_returns_only_existing_files(self):
        self.write("a.txt", b"a")
        (self.root / "dir").mkdir()
        self.assertEqual(sync.read(self.root, ["a.txt", "missing.txt", "dir"]), {"a.txt": b"a"})

    def test_read_of_missing_root_is_empty(self):
        self.assertEqual(sync.read(self.root / "nope", ["a.txt"]), {})

    def test_writes_byte_exact_and_creates_parents_and_missing_root(self):
        content = {
            "crlf.ps1": b"Write-Host hi\r\n",
            "deep/nested/binary.bin": bytes(range(256)),
            "mixed.txt": b"a\nb\r\nc\r",
        }
        self.apply(content)
        self.assertEqual(self.files(), content)

    def test_never_deletes(self):
        self.write("a.txt", b"old")
        self.write("local-only.txt", b"keep me")
        self.write("skills/local/SKILL.md", b"keep me too")
        self.apply({"a.txt": b"new"})
        self.assertEqual(
            self.files(),
            {"a.txt": b"new", "local-only.txt": b"keep me", "skills/local/SKILL.md": b"keep me too"},
        )

    def test_unchanged_files_are_not_written(self):
        self.write("a.txt", b"same")
        os.utime(self.root / "a.txt", (1_000_000, 1_000_000))
        self.apply({"a.txt": b"same"})
        self.assertEqual((self.root / "a.txt").stat().st_mtime, 1_000_000)

    def test_second_apply_is_a_no_op(self):
        source = {"a.txt": b"a", "b/c.txt": b"c"}
        self.apply(source)
        again = self.apply(source)
        self.assertEqual((again.added, again.updated), ((), ()))
        self.assertEqual(again.unchanged, ("a.txt", "b/c.txt"))

    def test_without_drops_paths_from_changes(self):
        source = {"a.txt": b"a", "b.txt": b"b"}
        plan = sync.plan(source, {}).without(["a.txt"])
        sync.apply(plan, source, self.root)
        self.assertEqual(self.files(), {"b.txt": b"b"})

    @unittest.skipUnless(os.name == "posix", "executable bit is POSIX only")
    def test_shell_scripts_become_executable_on_request(self):
        self.write("old.sh", b"#!/bin/sh\n")
        (self.root / "old.sh").chmod(0o644)
        self.apply(
            {"run.sh": b"#!/bin/sh\n", "data.txt": b"x", "old.sh": b"#!/bin/sh\n"},
            executable_scripts=True,
        )
        for path in ("run.sh", "old.sh"):
            self.assertTrue((self.root / path).stat().st_mode & stat.S_IXUSR, path)
        self.assertFalse((self.root / "data.txt").stat().st_mode & stat.S_IXUSR)

    @unittest.skipUnless(os.name == "posix", "file modes are POSIX only")
    def test_private_files_and_their_directories_are_owner_only(self):
        self.write("old_key", b"old")
        (self.root / "old_key").chmod(0o644)
        self.write("same_key", b"same")
        (self.root / "same_key").chmod(0o644)
        self.root.chmod(0o755)
        self.apply(
            {"old_key": b"new", "same_key": b"same", "nested/dir/key": b"k", "run.sh": b"#!/bin/sh\n"},
            executable_scripts=True,
            private=True,
        )

        def mode(path):
            return stat.S_IMODE(path.stat().st_mode)

        for path in ("old_key", "same_key", "nested/dir/key"):
            self.assertEqual(mode(self.root / path), 0o600, path)
        self.assertEqual(mode(self.root / "run.sh"), 0o700)
        for directory in (self.root, self.root / "nested", self.root / "nested/dir"):
            self.assertEqual(mode(directory), 0o700, directory)

    @unittest.skipUnless(os.name == "posix", "file modes are POSIX only")
    def test_new_private_files_are_created_owner_only(self):
        created = []
        real_open = os.open

        def spy(path, flags, mode=0o777, *args, **kwargs):
            created.append(mode)
            return real_open(path, flags, mode, *args, **kwargs)

        with mock.patch("bento.sync.os.open", spy):
            self.apply({"key": b"k"}, private=True)
        self.assertEqual(created, [0o600])

    @unittest.skipUnless(os.name == "posix", "executable bit is POSIX only")
    def test_shell_scripts_keep_their_mode_by_default(self):
        self.write("run.sh", b"#!/bin/sh\n")
        (self.root / "run.sh").chmod(0o644)
        self.apply({"run.sh": b"#!/bin/sh\necho\n"})
        self.assertFalse((self.root / "run.sh").stat().st_mode & stat.S_IXUSR)


if __name__ == "__main__":
    unittest.main()
