import tempfile
import unittest
from pathlib import Path

from bento import ops
from bento.archive import Bento
from bento.gui import model
from bento.gui.model import FileEntry, ModuleEntry, Tree
from bento.manifest import Module
from bento.picks import ModulePicks, Picks, stored, with_picks

SECRET = b'{"api_key": "abcdefghijklmnopqrstuvwxyz123456"}\n'


class TreeTest(unittest.TestCase):
    def setUp(self):
        self.home = Path("/home/me")
        self.claude = Module("claude", self.home / ".claude", ("**",), ())
        self.git = Module("git", self.home / ".config" / "git", ("aliases",), ())
        self.tree = Tree(
            [
                ModuleEntry(
                    self.claude,
                    (
                        FileEntry("settings.json", model.UPDATED, b"x" * 10, b"y" * 12),
                        FileEntry("skills/demo/SKILL.md", model.ADDED, None, b"s" * 54),
                        FileEntry("skills/old/SKILL.md", model.UNCHANGED, b"old", b"old"),
                        FileEntry("CLAUDE.md", model.UNCHANGED, b"rules", b"rules"),
                    ),
                ),
                ModuleEntry(self.git, (FileEntry("aliases", model.ADDED, None, b"a"),), skipped=True),
            ],
            home=self.home,
        )

    def test_modules_then_folders_then_files(self):
        self.assertEqual(self.tree.roots, ["claude", "git"])
        self.assertEqual(
            self.tree.children("claude", show_unchanged=True),
            ["claude/skills/", "claude/CLAUDE.md", "claude/settings.json"],
        )
        self.assertEqual(
            self.tree.children("claude/skills/", show_unchanged=True),
            ["claude/skills/demo/", "claude/skills/old/"],
        )
        file = self.tree.node("claude/skills/demo/SKILL.md")
        self.assertEqual((file.kind, file.name, file.path), (model.FILE, "SKILL.md", "skills/demo/SKILL.md"))
        self.assertEqual(file.parent, "claude/skills/demo/")

    def test_unchanged_files_are_hidden_unless_asked_for(self):
        self.assertEqual(self.tree.children("claude"), ["claude/skills/", "claude/settings.json"])
        self.assertEqual(self.tree.children("claude/skills/"), ["claude/skills/demo/"])
        self.assertFalse(self.tree.visible("claude/skills/old/"))
        self.assertTrue(self.tree.visible("claude/skills/old/", show_unchanged=True))

    def test_modules_show_their_target_and_number_of_changes(self):
        self.assertEqual(self.tree.label("claude"), f"claude  ·  {Path('~') / '.claude'}")
        self.assertEqual(self.tree.status("claude"), "2 changes")
        self.assertEqual(self.tree.status("claude/skills/"), "1 change")
        self.assertEqual(self.tree.status("claude/skills/old/"), "")
        self.assertEqual(self.tree.status("claude/settings.json"), "updated")
        self.assertEqual(self.tree.total_changes(), 2)

    def test_a_module_without_changes(self):
        tree = Tree([ModuleEntry(self.claude, (FileEntry("CLAUDE.md", model.UNCHANGED, b"1", b"1"),))])
        self.assertEqual(tree.status("claude"), "no changes")
        self.assertEqual(tree.children("claude"), [])

    def test_a_skipped_module_is_greyed_out_and_changes_nothing(self):
        self.assertEqual(self.tree.status("git"), "skipped")
        self.assertEqual(self.tree.status("git/aliases"), "skipped")
        self.assertEqual(self.tree.changes("git"), 0)
        self.assertTrue(self.tree.muted("git"))
        self.assertTrue(self.tree.muted("git/aliases"))
        self.assertEqual(self.tree.children("git"), ["git/aliases"])
        self.assertIsNone(self.tree.check("git"))
        self.assertIn(
            ("State", "skipped: the target directory does not exist on this machine"),
            self.tree.details("git"),
        )

    def test_details_of_a_file_show_its_state_and_both_sizes(self):
        self.assertEqual(
            self.tree.details("claude/settings.json"),
            [
                ("File", "settings.json"),
                ("Module", "claude"),
                ("State", "updated"),
                ("On this machine", "10 bytes"),
                ("In the bento", "12 bytes"),
            ],
        )
        details = dict(self.tree.details("claude/skills/demo/SKILL.md"))
        self.assertEqual((details["On this machine"], details["In the bento"]), ("not there", "54 bytes"))

    def test_details_of_a_module_and_a_folder(self):
        self.assertEqual(
            self.tree.details("claude"),
            [
                ("Module", "claude"),
                ("Target", str(self.claude.target)),
                ("Changes", "1 added, 1 updated, 2 unchanged"),
            ],
        )
        self.assertEqual(self.tree.details("claude/skills/")[0], ("Folder", "skills/"))

    def test_size_text(self):
        self.assertEqual(model.size_text(1), "1 byte")
        self.assertEqual(model.size_text(1023), "1023 bytes")
        self.assertEqual(model.size_text(1536), "1.5 KB")
        self.assertEqual(model.size_text(3 * 1024 * 1024), "3.0 MB")

    # Ticks

    def test_everything_starts_ticked_in_deploy(self):
        self.assertEqual(self.tree.check("claude"), model.ON)
        self.assertEqual(set(self.tree.ticks), {
            "claude/settings.json", "claude/skills/demo/SKILL.md", "claude/skills/old/SKILL.md",
            "claude/CLAUDE.md",
        })

    def test_ticks_propagate_and_parents_show_partly_ticked(self):
        self.tree.toggle("claude/skills/")
        self.assertEqual(self.tree.check("claude/skills/"), model.OFF)
        self.assertEqual(self.tree.check("claude/skills/demo/"), model.OFF)
        self.assertEqual(self.tree.check("claude"), model.PARTIAL)
        self.tree.toggle("claude/skills/demo/SKILL.md")
        self.assertEqual(self.tree.check("claude/skills/"), model.PARTIAL)
        self.tree.toggle("claude")  # partly ticked: ticks everything
        self.assertEqual(self.tree.check("claude"), model.ON)
        self.tree.toggle("claude")
        self.assertEqual(self.tree.check("claude"), model.OFF)

    def test_unticking_in_deploy_leaves_the_file_out_for_this_run(self):
        self.tree.toggle("claude/settings.json")
        self.assertEqual(self.tree.state("claude/settings.json"), model.LEFT_OUT)
        self.assertEqual(self.tree.total_changes(), 1)
        self.assertEqual(self.tree.skips(), {"claude": {"settings.json"}})
        self.tree.toggle("claude/CLAUDE.md")
        self.assertEqual(self.tree.state("claude/CLAUDE.md"), model.UNCHANGED)

    def test_carry_keeps_only_the_ticks_the_user_changed(self):
        self.tree.toggle("claude/settings.json")
        fresh = Tree([ModuleEntry(self.claude, (
            FileEntry("settings.json", model.UPDATED, b"x", b"y"),
            FileEntry("new.md", model.ADDED, None, b"n"),
        ))])
        fresh.carry(self.tree)
        self.assertEqual(fresh.ticks, {"claude/settings.json": False, "claude/new.md": True})

    # Deploy

    def test_deploy_asks_only_before_overwriting(self):
        confirmation = self.tree.confirm_deploy()
        self.assertEqual(confirmation.sections, (("Overwritten", ("claude/settings.json",)),))
        self.tree.toggle("claude/settings.json")
        self.assertIsNone(self.tree.confirm_deploy())

    def test_stale_lists_ticked_files_that_changed(self):
        fresh = Tree([
            ModuleEntry(self.claude, (
                FileEntry("settings.json", model.UPDATED, b"edited meanwhile", b"y" * 12),
                FileEntry("skills/demo/SKILL.md", model.ADDED, None, b"s" * 54),
                FileEntry("skills/old/SKILL.md", model.UNCHANGED, b"old", b"old"),
                FileEntry("CLAUDE.md", model.UNCHANGED, b"rules", b"rules"),
            )),
            ModuleEntry(self.git, (FileEntry("aliases", model.ADDED, None, b"a"),), skipped=True),
        ])
        self.assertEqual(self.tree.stale(fresh), ["claude/settings.json"])
        self.tree.toggle("claude/settings.json")
        self.assertEqual(self.tree.stale(fresh), [])


class MachineCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "bento.tar.gz"
        self.claude = Module("claude", self.dir / "claude", ("*.md", "*.json", "skills/**"), ())
        self.git = Module("git", self.dir / "git", ("aliases",), ())
        self.modules = [self.claude, self.git]
        self.write(self.claude, "CLAUDE.md", b"old rules")
        self.write(self.claude, "same.md", b"same")

    def write(self, module, path, data):
        file = module.target / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)


class DeployTreeTest(MachineCase):
    def tree(self, files, meta=None):
        return model.deploy_tree(Bento(files, meta=meta or {}), self.modules, self.dir)

    def test_states_against_this_machine(self):
        tree = self.tree({
            "claude/CLAUDE.md": b"new rules!",
            "claude/same.md": b"same",
            "claude/skills/demo/SKILL.md": b"skill",
        })
        self.assertEqual(tree.status("claude/CLAUDE.md"), model.UPDATED)
        self.assertEqual(tree.status("claude/same.md"), model.UNCHANGED)
        self.assertEqual(tree.status("claude/skills/demo/SKILL.md"), model.ADDED)
        self.assertEqual(tree.status("claude"), "2 changes")
        details = dict(tree.details("claude/CLAUDE.md"))
        self.assertEqual((details["On this machine"], details["In the bento"]), ("9 bytes", "10 bytes"))

    def test_a_module_whose_target_is_missing_is_skipped(self):
        tree = self.tree({"git/aliases": b"[alias]"})
        self.assertEqual(tree.status("git"), model.SKIPPED)
        self.assertEqual(tree.total_changes(), 0)

    def test_files_the_selection_excludes_do_not_appear(self):
        selection = Picks({"claude": ModulePicks(("skills/old/",), ("skills/old/keep.md",))})
        tree = self.tree(
            {
                "claude/skills/old/SKILL.md": b"retired",
                "claude/skills/old/keep.md": b"kept",
                "claude/skills/new/SKILL.md": b"new",
            },
            with_picks({}, selection),
        )
        files = sorted(node.path for node in tree.files("claude"))
        self.assertEqual(files, ["skills/new/SKILL.md", "skills/old/keep.md"])

    def test_files_the_manifest_does_not_manage_are_greyed_out(self):
        tree = self.tree({"claude/history.jsonl": b"{}"})
        self.assertEqual(tree.status("claude/history.jsonl"), model.NOT_MANAGED)
        self.assertTrue(tree.muted("claude/history.jsonl"))
        self.assertIsNone(tree.check("claude/history.jsonl"))
        self.assertEqual(tree.changes("claude"), 0)
        self.assertEqual(dict(tree.details("claude/history.jsonl"))["In the bento"], "2 bytes")

    def test_unticked_files_are_not_written_and_skipped_modules_neither(self):
        contents = Bento({
            "claude/CLAUDE.md": b"new rules",
            "claude/skills/demo/SKILL.md": b"skill",
            "git/aliases": b"[alias]",
        })
        tree = model.deploy_tree(contents, self.modules, self.dir)
        tree.toggle("claude/CLAUDE.md")
        applied = model.with_skips(ops.plan_deploy(contents, self.modules), tree.skips())
        ops.apply_deploy(applied)
        self.assertEqual((self.claude.target / "CLAUDE.md").read_bytes(), b"old rules")
        self.assertEqual((self.claude.target / "skills/demo/SKILL.md").read_bytes(), b"skill")
        self.assertFalse(self.git.target.exists())
        self.assertEqual(model.deploy_report(applied), [
            f"deploy claude -> {self.claude.target}",
            "  added    skills/demo/SKILL.md",
        ])


