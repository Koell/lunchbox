import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from bento import archive, gpg, ops, picks
from bento.archive import Bento, BentoError
from bento.manifest import Module
from bento.picks import ModulePicks, Picks

T0 = datetime(2026, 10, 23, 14, 15, 2, tzinfo=timezone.utc)
SECRET = b'{"api_key": "abcdefghijklmnopqrstuvwxyz123456"}\n'


class MachineTestCase(unittest.TestCase):
    """A temporary machine with a `claude` and a `git` module, and a bento path next to it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "export" / "bento.tar.gz"
        self.claude = self.machine("claude", ("*.md", "skills/**"))
        self.git = self.machine("git", ("aliases",))
        self.modules = [self.claude, self.git]

    def machine(self, name, include, root="here", secrets=False):
        return Module(name, self.dir / root / name, include, (), secrets)

    def write(self, module, path, data):
        file = module.target / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)

    def capture(self, modules=None, **options):
        """Plan and write a capture of `modules`, returning the plan."""
        plan = ops.plan_capture(self.path, ops.load(self.path), modules or self.modules, **options)
        if plan.changed:
            ops.apply_capture(plan, self.path, now=T0)
        return plan

    def packed(self):
        return dict(archive.read(self.path).files)

    def module_result(self, plan, module):
        return next(m for m in plan.modules if m.module == module)


class OpsTest(MachineTestCase):
    # Capture

    def test_first_capture_writes_a_new_bento(self):
        self.write(self.claude, "CLAUDE.md", b"rules")
        plan = ops.plan_capture(self.path, None, self.modules)
        self.assertTrue(plan.changed)
        self.assertFalse(self.path.exists())
        result = ops.apply_capture(plan, self.path, now=T0)
        self.assertIsNone(result.backup)
        loaded = archive.read(self.path)
        self.assertEqual((dict(loaded.files), loaded.captured), ({"claude/CLAUDE.md": b"rules"}, T0))
        self.assertTrue(self.module_result(plan, self.git).skipped)

    def test_unchanged_capture_is_not_changed(self):
        self.write(self.claude, "CLAUDE.md", b"rules")
        self.capture()
        plan = ops.plan_capture(self.path, ops.load(self.path), self.modules)
        self.assertFalse(plan.changed)
        self.assertEqual(self.module_result(plan, self.claude).plan.unchanged, ("CLAUDE.md",))

    def test_merge_keeps_what_this_machine_lacks_and_prune_drops_it(self):
        archive.write(self.path, Bento({"claude/old.md": b"old"}, T0))
        self.write(self.claude, "CLAUDE.md", b"rules")
        plan = self.capture()
        self.assertEqual(self.module_result(plan, self.claude).only_in_bento, ("old.md",))
        self.assertIn("claude/old.md", self.packed())
        plan = self.capture(prune=True)
        self.assertEqual(self.module_result(plan, self.claude).removed, ("old.md",))
        self.assertEqual(self.packed(), {"claude/CLAUDE.md": b"rules"})

    def test_drop_unmanaged_keeps_what_this_machine_lacks(self):
        archive.write(self.path, Bento({
            "claude/old.md": b"old", "claude/x.log": b"?", "nvim/init.lua": b"x",
        }, T0))
        self.write(self.claude, "CLAUDE.md", b"rules")
        plan = self.capture(drop_unmanaged=True)
        self.assertEqual(self.module_result(plan, self.claude).removed, ("x.log",))
        self.assertEqual(self.module_result(plan, self.claude).only_in_bento, ("old.md",))
        self.assertTrue(plan.pruned)
        self.assertEqual(set(self.packed()), {"claude/CLAUDE.md", "claude/old.md"})

    def test_refused_files_are_dropped_from_the_plan(self):
        archive.write(self.path, Bento({"claude/a.md": b"clean"}, T0))
        self.write(self.claude, "a.md", SECRET)
        plan = self.capture()
        result = self.module_result(plan, self.claude)
        self.assertEqual(result.refused_files, ("a.md",))
        self.assertEqual(result.plan.changes, ())
        self.assertEqual(result.findings["a.md"][0].kind, "secret")
        self.assertTrue(plan.refused)
        self.assertEqual(self.packed(), {"claude/a.md": b"clean"})

    def test_allowed_paths_pass_one_refused_file_and_keep_the_other_refused(self):
        self.write(self.claude, "a.md", SECRET)
        self.write(self.claude, "b.md", SECRET)
        plan = self.capture(allowed={"claude/a.md"})
        result = self.module_result(plan, self.claude)
        self.assertEqual((result.allowed, result.refused_files), (frozenset({"a.md"}), ("b.md",)))
        self.assertTrue(plan.refused)
        self.assertEqual(self.packed(), {"claude/a.md": SECRET})

    def test_allow_all_takes_every_flagged_file(self):
        self.write(self.claude, "a.md", SECRET)
        self.write(self.claude, "b.md", SECRET)
        plan = self.capture(allowed=ops.ALL)
        self.assertFalse(plan.refused)
        self.assertEqual(set(self.packed()), {"claude/a.md", "claude/b.md"})

    def test_allowed_paths_are_never_stored(self):
        self.write(self.claude, "a.md", SECRET)
        self.capture(allowed={"claude/a.md"})
        self.assertEqual(dict(archive.read(self.path).meta), {})
        self.write(self.claude, "a.md", SECRET + b"more")
        plan = ops.plan_capture(self.path, ops.load(self.path), self.modules)
        self.assertEqual(self.module_result(plan, self.claude).refused_files, ("a.md",))

    def test_secrets_module_is_refused_in_a_plain_bento(self):
        ssh = self.machine("ssh", ("id_*",), secrets=True)
        self.write(ssh, "id_ed25519", b"key")
        plan = ops.plan_capture(self.path, None, [ssh])
        self.assertTrue(self.module_result(plan, ssh).refused)
        self.assertTrue(plan.refused)
        self.assertIsNone(plan.blocked)

    def test_a_plain_bento_holding_secrets_is_blocked(self):
        ssh = self.machine("ssh", ("id_*",), secrets=True)
        plan = ops.plan_capture(self.path, Bento({"ssh/id_ed25519": b"key"}, T0), [ssh])
        self.assertIn("can't hold module(s) with secrets: ssh", plan.blocked)
        with self.assertRaisesRegex(BentoError, "secrets"):
            ops.apply_capture(plan, self.path)
        self.assertFalse(self.path.exists())

    def test_encrypt_and_decrypt_exclude_each_other(self):
        with self.assertRaises(ValueError):
            ops.plan_capture(self.path, None, self.modules, encrypt=True, decrypt=True)

    # Deploy

    def test_deploy_writes_managed_files_and_lists_ignored_ones(self):
        archive.write(self.path, Bento({"claude/CLAUDE.md": b"rules", "claude/stray.txt": b"x"}, T0))
        fresh = self.machine("claude", self.claude.include, root="fresh")
        planned = ops.plan_deploy(archive.read(self.path), [fresh])
        self.assertEqual((planned[0].plan.added, planned[0].ignored), (("CLAUDE.md",), ("stray.txt",)))
        ops.apply_deploy(planned)
        self.assertEqual((fresh.target / "CLAUDE.md").read_bytes(), b"rules")
        self.assertFalse((fresh.target / "stray.txt").exists())
        again = ops.plan_deploy(archive.read(self.path), [fresh])
        self.assertEqual(again[0].plan.changes, ())

    def test_load_returns_none_without_a_bento(self):
        self.assertIsNone(ops.load(self.path))


class SelectionTest(MachineTestCase):
    """The stored selection: what it keeps out stays out of the bento and off every machine."""

    def keep_out(self, *paths, include=()):
        return Picks({"claude": ModulePicks(tuple(paths), tuple(include))})

    def test_excluded_file_is_removed_and_the_selection_stored(self):
        self.write(self.claude, "CLAUDE.md", b"rules")
        self.write(self.claude, "skills/old/SKILL.md", b"old")
        self.capture()
        plan = self.capture(selection=self.keep_out("skills/old/"))
        result = self.module_result(plan, self.claude)
        self.assertEqual((result.removed, result.excluded), (("skills/old/SKILL.md",),) * 2)
        self.assertEqual(self.packed(), {"claude/CLAUDE.md": b"rules"})
        stored = picks.stored(archive.read(self.path))
        self.assertEqual(stored.module("claude").exclude, ("skills/old/",))

    def test_stored_selection_keeps_files_out_on_later_captures(self):
        self.write(self.claude, "CLAUDE.md", b"rules")
        self.write(self.claude, "skills/old/SKILL.md", b"old")
        self.capture(selection=self.keep_out("skills/old/"))
        self.write(self.claude, "skills/old/later.md", b"new file in the excluded folder")
        self.write(self.claude, "skills/new/SKILL.md", b"new skill")
        plan = self.capture()
        result = self.module_result(plan, self.claude)
        self.assertEqual(result.excluded, ("skills/old/SKILL.md", "skills/old/later.md"))
        self.assertEqual(set(self.packed()), {"claude/CLAUDE.md", "claude/skills/new/SKILL.md"})

    def test_another_machine_that_still_has_the_file_does_not_bring_it_back(self):
        self.write(self.claude, "a.md", b"a")
        self.write(self.claude, "retired.md", b"old")
        self.capture()
        (self.claude.target / "retired.md").unlink()
        plan = self.capture(selection=self.keep_out("retired.md"))
        self.assertEqual(self.module_result(plan, self.claude).removed, ("retired.md",))
        other = self.machine("claude", self.claude.include, root="other")
        self.write(other, "retired.md", b"still here")
        self.capture([other])
        self.assertEqual(set(self.packed()), {"claude/a.md"})

    def test_include_brings_back_one_file_inside_an_excluded_folder(self):
        self.write(self.claude, "skills/x/keep.md", b"keep")
        self.write(self.claude, "skills/x/drop.md", b"drop")
        self.capture(selection=self.keep_out("skills/x/", include=("skills/x/keep.md",)))
        self.assertEqual(set(self.packed()), {"claude/skills/x/keep.md"})

    def test_selection_only_narrows_the_manifest(self):
        self.write(self.claude, "CLAUDE.md", b"rules")
        self.write(self.claude, "history.jsonl", b"not managed")
        plan = self.capture(selection=self.keep_out("history.jsonl"))
        self.assertEqual(self.module_result(plan, self.claude).excluded, ())
        self.assertEqual(self.packed(), {"claude/CLAUDE.md": b"rules"})

    def test_clearing_the_selection_removes_the_entry(self):
        self.write(self.claude, "a.md", b"a")
        self.capture(selection=self.keep_out("a.md"))
        self.assertIn(picks.ENTRY, archive.read(self.path).meta)
        plan = self.capture(selection=Picks())
        self.assertTrue(plan.changed)
        self.assertEqual((dict(archive.read(self.path).meta), self.packed()), ({}, {"claude/a.md": b"a"}))

    def test_deploy_never_writes_excluded_files(self):
        selection = picks.dump(self.keep_out("a.md"))
        contents = Bento({"claude/a.md": b"a", "claude/b.md": b"b"}, T0, meta={picks.ENTRY: selection})
        planned = ops.plan_deploy(contents, [self.claude])
        self.assertEqual((planned[0].plan.added, planned[0].ignored), (("b.md",), ()))
        ops.apply_deploy(planned)
        self.assertFalse((self.claude.target / "a.md").exists())

    def test_a_skipped_module_leaves_the_bento_untouched(self):
        contents = Bento({"claude/a.md": b"a"}, T0)
        plan = ops.plan_capture(self.path, contents, [self.claude], selection=self.keep_out("a.md"))
        self.assertTrue(self.module_result(plan, self.claude).skipped)
        self.assertEqual(dict(plan.bento.files), {"claude/a.md": b"a"})

    @unittest.skipUnless(gpg.available(), "gpg not installed")
    def test_selection_survives_encrypting_and_decrypting(self):
        self.write(self.claude, "a.md", b"a")
        self.write(self.claude, "b.md", b"b")
        self.capture(selection=self.keep_out("b.md"))
        plan = ops.plan_capture(self.path, ops.load(self.path), self.modules, encrypt=True)
        ops.apply_capture(plan, self.path, new_passphrase=lambda: "pw12", now=T0)
        sealed = archive.read(self.path, lambda: "pw12")
        self.assertEqual(picks.stored(sealed).module("claude").exclude, ("b.md",))
        plan = ops.plan_capture(self.path, sealed, self.modules, decrypt=True)
        ops.apply_capture(plan, self.path, now=T0)
        self.assertEqual(picks.stored(archive.read(self.path)).module("claude").exclude, ("b.md",))


if __name__ == "__main__":
    unittest.main()
