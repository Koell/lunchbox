import os
import stat
import tempfile
import unittest
from pathlib import Path

from lunchbox import sync


class SyncTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.source = Path(tmp.name) / "source"
        self.destination = Path(tmp.name) / "destination"
        self.source.mkdir()

    def write(self, root, path, data):
        file = root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)

    def files(self, root):
        return {
            p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()
        }

    def test_classifies_added_updated_unchanged(self):
        for path in ("new.txt", "changed.txt", "same.txt"):
            self.write(self.source, path, b"source")
        self.write(self.destination, "changed.txt", b"destination")
        self.write(self.destination, "same.txt", b"source")
        plan = sync.plan(self.source, self.destination, ["same.txt", "new.txt", "changed.txt"])
        self.assertEqual(plan.added, ("new.txt",))
        self.assertEqual(plan.updated, ("changed.txt",))
        self.assertEqual(plan.unchanged, ("same.txt",))

    def test_line_ending_difference_is_an_update(self):
        self.write(self.source, "a.txt", b"x\r\n")
        self.write(self.destination, "a.txt", b"x\n")
        self.assertEqual(sync.plan(self.source, self.destination, ["a.txt"]).updated, ("a.txt",))

    def test_copies_byte_exact_and_creates_parents_and_missing_destination(self):
        content = {
            "crlf.ps1": b"Write-Host hi\r\n",
            "deep/nested/binary.bin": bytes(range(256)),
            "mixed.txt": b"a\nb\r\nc\r",
        }
        for path, data in content.items():
            self.write(self.source, path, data)
        sync.apply(sync.plan(self.source, self.destination, content))
        self.assertEqual(self.files(self.destination), content)

    def test_never_deletes_at_destination(self):
        self.write(self.source, "a.txt", b"new")
        self.write(self.destination, "a.txt", b"old")
        self.write(self.destination, "local-only.txt", b"keep me")
        self.write(self.destination, "skills/local/SKILL.md", b"keep me too")
        sync.apply(sync.plan(self.source, self.destination, ["a.txt"]))
        self.assertEqual(
            self.files(self.destination),
            {"a.txt": b"new", "local-only.txt": b"keep me", "skills/local/SKILL.md": b"keep me too"},
        )

    def test_unchanged_files_are_not_written(self):
        self.write(self.source, "a.txt", b"same")
        self.write(self.destination, "a.txt", b"same")
        target = self.destination / "a.txt"
        os.utime(target, (1_000_000, 1_000_000))
        sync.apply(sync.plan(self.source, self.destination, ["a.txt"]))
        self.assertEqual(target.stat().st_mtime, 1_000_000)

    def test_second_apply_is_a_no_op(self):
        self.write(self.source, "a.txt", b"a")
        self.write(self.source, "b/c.txt", b"c")
        sync.apply(sync.plan(self.source, self.destination, ["a.txt", "b/c.txt"]))
        again = sync.plan(self.source, self.destination, ["a.txt", "b/c.txt"])
        self.assertEqual((again.added, again.updated), ((), ()))
        self.assertEqual(again.unchanged, ("a.txt", "b/c.txt"))

    def test_without_drops_paths_from_changes(self):
        self.write(self.source, "a.txt", b"a")
        self.write(self.source, "b.txt", b"b")
        plan = sync.plan(self.source, self.destination, ["a.txt", "b.txt"]).without(["a.txt"])
        sync.apply(plan)
        self.assertEqual(self.files(self.destination), {"b.txt": b"b"})

    @unittest.skipUnless(os.name == "posix", "executable bit is POSIX only")
    def test_shell_scripts_become_executable_on_request(self):
        self.write(self.source, "run.sh", b"#!/bin/sh\n")
        self.write(self.source, "data.txt", b"x")
        self.write(self.destination, "old.sh", b"#!/bin/sh\n")
        self.write(self.source, "old.sh", b"#!/bin/sh\n")
        (self.destination / "old.sh").chmod(0o644)
        plan = sync.plan(self.source, self.destination, ["run.sh", "data.txt", "old.sh"])
        sync.apply(plan, executable_scripts=True)
        for path in ("run.sh", "old.sh"):
            self.assertTrue((self.destination / path).stat().st_mode & stat.S_IXUSR, path)
        self.assertFalse((self.destination / "data.txt").stat().st_mode & stat.S_IXUSR)

    @unittest.skipUnless(os.name == "posix", "executable bit is POSIX only")
    def test_shell_scripts_keep_their_mode_by_default(self):
        self.write(self.source, "run.sh", b"#!/bin/sh\n")
        (self.source / "run.sh").chmod(0o644)
        sync.apply(sync.plan(self.source, self.destination, ["run.sh"]))
        self.assertFalse((self.destination / "run.sh").stat().st_mode & stat.S_IXUSR)


if __name__ == "__main__":
    unittest.main()