class CaptureTreeTest(MachineCase):
    def bento(self, files, selection=None):
        meta = with_picks({}, selection) if selection else {}
        return Bento(files, meta=meta)

    def tree(self, previous, **options):
        return model.capture_tree(self.path, previous, self.modules, home=self.dir, **options)

    def test_states_and_ticks_prefilled_from_the_stored_selection(self):
        self.write(self.claude, "skills/old/SKILL.md", b"old")
        self.write(self.claude, "skills/new/SKILL.md", b"new")
        previous = self.bento(
            {"claude/CLAUDE.md": b"bento rules", "claude/gone.md": b"elsewhere", "claude/x.log": b"?"},
            Picks({"claude": ModulePicks(("skills/old/",))}),
        )
        tree = self.tree(previous)
        self.assertEqual(tree.state("claude/CLAUDE.md"), model.UPDATED)
        self.assertEqual(tree.state("claude/same.md"), model.ADDED)
        self.assertEqual(tree.state("claude/skills/new/SKILL.md"), model.ADDED)
        self.assertEqual(tree.state("claude/skills/old/SKILL.md"), model.EXCLUDED)
        self.assertEqual(tree.state("claude/gone.md"), model.ONLY_IN_BENTO)
        self.assertTrue(tree.ticked("claude/gone.md"))
        self.assertEqual(tree.state("claude/x.log"), model.NOT_MANAGED)
        self.assertIsNone(tree.check("claude/x.log"))
        self.assertEqual(tree.check("claude/skills/"), model.PARTIAL)
        self.assertEqual(tree.picks(), stored(previous))

    def test_unticking_files_and_folders_becomes_the_stored_selection(self):
        self.write(self.claude, "skills/a/SKILL.md", b"a")
        self.write(self.claude, "skills/b/SKILL.md", b"b")
        self.write(self.claude, "skills/b/notes.md", b"n")
        tree = self.tree(None)
        tree.toggle("claude/skills/a/")
        tree.toggle("claude/skills/b/notes.md")
        tree.toggle("claude/same.md")
        self.assertEqual(
            tree.picks(),
            Picks({"claude": ModulePicks(("same.md", "skills/a/", "skills/b/notes.md"))}),
        )

    def test_re_ticking_inside_an_excluded_folder(self):
        for name in ("a.md", "b.md", "c.md"):
            self.write(self.claude, f"skills/old/{name}", b"x")
        previous = self.bento({}, Picks({"claude": ModulePicks(("skills/old/",))}))
        tree = self.tree(previous)
        self.assertEqual(tree.check("claude/skills/old/"), model.OFF)
        tree.toggle("claude/skills/old/b.md")
        self.assertEqual(tree.check("claude/skills/old/"), model.PARTIAL)
        self.assertEqual(
            tree.picks(),
            Picks({"claude": ModulePicks(("skills/old/",), ("skills/old/b.md",))}),
        )
        # A folder that was not excluded stores its unticked files one by one.
        tree = self.tree(None)
        tree.toggle("claude/skills/old/")
        tree.toggle("claude/skills/old/b.md")
        self.assertEqual(
            tree.picks(),
            Picks({"claude": ModulePicks(("skills/old/a.md", "skills/old/c.md"))}),
        )

    def test_excludes_of_files_this_machine_and_bento_lack_are_kept(self):
        previous = self.bento({}, Picks({"claude": ModulePicks(("retired.md", "skills/gone/"))}))
        self.assertEqual(self.tree(previous).picks(), stored(previous))

    def test_unticking_an_only_in_bento_file_removes_it_and_stores_the_exclusion(self):
        previous = self.bento({"claude/gone.md": b"elsewhere", "claude/same.md": b"same"})
        tree = self.tree(previous)
        tree.toggle("claude/gone.md")
        self.assertEqual(tree.state("claude/gone.md"), model.REMOVED)
        plan = ops.plan_capture(self.path, previous, self.modules, selection=tree.picks())
        self.assertNotIn("claude/gone.md", plan.bento.files)
        self.assertEqual(stored(plan.bento).module("claude").exclude, ("gone.md",))
        confirmation = model.confirm_capture(plan)
        self.assertEqual(confirmation.sections[0], ("Leave the bento", ("claude/gone.md",)))

    def test_capture_that_only_adds_or_updates_runs_without_asking(self):
        tree = self.tree(self.bento({"claude/CLAUDE.md": b"bento rules"}))
        plan = ops.plan_capture(self.path, self.bento({"claude/CLAUDE.md": b"bento rules"}),
                                self.modules, selection=tree.picks())
        self.assertIsNone(model.confirm_capture(plan))

    def test_drop_unmanaged_entries_and_unknown_modules(self):
        previous = self.bento({"claude/x.log": b"?", "nvim/init.lua": b"x", "claude/same.md": b"same"})
        tree = self.tree(previous)
        self.assertEqual(tree.status("nvim"), model.NOT_MANAGED)
        self.assertTrue(tree.muted("nvim"))
        tree.drop_unmanaged = True
        self.assertEqual(tree.state("claude/x.log"), model.REMOVED)
        self.assertEqual(tree.status("nvim"), model.REMOVED)
        plan = ops.plan_capture(self.path, previous, self.modules, selection=tree.picks(),
                                drop_unmanaged=True)
        self.assertEqual(set(plan.bento.files), {"claude/same.md", "claude/CLAUDE.md"})
        leaving = dict(model.confirm_capture(plan).sections)["Leave the bento"]
        self.assertEqual(leaving, ("claude/x.log", "module nvim"))

    def test_guard_findings_refuse_a_file_until_it_is_allowed(self):
        self.write(self.claude, "settings.json", SECRET)
        tree = self.tree(None)
        id = "claude/settings.json"
        self.assertEqual(tree.state(id), model.REFUSED)
        self.assertTrue(tree.ticked(id))
        self.assertIn(("Line 1", "secret: " + tree.node(id).file.findings[0].excerpt), tree.details(id))
        plan = ops.plan_capture(self.path, None, self.modules, selection=tree.picks(),
                                allowed=frozenset(tree.allowed))
        self.assertNotIn(id, plan.bento.files)
        self.assertEqual(dict(model.confirm_capture(plan).sections)["Refused, not captured"], (id,))

        tree.allowed.add(id)
        self.assertEqual(tree.state(id), model.ADDED)
        self.assertEqual(tree.status(id), "added, allowed, high risk")
        plan = ops.plan_capture(self.path, None, self.modules, selection=tree.picks(),
                                allowed=frozenset(tree.allowed))
        self.assertIn(id, plan.bento.files)
        sections = dict(model.confirm_capture(plan).sections)
        self.assertEqual(sections["Captured despite guard findings"], (id,))
        self.assertNotIn("Refused, not captured", sections)

    def test_allowed_files_are_never_carried_to_a_fresh_tree(self):
        self.write(self.claude, "settings.json", SECRET)
        tree = self.tree(None)
        tree.allowed.add("claude/settings.json")
        fresh = self.tree(None)
        fresh.carry(tree)
        self.assertEqual(fresh.allowed, set())

    def test_a_secrets_module_in_a_plain_bento_is_refused_on_the_module(self):
        keys = Module("keys", self.dir / "keys", ("*",), (), secrets=True)
        self.write(keys, "id_ed25519", b"key")
        self.modules.append(keys)
        tree = self.tree(None)
        self.assertEqual(tree.status("keys"), model.REFUSED)
        self.assertIn("encrypt the bento", dict(tree.details("keys"))["State"])
        self.assertIsNone(tree.check("keys"))
        plan = ops.plan_capture(self.path, None, self.modules, selection=tree.picks())
        self.assertEqual(dict(model.confirm_capture(plan).sections)["Refused, not captured"],
                         ("module keys",))
        self.assertEqual(self.tree(None, encrypt=True).status("keys"), "1 change")

    def test_an_encryption_change_is_confirmed(self):
        plan = ops.plan_capture(self.path, None, self.modules, encrypt=True)
        self.assertIn("Encryption", dict(model.confirm_capture(plan).sections))

    def test_capture_report(self):
        previous = self.bento({"claude/gone.md": b"x"})
        tree = self.tree(previous)
        tree.toggle("claude/gone.md")
        plan = ops.plan_capture(self.path, previous, self.modules, selection=tree.picks())
        result = ops.CaptureResult(self.dir / "bento.20261027T000000Z.tar.gz")
        self.assertEqual(model.capture_report(plan, result, self.path), [
            f"bento written: {self.path}",
            "  previous kept as bento.20261027T000000Z.tar.gz",
            "  added    claude/CLAUDE.md",
            "  added    claude/same.md",
            "  removed  claude/gone.md",
        ])
        self.assertEqual(model.capture_report(plan, None, self.path)[0], "bento unchanged")


