"""Smoke tests of the Tk window; skipped without tkinter or a display (CI uses xvfb on Linux).

With BENTO_GUI_TESTS=required, as in CI, they run anyway and fail instead of being skipped.
"""

import gc
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from bento import archive, gpg
from bento.archive import Bento
from bento.gui.state import State
from bento.manifest import Manifest, Module, read

try:
    import tkinter

    tkinter.Tk().destroy()
    DISPLAY = True
except Exception:  # no tkinter, or no display
    DISPLAY = False
REQUIRED = os.environ.get("BENTO_GUI_TESTS") == "required"

T0 = datetime(2026, 10, 23, 14, 15, 2, tzinfo=timezone.utc)
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "machine"


def answers(*replies):
    """An ask_passphrase stand-in giving `replies` in order and recording the errors it was shown."""
    errors = []

    def ask(parent, bento, error=None):
        errors.append(error)
        return replies[len(errors) - 1]

    ask.errors = errors
    return ask


@unittest.skipUnless(DISPLAY or REQUIRED, "no tkinter or no display")
class AppTest(unittest.TestCase):
    def setUp(self):
        import tkinter as tk

        from bento.gui.app import App

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "export" / "bento.tar.gz"
        self.state = State(self.dir / "local" / "gui.json")
        self.default = self.dir / "default" / "bento.tar.gz"
        # A copy of the fixture machine, so a deploy can never write into the repository.
        self.machine = self.dir / "machine"
        shutil.copytree(FIXTURE, self.machine)
        self.modules = [
            Module("claude", self.machine / "claude", ("CLAUDE.md", "skills/**"), ()),
            Module("git", self.machine / "xdg" / "git", ("aliases", "ignore"), ()),
        ]
        self.confirmations = []
        self.App = App
        # Free this test's Tk objects on the main thread once it is done: collected later in a
        # worker thread, they call into Tk from there, which can stall that worker on Windows.
        self.addCleanup(gc.collect)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def app(self, requested=None, ask=None, *, agree=True, new_passphrase=None, manifest=None):
        """The window; confirmations are recorded and answered with `agree`. `manifest` reads
        the modules, by default `self.modules`."""

        def confirm(parent, confirmation):
            self.confirmations.append(confirmation)
            return agree

        options = {
            "state": self.state,
            "confirm": confirm,
            "ask_new_passphrase": lambda parent, bento: new_passphrase,
            # Never the real export/bento.tar.gz, which may exist and be encrypted.
            "default": self.default,
        }
        if ask is not None:
            options["ask_passphrase"] = ask
        return self.App(self.root, manifest or (lambda: Manifest(list(self.modules))), requested,
                        **options)

    def settle(self, app, timeout=20.0):
        """Run the event loop until the background work is done."""
        deadline = time.monotonic() + timeout
        self.root.update()
        while app.busy:
            if time.monotonic() > deadline:
                self.fail("the window did not finish its background work")
            self.root.update()
            time.sleep(0.01)
        self.root.update()

    def result_text(self, app):
        return app.result.get("1.0", "end-1c")

    def test_opens_a_plain_bento_and_remembers_it(self):
        archive.write(self.path, Bento({"claude/CLAUDE.md": b"rules", "git/aliases": b"x"}, T0))
        app = self.app(self.path)
        self.settle(app)
        self.assertEqual(app.path_var.get(), str(self.path))
        self.assertIn("not encrypted", app.info_var.get())
        self.assertIn("2 modules, 2 files", app.info_var.get())
        self.assertEqual(dict(app.contents.files)["claude/CLAUDE.md"], b"rules")
        self.assertEqual(self.state.remembered(), self.path)

    def test_the_bar_counts_high_risk_files(self):
        key = b"-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----\n"
        archive.write(self.path, Bento({"claude/CLAUDE.md": key, "git/aliases": b"x"}, T0))
        app = self.app(self.path)
        self.settle(app)
        self.assertIn("1 high-risk file", app.info_var.get())
        self.assertIn("high risk", app.deploy.view.item("claude/CLAUDE.md", "tags"))

    def test_the_window_is_dark(self):
        from tkinter import ttk

        from bento.gui import theme

        app = self.app(self.path)
        self.settle(app)
        self.assertEqual(ttk.Style(self.root).theme_use(), "clam")
        self.assertEqual(self.root.cget("background"), theme.BG)
        self.assertEqual(ttk.Style(self.root).lookup("TButton", "background"), theme.RAISED)
        self.assertEqual(app.result.cget("background"), theme.BG)

    def test_title_and_icon(self):
        from bento.gui import icon

        app = self.app(self.path)
        self.settle(app)
        self.assertEqual(self.root.title(), "Backup, Encrypt 'n' Take Out")
        sizes = [image.width() for image in app._icons]
        self.assertEqual(sizes, [16 * scale for scale in sorted(icon.SCALES, reverse=True)])
        self.assertTrue(all(len(row) == 16 and set(row) <= {".", *icon.COLORS} for row in icon.PIXELS))

    def test_a_missing_bento_waits_for_the_first_capture(self):
        app = self.app(self.path)
        self.settle(app)
        self.assertIsNone(app.contents)
        self.assertIn("not found", app.info_var.get())

    def test_reopens_the_remembered_bento(self):
        archive.write(self.path, Bento({"claude/CLAUDE.md": b"rules"}, T0))
        self.state.remember(self.path)
        app = self.app()
        self.settle(app)
        self.assertEqual(app.path, self.path)
        self.assertIsNotNone(app.contents)

    def test_a_gone_remembered_bento_falls_back_with_a_note(self):
        self.state.remember(self.dir / "gone.tar.gz")
        app = self.app()
        self.settle(app)
        self.assertEqual(app.path, self.default)
        self.assertIn("gone.tar.gz", self.result_text(app))

    def test_a_damaged_bento_is_reported(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"not a bento")
        app = self.app(self.path)
        self.settle(app)
        self.assertIn("can't be read", app.info_var.get())
        self.assertIn("not a readable .tar.gz", self.result_text(app))

    @unittest.skipUnless(gpg.available(), "gpg not installed")
    def test_encrypted_bento_asks_again_after_a_wrong_passphrase(self):
        archive.write(self.path, Bento({"claude/CLAUDE.md": b"rules"}, T0, True), passphrase="pw12")
        ask = answers("nope", "pw12")
        app = self.app(self.path, ask)
        self.settle(app)
        self.settle(app)
        self.assertEqual(ask.errors[0], None)
        self.assertIn("Wrong passphrase", ask.errors[1])
        self.assertTrue(app.contents.encrypted)
        self.assertIn("encrypted", app.info_var.get())
        self.assertEqual(app.passphrase, "pw12")

    @unittest.skipUnless(gpg.available(), "gpg not installed")
    def test_cancelled_passphrase_leaves_the_bento_locked_until_unlock(self):
        archive.write(self.path, Bento({"claude/CLAUDE.md": b"rules"}, T0, True), passphrase="pw12")
        ask = answers(None, "pw12")
        app = self.app(self.path, ask)
        self.settle(app)
        self.assertTrue(app.locked)
        self.assertIn("locked", app.info_var.get())
        self.assertTrue(app.unlock_button.grid_info())
        app.unlock()
        self.settle(app)
        self.assertFalse(app.locked)
        self.assertEqual(dict(app.contents.files), {"claude/CLAUDE.md": b"rules"})
        self.assertFalse(app.unlock_button.grid_info())

    def test_deploy_tab_shows_the_bento_against_this_machine(self):
        aliases = (self.machine / "xdg" / "git" / "aliases").read_bytes()
        archive.write(self.path, Bento({
            "claude/CLAUDE.md": b"other rules",
            "claude/skills/new/SKILL.md": b"new skill",
            "git/aliases": aliases,
        }, T0))
        app = self.app(self.path)
        self.settle(app)
        self.settle(app)
        rows = {id: state for id, _, state in app.deploy.rows()}
        self.assertEqual(rows, {
            "claude": "2 changes",
            "claude/skills/": "1 change",
            "claude/skills/new/": "1 change",
            "claude/skills/new/SKILL.md": "added",
            "claude/CLAUDE.md": "updated",
            "git": "no changes",
        })
        app.deploy.show_unchanged.set(True)
        app.deploy._render()
        self.assertIn(("git/aliases", "aliases", "unchanged"), app.deploy.rows())

        app.deploy.view.selection_set("claude/CLAUDE.md")
        self.root.update()
        shown = [w.cget("text") for w in app.deploy.info.winfo_children()]
        self.assertIn("updated", shown)
        self.assertIn("11 bytes", shown)
        self.assertIn("+other rules", app.deploy.diff_text())

    def test_refresh_rereads_the_bento_and_keeps_the_selection(self):
        archive.write(self.path, Bento({"claude/CLAUDE.md": b"other rules"}, T0))
        app = self.app(self.path)
        self.settle(app)
        self.settle(app)
        app.deploy.view.selection_set("claude/CLAUDE.md")
        old = archive.read(self.path)
        archive.write(self.path, Bento({**old.files, "claude/skills/x/SKILL.md": b"x"}, T0), old)
        app.refresh()
        self.settle(app)
        self.settle(app)
        self.assertIn("claude/skills/x/SKILL.md", [id for id, _, _ in app.deploy.rows()])
        self.assertEqual(app.deploy.selected, "claude/CLAUDE.md")

    def test_a_module_without_target_is_skipped_in_deploy(self):
        self.modules.append(Module("gone", self.dir / "nowhere", ("*",), ()))
        archive.write(self.path, Bento({"gone/file": b"x"}, T0))
        app = self.app(self.path)
        self.settle(app)
        self.settle(app)
        self.assertIn(("gone/file", "file", "skipped"), app.deploy.rows())
        self.assertIn("muted", app.deploy.view.item("gone", "tags"))

    # Deploy

    def test_deploy_writes_ticked_files_and_asks_before_overwriting(self):
        target = self.machine / "claude"
        archive.write(self.path, Bento({
            "claude/CLAUDE.md": b"bento rules",
            "claude/skills/new/SKILL.md": b"new skill",
            "claude/skills/other/SKILL.md": b"other skill",
        }, T0))
        app = self.app(self.path, agree=False)
        self.settle(app)
        app.deploy.toggle("claude/skills/other/")
        app.run_deploy()
        self.settle(app)
        self.assertEqual(self.confirmations[0].sections, (("Overwritten", ("claude/CLAUDE.md",)),))
        self.assertFalse((target / "skills/new").exists())

        self.confirmations.clear()
        app = self.app(self.path)
        self.settle(app)
        app.deploy.toggle("claude/skills/other/")
        app.run_deploy()
        self.settle(app)
        self.assertEqual(len(self.confirmations), 1)
        self.assertEqual((target / "CLAUDE.md").read_bytes(), b"bento rules")
        self.assertEqual((target / "skills/new/SKILL.md").read_bytes(), b"new skill")
        self.assertFalse((target / "skills/other").exists())
        self.assertIn("added    skills/new/SKILL.md", self.result_text(app))
        self.assertEqual(app.deploy.tree.check("claude"), "on")  # this run's untick is gone

    def test_an_add_only_deploy_does_not_ask(self):
        archive.write(self.path, Bento({"claude/skills/new/SKILL.md": b"new skill"}, T0))
        app = self.app(self.path, agree=False)
        self.settle(app)
        app.run_deploy()
        self.settle(app)
        self.assertEqual(self.confirmations, [])
        self.assertTrue((self.machine / "claude/skills/new/SKILL.md").is_file())

    def test_deploying_a_key_asks_first(self):
        key = b"-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----\n"
        target = self.dir / "fresh-ssh"
        target.mkdir()
        self.modules.append(Module("ssh", target, ("*",), (), secrets=True, plain=("*.pub",)))
        archive.write(self.path, Bento({"ssh/work": key, "ssh/work.pub": b"ssh-ed25519 A"}, T0))
        app = self.app(self.path, agree=False)
        self.settle(app)
        app.run_deploy()
        self.settle(app)
        self.assertEqual(self.confirmations[0].sections, (("Secrets written to this machine", ("ssh/work",)),))
        self.assertFalse((target / "work").exists())

    def test_deploy_stops_when_a_ticked_file_changed_meanwhile(self):
        archive.write(self.path, Bento({"claude/CLAUDE.md": b"bento rules"}, T0))
        app = self.app(self.path)
        self.settle(app)
        (self.machine / "claude/CLAUDE.md").write_bytes(b"edited meanwhile")
        app.run_deploy()
        self.settle(app)
        self.assertEqual((self.machine / "claude/CLAUDE.md").read_bytes(), b"edited meanwhile")
        self.assertIn("Deploy stopped", self.result_text(app))
        self.assertIn("claude/CLAUDE.md", self.result_text(app))

    # Capture

    def test_capture_leaves_out_an_unticked_file_and_stores_it(self):
        app = self.app(self.path)
        self.settle(app)
        app.capture.toggle("claude/skills/demo/SKILL.md")
        app.run_capture()
        self.settle(app)
        self.assertEqual(self.confirmations, [])  # nothing leaves a bento that doesn't exist yet
        contents = archive.read(self.path)
        self.assertIn("claude/CLAUDE.md", contents.files)
        self.assertNotIn("claude/skills/demo/SKILL.md", contents.files)
        self.assertIn("bento written", self.result_text(app))
        self.assertEqual(app.capture.tree.check("claude/skills/demo/SKILL.md"), "off")

        app.capture.toggle("claude/CLAUDE.md")
        app.run_capture()
        self.settle(app)
        self.assertEqual(self.confirmations[0].sections[0], ("Leave the bento", ("claude/CLAUDE.md",)))
        self.assertNotIn("claude/CLAUDE.md", archive.read(self.path).files)

    def test_capture_refuses_to_write_over_a_bento_that_changed_on_disk(self):
        archive.write(self.path, Bento({"claude/CLAUDE.md": b"old"}, T0))
        app = self.app(self.path)
        self.settle(app)
        app.capture.toggle("claude/skills/demo/SKILL.md")
        newer = Bento({"claude/CLAUDE.md": b"from a terminal"}, T0.replace(hour=15))
        archive.write(self.path, newer, archive.read(self.path))
        app.run_capture()
        self.settle(app)
        self.assertEqual(archive.read(self.path).files, newer.files)
        self.assertIn("changed on disk", self.result_text(app))
        self.assertEqual(app.capture.tree.check("claude/skills/demo/SKILL.md"), "off")

    def test_allowed_file_is_captured_for_one_run(self):
        secret = b'{"api_key": "abcdefghijklmnopqrstuvwxyz123456"}\n'
        (self.machine / "claude/skills/demo/SKILL.md").write_bytes(secret)
        app = self.app(self.path)
        self.settle(app)
        id = "claude/skills/demo/SKILL.md"
        self.assertEqual(app.capture.tree.state(id), "refused")
        app.capture.view.selection_set(id)
        self.root.update()
        self.assertTrue(app.capture.allow_button.grid_info())
        app.capture.allow.set(True)
        app.capture._allow_changed()
        app.run_capture()
        self.settle(app)
        sections = dict(self.confirmations[0].sections)
        self.assertEqual(sections["Captured despite guard findings"], (id,))
        self.assertIn(id, archive.read(self.path).files)
        self.assertEqual(app.capture.tree.allowed, set())

    # The manifest

    def manifest_file(self, text):
        """`bento.toml` in the temporary folder, with `{machine}` standing for the fixture copy."""
        path = self.dir / "bento.toml"
        path.write_text(text.replace("{machine}", self.machine.as_posix()), encoding="utf-8")
        return lambda: read(path, env={}, home=self.dir)

    def test_refresh_reads_an_edited_manifest(self):
        claude = '[modules.claude]\ntarget = "{machine}/claude"\ninclude = ["CLAUDE.md"]\n'
        load = self.manifest_file(claude)
        app = self.app(self.path, manifest=load)
        self.settle(app)
        self.assertEqual([m.name for m in app.modules], ["claude"])
        self.manifest_file(claude + '[modules.git]\ntarget = "{machine}/xdg/git"\ninclude = ["aliases"]\n')
        app.refresh()
        self.settle(app)
        self.assertEqual(app.capture.tree.roots, ["claude", "git"])

    def test_warnings_show_on_each_read_until_fixed(self):
        warned = '[modules.git]\ntarget = "{machine}/xdg/git"\ninclude = ["aliases"]\nplain = ["aliases"]\n'
        load = self.manifest_file(warned)
        app = self.app(self.path, manifest=load)
        self.settle(app)
        self.assertEqual(self.result_text(app).count("'plain' only applies"), 1)
        self.manifest_file(warned.replace('plain = ["aliases"]\n', ""))
        app.refresh()
        self.settle(app)
        self.assertEqual(self.result_text(app).count("'plain' only applies"), 1)

    def test_a_broken_manifest_holds_the_actions_until_fixed(self):
        good = '[modules.claude]\ntarget = "{machine}/claude"\ninclude = ["CLAUDE.md"]\n'
        load = self.manifest_file(good)
        app = self.app(self.path, manifest=load)
        self.settle(app)
        self.assertEqual(str(app.capture.action_button.cget("state")), "normal")
        self.manifest_file(good + "[modules.broken\n")
        app.refresh()
        self.settle(app)
        self.assertIn("invalid TOML", self.result_text(app))
        self.assertEqual([m.name for m in app.modules], ["claude"])
        self.assertEqual(str(app.capture.action_button.cget("state")), "disabled")
        self.assertEqual(str(app.capture.refresh_button.cget("state")), "normal")
        app.run_capture()
        self.settle(app)
        self.assertFalse(self.path.exists())
        self.manifest_file(good)
        app.refresh()
        self.settle(app)
        self.assertEqual(str(app.capture.action_button.cget("state")), "normal")

    # Encryption

    @unittest.skipUnless(gpg.available(), "gpg not installed")
    def test_encrypt_then_decrypt_keeps_the_selection(self):
        app = self.app(self.path, new_passphrase="pw12")
        self.settle(app)
        app.capture.toggle("claude/skills/demo/SKILL.md")
        app.run_capture()
        self.settle(app)
        app.encrypt()
        self.settle(app)
        self.assertTrue(archive.is_encrypted(self.path))
        self.assertEqual(app.passphrase, "pw12")
        self.assertEqual(self.confirmations[-1].action, "Encrypt")
        app.decrypt()
        self.settle(app)
        self.assertFalse(archive.is_encrypted(self.path))
        self.assertIsNone(app.passphrase)
        self.assertIn("selection.json", archive.read(self.path).meta)

    @unittest.skipUnless(gpg.available(), "gpg not installed")
    def test_encrypt_before_the_first_capture(self):
        app = self.app(self.path, new_passphrase="pw12")
        self.settle(app)
        app.encrypt()
        self.settle(app)
        self.assertEqual(app.pending, "pw12")
        app.run_capture()
        self.settle(app)
        self.assertIn("Encryption", dict(self.confirmations[-1].sections))
        self.assertTrue(archive.is_encrypted(self.path))
        self.assertEqual((app.passphrase, app.pending), ("pw12", None))

    def test_decrypt_is_refused_while_a_secrets_module_is_in_the_bento(self):
        self.modules.append(Module("keys", self.dir / "keys", ("*",), (), secrets=True))
        app = self.app(self.path)
        self.settle(app)
        app.contents = Bento({"keys/id": b"k"}, T0, encrypted=True)
        app.decrypt()
        self.assertIn("Decrypt refused", self.result_text(app))
        self.assertEqual(self.confirmations, [])

    def test_opening_another_bento_forgets_the_passphrase(self):
        archive.write(self.path, Bento({"claude/CLAUDE.md": b"rules"}, T0))
        app = self.app(self.path)
        self.settle(app)
        app.passphrase = "kept in memory"
        other = self.dir / "other.tar.gz"
        app.open(other)
        self.settle(app)
        self.assertIsNone(app.passphrase)
        self.assertEqual(self.state.remembered(), other)


if __name__ == "__main__":
    unittest.main()