class DiffTest(unittest.TestCase):
    def tree(self, action, machine, bento, *, secrets=False, findings=()):
        module = Module("claude", Path("/nowhere"), ("**",), (), secrets)
        state = model.ADDED if bento is None and action == model.CAPTURE else model.UPDATED
        file = FileEntry("a.txt", state, machine, bento, findings)
        return Tree([ModuleEntry(module, (file,))], action)

    def test_plus_is_what_the_action_writes(self):
        deploy = self.tree(model.DEPLOY, b"here\n", b"bento\n").diff("claude/a.txt")
        self.assertIn(("-here", "remove", False), deploy.lines)
        self.assertIn(("+bento", "add", False), deploy.lines)
        capture = self.tree(model.CAPTURE, b"here\n", b"bento\n").diff("claude/a.txt")
        self.assertIn(("+here", "add", False), capture.lines)
        self.assertIn(("-bento", "remove", False), capture.lines)
        self.assertEqual(capture.lines[0][1], "header")

    def test_binary_files_show_both_sizes(self):
        diff = self.tree(model.DEPLOY, b"\xff\xfe", b"\xff").diff("claude/a.txt")
        self.assertEqual(diff.lines, ())
        self.assertEqual(diff.note, "binary, differs: 2 bytes in this machine, 1 byte in bento")

    def test_long_diffs_are_cut_off(self):
        old = "".join(f"{i}\n" for i in range(3000)).encode()
        diff = self.tree(model.DEPLOY, old, b"").diff("claude/a.txt")
        self.assertEqual(len(diff.lines), model.DIFF_LIMIT)
        self.assertIn("Cut off after 2,000 lines", diff.note)

    def test_secrets_stay_hidden_until_revealed(self):
        tree = self.tree(model.DEPLOY, b"a\n", b"b\n", secrets=True)
        self.assertTrue(tree.diff("claude/a.txt").hidden)
        self.assertEqual(tree.diff("claude/a.txt").lines, ())
        self.assertFalse(tree.diff("claude/a.txt", reveal=True).hidden)

    def test_finding_lines_are_highlighted(self):
        from bento.guards import Finding

        tree = self.tree(model.CAPTURE, b"one\ntwo\nthree\n", b"one\n",
                         findings=(Finding("secret", 3, "three"),))
        highlighted = [text for text, _, marked in tree.diff("claude/a.txt").lines if marked]
        self.assertEqual(highlighted, ["+three"])

    def test_unchanged_files_have_no_diff(self):
        module = Module("claude", Path("/nowhere"), ("**",), ())
        tree = Tree([ModuleEntry(module, (FileEntry("a", model.UNCHANGED, b"x", b"x"),))])
        self.assertIsNone(tree.diff("claude/a"))


class HighRiskCase(MachineCase):
    KEY = b"-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk\n-----END OPENSSH PRIVATE KEY-----\n"

    def setUp(self):
        super().setUp()
        self.ssh = Module("ssh", self.dir / "ssh", ("*",), (), secrets=True, plain=("config", "*.pub"))
        self.modules.append(self.ssh)
        self.write(self.ssh, "config", b"Host *\n")
        self.write(self.ssh, "work", self.KEY)
        self.write(self.ssh, "work.pub", b"ssh-ed25519 AAAA\n")


class HighRiskTest(HighRiskCase):
    def test_capture_marks_keys_and_unlisted_files(self):
        self.write(self.ssh, "token", b"opaque")
        tree = model.capture_tree(self.path, None, self.modules, encrypt=True, home=self.dir)
        self.assertEqual(tree.high_risk("ssh/work"), "private key")
        self.assertEqual(tree.high_risk("ssh/token"), "secrets module, not listed as plain")
        self.assertIsNone(tree.high_risk("ssh/config"))
        self.assertIsNone(tree.high_risk("ssh/work.pub"))
        self.assertEqual(tree.status("ssh/work"), "added, high risk")
        self.assertIn(("High risk", "private key"), tree.details("ssh/work"))

    def test_deploy_marks_keys_from_the_bento(self):
        contents = Bento({"ssh/config": b"Host *\n", "ssh/old": self.KEY})
        tree = model.deploy_tree(contents, self.modules, self.dir)
        self.assertEqual(tree.high_risk("ssh/old"), "private key")
        self.assertIsNone(tree.high_risk("ssh/config"))

    def test_the_diff_of_a_high_risk_file_never_shows(self):
        contents = Bento({"ssh/work": self.KEY + b"changed\n", "ssh/config": b"Host other\n"})
        tree = model.deploy_tree(contents, self.modules, self.dir)
        for reveal in (False, True):
            diff = tree.diff("ssh/work", reveal=reveal)
            self.assertEqual(diff.lines, ())
            self.assertFalse(diff.hidden)
            self.assertIn("High risk (private key), so no diff: differs", diff.note)
            self.assertNotIn("BEGIN", diff.note)
        # Show still works for the plain files of the module.
        self.assertTrue(tree.diff("ssh/config").hidden)
        self.assertIn(("+Host other", "add", False), tree.diff("ssh/config", reveal=True).lines)


class HighRiskConfirmationTest(HighRiskCase):
    def test_capture_asks_before_writing_a_key_into_the_bento(self):
        tree = model.capture_tree(self.path, None, self.modules, encrypt=True, home=self.dir)
        plan = ops.plan_capture(self.path, None, self.modules, selection=tree.picks(), encrypt=True)
        sections = dict(model.confirm_capture(plan).sections)
        self.assertEqual(sections["Secrets written to the bento"], ("ssh/work",))

        tree.toggle("ssh/work")
        plan = ops.plan_capture(self.path, None, self.modules, selection=tree.picks(), encrypt=True)
        self.assertNotIn("Secrets written to the bento", dict(model.confirm_capture(plan).sections))

    def test_a_capture_that_leaves_keys_alone_runs_directly(self):
        previous = Bento({"ssh/work": self.KEY, "ssh/config": b"Host *\n", "ssh/work.pub": b"ssh-ed25519 AAAA\n"},
                         encrypted=True)
        self.write(self.claude, "CLAUDE.md", b"new rules")
        plan = ops.plan_capture(self.path, previous, self.modules)
        self.assertIsNone(model.confirm_capture(plan))

    def test_deploy_asks_before_writing_a_key_even_when_it_only_adds(self):
        (self.ssh.target / "work").unlink()
        contents = Bento({"ssh/work": self.KEY, "claude/skills/new/SKILL.md": b"new"})
        tree = model.deploy_tree(contents, self.modules, self.dir)
        confirmation = tree.confirm_deploy()
        self.assertEqual(confirmation.sections, (("Secrets written to this machine", ("ssh/work",)),))
        self.assertIn("keys or credentials", confirmation.question)
        tree.toggle("ssh/work")
        self.assertIsNone(tree.confirm_deploy())


class EncryptionTest(MachineCase):
    def test_decrypt_is_refused_while_a_secrets_module_is_in_the_bento(self):
        keys = Module("keys", self.dir / "keys", ("*",), (), secrets=True)
        contents = Bento({"keys/id": b"k", "claude/CLAUDE.md": b"x"}, encrypted=True)
        refusal = model.decrypt_refusal(self.path, contents, [*self.modules, keys])
        self.assertIn("keys", refusal)
        self.assertIsNone(model.decrypt_refusal(self.path, contents, self.modules))

    def test_confirmations(self):
        plain, encrypted = Bento({}), Bento({}, encrypted=True)
        self.assertEqual(model.confirm_encryption(plain, True).action, "Encrypt")
        self.assertEqual(model.confirm_encryption(encrypted, True).action, "Change passphrase")
        self.assertEqual(model.confirm_encryption(encrypted, False).action, "Decrypt")


if __name__ == "__main__":
    unittest.main()
