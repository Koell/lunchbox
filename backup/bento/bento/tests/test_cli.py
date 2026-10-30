import contextlib
import io
import os
import stat
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from bento import archive, cli, gpg, picks
from bento.archive import Bento, BentoError
from bento.manifest import Manifest, Module
from bento.picks import ModulePicks, Picks
from bento.tests.test_archive import tar_bytes

T0 = datetime(2026, 10, 23, 14, 15, 2, tzinfo=timezone.utc)
SECRET = b'{"api_key": "abcdefghijklmnopqrstuvwxyz123456"}\n'


class CliTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "export" / "bento.tar.gz"
        self.claude = Module("claude", self.dir / "claude", ("*.md", "skills/**"), ("skills/synced/**",))
        self.git = Module("git", self.dir / "git", ("aliases",), ())
        self.modules = [self.claude, self.git]

    def write(self, module, path, data):
        file = module.target / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)

    def run_cli(self, function, *args, **kwargs):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = function(*args, **kwargs)
        return code, out.getvalue()

    def capture(self, modules=None, **kwargs):
        options = {"dry_run": False, "force": False, "prune": False, "known": self.modules}
        options.update(kwargs)
        return self.run_cli(cli.capture, modules or self.modules, self.path, **options)

    def packed(self):
        return dict(archive.read(self.path).files)

    def test_capture_creates_the_bento_from_managed_files_only(self):
        self.write(self.claude, "CLAUDE.md", b"rules")
        self.write(self.claude, "skills/a/SKILL.md", b"skill")
        self.write(self.claude, "skills/synced/x.md", b"synced")
        self.write(self.claude, "history.jsonl", b"runtime")
        self.write(self.git, "aliases", b"[alias]")
        code, out = self.capture()
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("not found", out)
        self.assertIn("bento written", out)
        self.assertEqual(
            self.packed(),
            {"claude/CLAUDE.md": b"rules", "claude/skills/a/SKILL.md": b"skill", "git/aliases": b"[alias]"},
        )

    def test_capture_merges_and_keeps_what_this_machine_lacks(self):
        archive.write(self.path, Bento({"claude/skills/other/SKILL.md": b"other", "git/aliases": b"old"},
                                T0))
        self.write(self.claude, "CLAUDE.md", b"rules")
        code, out = self.capture()
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("only in bento  skills/other/SKILL.md", out)
        self.assertIn("capture git", out)
        self.assertIn("skipped: target directory does not exist", out)
        self.assertEqual(
            self.packed(),
            {"claude/CLAUDE.md": b"rules", "claude/skills/other/SKILL.md": b"other", "git/aliases": b"old"},
        )
        self.assertEqual(len(archive.backups(self.path)), 1)

    def test_capture_prune_drops_entries_missing_here_and_unmanaged_ones(self):
        archive.write(self.path, Bento({
            "claude/skills/old/SKILL.md": b"old",
            "claude/unmanaged.txt": b"x",
            "claude/CLAUDE.md": b"rules",
            "git/aliases": b"kept, target missing",
            "retired/file": b"gone",
        }, T0))
        self.write(self.claude, "CLAUDE.md", b"rules")
        code, out = self.capture(prune=True)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("removed  skills/old/SKILL.md", out)
        self.assertIn("removed  unmanaged.txt", out)
        self.assertIn("removed  module retired", out)
        self.assertEqual(
            self.packed(), {"claude/CLAUDE.md": b"rules", "git/aliases": b"kept, target missing"}
        )

    def test_prune_of_selected_modules_leaves_unknown_modules_alone(self):
        archive.write(self.path, Bento({"retired/file": b"x", "claude/old.md": b"old"}, T0))
        self.write(self.claude, "CLAUDE.md", b"rules")
        self.capture([self.claude], prune=True, known=self.modules)
        self.assertEqual(self.packed(), {"retired/file": b"x", "claude/CLAUDE.md": b"rules"})

    def test_capture_refuses_flagged_files_and_keeps_their_packed_version(self):
        archive.write(self.path, Bento({"claude/settings.md": b"clean"}, T0))
        self.write(self.claude, "settings.md", SECRET)
        self.write(self.claude, "CLAUDE.md", b"rules")
        code, out = self.capture()
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("refused  settings.md:1  secret", out)
        self.assertNotIn("only in bento", out)
        self.assertEqual(self.packed(), {"claude/settings.md": b"clean", "claude/CLAUDE.md": b"rules"})

    def test_capture_force_takes_flagged_files(self):
        self.write(self.claude, "settings.md", SECRET)
        code, out = self.capture(force=True)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("warning  settings.md:1", out)
        self.assertEqual(self.packed(), {"claude/settings.md": SECRET})

    def test_a_forced_credential_is_marked_high_risk_everywhere(self):
        self.write(self.claude, "settings.md", SECRET)
        code, out = self.capture(force=True)
        self.assertIn("  added    settings.md  (high risk: secret value)\n", out)
        self.write(self.claude, "settings.md", b"clean now")
        _, out = self.run_cli(cli.status, self.modules, self.path, known=self.modules)
        self.assertRegex(out.splitlines()[0], r"\(captured [^)]*, 1 high-risk file\)$")
        self.assertIn("  updated  settings.md  (high risk: secret value)\n", out)

    def test_capture_without_changes_writes_nothing(self):
        self.write(self.claude, "CLAUDE.md", b"rules")
        self.capture()
        before = self.path.stat().st_mtime_ns
        code, out = self.capture()
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("bento unchanged", out)
        self.assertEqual(self.path.stat().st_mtime_ns, before)
        self.assertEqual(archive.backups(self.path), [])

    def test_damaged_label_is_reported_and_repaired_by_capture(self):
        self.path.parent.mkdir()
        contents = tar_bytes({"claude/CLAUDE.md": b"rules"})
        self.path.write_bytes(tar_bytes({"label.json": b"{broken", "contents.tar": contents}, "gz"))
        self.write(self.claude, "CLAUDE.md", b"rules")

        code, out = self.run_cli(cli.status, self.modules, self.path)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("(capture time unknown)", out)
        self.assertIn("warning  label.json is not valid JSON", out)
        self.assertIn("1 unchanged", out)

        code, out = self.capture()
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("bento written", out)
        repaired = archive.read(self.path)
        self.assertEqual((repaired.warnings, dict(repaired.files)), ((), {"claude/CLAUDE.md": b"rules"}))
        self.assertEqual(len(archive.backups(self.path)), 1)

    def test_capture_dry_run_writes_nothing(self):
        self.write(self.claude, "CLAUDE.md", b"rules")
        code, out = self.capture(dry_run=True)
        self.assertIn("added    CLAUDE.md", out)
        self.assertIn("bento would be written", out)
        self.assertFalse(self.path.exists())

    def test_deploy_round_trip_and_ignores_unmanaged_entries(self):
        self.write(self.claude, "CLAUDE.md", b"rules\r\n")
        self.write(self.claude, "skills/a/run.sh", b"#!/bin/sh\n")
        self.capture()
        fresh = Module("claude", self.dir / "fresh", self.claude.include, self.claude.exclude)
        contents = archive.read(self.path)
        archive.write(self.path, Bento({**contents.files, "claude/stray.txt": b"x"}, T0), contents)
        code, out = self.run_cli(cli.deploy, [fresh], self.path, dry_run=False)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("ignored  stray.txt", out)
        self.assertEqual((fresh.target / "CLAUDE.md").read_bytes(), b"rules\r\n")
        self.assertFalse((fresh.target / "stray.txt").exists())
        if os.name == "posix":
            self.assertTrue(os.access(fresh.target / "skills/a/run.sh", os.X_OK))
        code, out = self.run_cli(cli.deploy, [fresh], self.path, dry_run=False)
        self.assertNotIn("added", out)
        self.assertNotIn("updated", out)

    def test_deploy_mentions_unknown_modules_and_leaves_them_alone(self):
        archive.write(self.path, Bento({"retired/file": b"x", "git/aliases": b"[alias]"}, T0))
        code, out = self.run_cli(cli.deploy, self.modules, self.path, dry_run=False, known=self.modules)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("unknown module  retired", out)
        self.assertFalse((self.dir / "retired").exists())
        self.assertEqual((self.git.target / "aliases").read_bytes(), b"[alias]")

    def test_deploy_without_a_bento_is_an_error(self):
        with self.assertRaisesRegex(BentoError, "no bento at"):
            self.run_cli(cli.deploy, self.modules, self.path, dry_run=False)

    def test_status_reports_both_directions_and_writes_nothing(self):
        archive.write(self.path, Bento({"claude/CLAUDE.md": b"packed", "claude/gone.md": b"x", "old/f": b"y"},
                                T0))
        self.write(self.claude, "CLAUDE.md", b"local")
        self.write(self.claude, "new.md", b"new")
        code, out = self.run_cli(cli.status, self.modules, self.path, known=self.modules)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("captured 2026-10-23 14:15:02 UTC", out)
        self.assertIn("unknown module  old", out)
        self.assertIn("deploy claude", out)
        self.assertIn("added    gone.md", out)
        self.assertIn("added    new.md", out)
        self.assertIn("only in bento  gone.md", out)
        self.assertEqual(archive.backups(self.path), [])
        self.assertFalse((self.claude.target / "gone.md").exists())

    def test_status_without_a_bento(self):
        self.write(self.claude, "CLAUDE.md", b"local")
        code, out = self.run_cli(cli.status, self.modules, self.path)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("not found", out)
        self.assertIn("skipped: no bento", out)
        self.assertIn("added    CLAUDE.md", out)
        self.assertFalse(self.path.exists())

    def keep_out(self, *paths):
        """Store a selection keeping `paths` of the claude module out of the existing bento."""
        contents = archive.read(self.path)
        meta = picks.with_picks(contents.meta, Picks({"claude": ModulePicks(paths)}))
        archive.write(self.path, Bento(contents.files, T0, meta=meta), contents)

    def test_capture_honours_the_stored_selection(self):
        self.write(self.claude, "CLAUDE.md", b"rules")
        self.write(self.claude, "skills/old/SKILL.md", b"old")
        self.capture()
        self.keep_out("skills/old/")
        code, out = self.capture()
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("removed  skills/old/SKILL.md", out)
        self.assertIn("excluded by selection: 1", out)
        self.assertEqual(self.packed(), {"claude/CLAUDE.md": b"rules"})
        code, out = self.capture()
        self.assertIn("bento unchanged", out)
        self.assertEqual(self.packed(), {"claude/CLAUDE.md": b"rules"})

    def test_status_counts_files_excluded_by_selection(self):
        self.write(self.claude, "CLAUDE.md", b"rules")
        self.write(self.claude, "skills/a/SKILL.md", b"a")
        self.write(self.claude, "skills/b/SKILL.md", b"b")
        self.capture()
        self.keep_out("skills/")
        code, out = self.run_cli(cli.status, [self.claude], self.path)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("  excluded by selection: 2\n  1 unchanged", out)

    def test_no_selection_line_without_a_selection(self):
        self.write(self.claude, "CLAUDE.md", b"rules")
        self.capture()
        _, out = self.run_cli(cli.status, self.modules, self.path)
        self.assertNotIn("excluded by selection", out)

    def test_bento_path_defaults_and_resolves_relative_to_the_working_directory(self):
        self.assertEqual(cli._bento_path(None), archive.DEFAULT_PATH)
        self.assertEqual(cli._bento_path("b/x.tar.gz"), Path.cwd() / "b" / "x.tar.gz")
        self.assertEqual(cli._bento_path(str(self.path)), self.path)

    def test_main_prints_the_help_with_help(self):
        code, out = self.run_cli(cli.main, ["help"])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertRegex(out, r"deploy +copy configuration")
        self.assertIn("Without a command, open the window", out)

    def test_main_opens_the_window_without_a_command(self):
        for argv, requested in (([], None), (["--bento", str(self.path)], self.path)):
            with mock.patch.object(cli, "read", return_value=Manifest([])) as read, \
                    mock.patch("bento.gui.run", return_value=cli.EXIT_OK) as run:
                self.assertEqual(cli.main(argv), cli.EXIT_OK)
            run.assert_called_once_with(read, requested)

    def test_main_refuses_the_window_bento_before_a_command(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as exit:
            cli.main(["--bento", str(self.path), "deploy"])
        self.assertEqual(exit.exception.code, cli.EXIT_USAGE)
        self.assertIn("--bento goes after the command", err.getvalue())

    def test_gui_is_no_longer_a_command(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            cli.main(["gui"])
        self.assertIn("invalid choice: 'gui'", err.getvalue())

    def test_main_help_with_a_command_prints_that_commands_help(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as exit:
            cli.main(["help", "capture"])
        self.assertEqual(exit.exception.code, 0)
        self.assertIn("usage: bento capture", out.getvalue())

    def test_main_accepts_a_unique_prefix_of_a_command(self):
        for name in cli.COMMANDS[:-1]:
            for argv in ([name[:2], "-h"], [f"-{name[0]}", "-h"], ["h", name[:2]], ["-h", name]):
                out = io.StringIO()
                with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
                    cli.main(argv)
                self.assertIn(f"usage: bento {name}", out.getvalue(), argv)
        code, out = self.run_cli(cli.main, ["he"])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("usage: bento [-h]", out)

    def test_main_answers_a_bad_command_line_with_the_full_help(self):
        for argv, usage in (
            (["bogus"], "usage: bento [-h]"),
            (["help", "bogus"], "usage: bento [-h]"),
            (["deploy", "--bogus"], "usage: bento deploy"),
            (["-x"], "usage: bento [-h]"),
        ):
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as exit:
                cli.main(argv)
            self.assertEqual(exit.exception.code, cli.EXIT_USAGE, argv)
            self.assertIn(usage, err.getvalue())
            self.assertIn("options:", err.getvalue())
            self.assertIn("error:", err.getvalue())

    def test_main_reports_a_missing_bento_as_an_error(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(["deploy", "--bento", str(self.path)])
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("no bento at", err.getvalue())

    def test_main_prints_manifest_warnings_and_carries_on(self):
        manifest = Manifest([self.claude], ["module 'git': 'plain' only applies with secrets = true; ignored"])
        err, out = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "read", return_value=manifest), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            code = cli.main(["status", "--bento", str(self.path)])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(
            err.getvalue(),
            "bento: warning: module 'git': 'plain' only applies with secrets = true; ignored\n",
        )
        self.assertIn("capture claude", out.getvalue())


def answers(*replies):
    """A prompt that gives `replies` in order and records what it was asked."""
    asked = []

    def prompt(question):
        asked.append(question)
        return replies[len(asked) - 1]

    prompt.asked = asked
    return prompt


@unittest.skipUnless(gpg.available(), "gpg not installed")
class EncryptionTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "export" / "bento.tar.gz"
        self.claude = Module("claude", self.dir / "claude", ("*.md",), ())
        self.ssh = Module("ssh", self.dir / "ssh", ("config", "id_*"), (), secrets=True)
        self.modules = [self.claude, self.ssh]
        for module, path, data in (
            (self.claude, "CLAUDE.md", b"rules"),
            (self.ssh, "config", b"Host *\n"),
            (self.ssh, "id_ed25519", b"-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n"),
        ):
            module.target.mkdir(parents=True, exist_ok=True)
            (module.target / path).write_bytes(data)

    def run_cli(self, function, *args, **kwargs):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = function(*args, **kwargs)
        return code, out.getvalue()

    def capture(self, prompt, modules=None, **kwargs):
        modules = modules or self.modules
        options = {"dry_run": False, "force": False, "prune": False, "known": modules}
        options.update(kwargs)
        return self.run_cli(cli.capture, modules, self.path, prompt=prompt, **options)

    def test_secrets_module_is_refused_in_a_plain_bento(self):
        code, out = self.capture(answers())
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("refused  module holds secrets", out)
        self.assertEqual(dict(archive.read(self.path).files), {"claude/CLAUDE.md": b"rules"})

    def test_encrypt_takes_secrets_past_the_secret_guard(self):
        prompt = answers("pw12", "pw12")
        code, out = self.capture(prompt, encrypt=True)
        self.assertEqual(code, cli.EXIT_OK, out)
        self.assertIn("bento written", out)
        self.assertIn("(encrypted)", out)
        self.assertEqual(prompt.asked, ["New passphrase for the bento: ", "Repeat the passphrase: "])
        loaded = archive.read(self.path, lambda: "pw12")
        self.assertTrue(loaded.encrypted)
        self.assertIn("ssh/id_ed25519", loaded.files)

    def test_other_modules_stay_guarded_in_an_encrypted_bento(self):
        (self.claude.target / "CLAUDE.md").write_bytes(SECRET)
        code, out = self.capture(answers("pw12", "pw12"), encrypt=True)
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("refused  CLAUDE.md:1  secret", out)

    def test_encrypted_bento_stays_encrypted_and_asks_once(self):
        self.capture(answers("pw12", "pw12"), encrypt=True)
        (self.claude.target / "CLAUDE.md").write_bytes(b"new rules")
        prompt = answers("pw12")
        code, out = self.capture(prompt)
        self.assertEqual(code, cli.EXIT_OK, out)
        self.assertEqual(len(prompt.asked), 1)
        self.assertIn("encrypted", out.splitlines()[0])
        loaded = archive.read(self.path, lambda: "pw12")
        self.assertEqual((loaded.encrypted, loaded.files["claude/CLAUDE.md"]), (True, b"new rules"))

    def test_encrypt_again_changes_the_passphrase(self):
        self.capture(answers("pw12", "pw12"), encrypt=True)
        code, out = self.capture(answers("pw12", "new passphrase", "new passphrase"), encrypt=True)
        self.assertEqual(code, cli.EXIT_OK, out)
        self.assertIn("bento written", out)
        with self.assertRaisesRegex(BentoError, "wrong passphrase"):
            archive.read(self.path, lambda: "pw12")
        self.assertTrue(archive.read(self.path, lambda: "new passphrase").encrypted)

    def test_encrypt_deletes_plain_backups(self):
        first = Bento({"claude/CLAUDE.md": b"v1"}, T0)
        archive.write(self.path, first)
        archive.write(self.path, Bento({"claude/CLAUDE.md": b"v2"}, T0.replace(hour=15)), first)
        self.assertEqual(len(archive.backups(self.path)), 1)
        code, out = self.capture(answers("pw12", "pw12"), encrypt=True)
        self.assertEqual(code, cli.EXIT_OK, out)
        self.assertEqual(out.count("removed  plain backup"), 2)
        self.assertEqual(archive.backups(self.path), [])

    def test_decrypt_is_refused_while_secrets_are_packed(self):
        self.capture(answers("pw12", "pw12"), encrypt=True)
        with self.assertRaisesRegex(BentoError, "plain bento can't hold module.*ssh"):
            self.capture(answers("pw12"), decrypt=True)
        self.assertTrue(archive.read(self.path, lambda: "pw12").encrypted)

    def test_decrypt_without_secrets(self):
        self.capture(answers("pw12", "pw12"), [self.claude], encrypt=True)
        code, out = self.capture(answers("pw12"), [self.claude], decrypt=True)
        self.assertEqual(code, cli.EXIT_OK, out)
        self.assertFalse(archive.read(self.path).encrypted)
        backups = archive.backups(self.path)
        self.assertEqual(len(backups), 1)
        self.assertTrue(archive.is_encrypted(backups[0]))

    def test_new_passphrase_rules(self):
        for replies, message in ((("abc",), "at least 4"), (("pw12", "pw13"), "do not match")):
            with self.subTest(message):
                with self.assertRaisesRegex(cli.UsageError, message):
                    self.capture(answers(*replies), encrypt=True)
                self.assertFalse(self.path.exists())

    def test_encrypt_and_decrypt_exclude_each_other(self):
        with self.assertRaisesRegex(cli.UsageError, "exclude each other"):
            self.capture(answers(), encrypt=True, decrypt=True)

    def test_dry_run_encrypt_asks_nothing_and_writes_nothing(self):
        code, out = self.capture(answers(), encrypt=True, dry_run=True)
        self.assertIn("bento would be written (dry run), encrypted", out)
        self.assertFalse(self.path.exists())

    def test_wrong_passphrase_stops_before_writing(self):
        self.capture(answers("pw12", "pw12"), encrypt=True)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(BentoError, "wrong passphrase"):
            self.capture(answers("nope"))
        self.assertEqual(self.path.read_bytes(), before)

    def test_deploy_and_status_of_an_encrypted_bento(self):
        self.capture(answers("pw12", "pw12"), encrypt=True)
        fresh = Module("ssh", self.dir / "fresh-ssh", self.ssh.include, (), secrets=True)
        prompt = answers("pw12")
        code, out = self.run_cli(cli.deploy, [fresh], self.path, dry_run=False, prompt=prompt)
        self.assertEqual(code, cli.EXIT_OK, out)
        self.assertEqual(len(prompt.asked), 1)
        key = fresh.target / "id_ed25519"
        self.assertTrue(key.read_bytes().startswith(b"-----BEGIN OPENSSH"))
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(fresh.target.stat().st_mode), 0o700)
        code, out = self.run_cli(cli.status, self.modules, self.path, prompt=answers("pw12"))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("encrypted", out.splitlines()[0])

    def test_high_risk_files_are_marked_and_counted(self):
        self.ssh = Module("ssh", self.ssh.target, ("*",), (), secrets=True, plain=("config", "*.pub"))
        (self.ssh.target / "work-key").write_bytes(b"opaque")
        (self.ssh.target / "work-key.pub").write_bytes(b"ssh-ed25519 AAAA\n")
        modules = [self.claude, self.ssh]
        code, out = self.capture(answers("pw12", "pw12"), modules, encrypt=True)
        self.assertEqual(code, cli.EXIT_OK, out)
        self.assertIn("  added    id_ed25519  (high risk: private key)\n", out)
        self.assertIn("  added    work-key  (high risk: secrets module, not listed as plain)\n", out)
        self.assertIn("  added    work-key.pub\n", out)
        self.assertIn("  added    config\n", out)

        code, out = self.run_cli(cli.status, modules, self.path, known=modules, prompt=answers("pw12"))
        self.assertRegex(out.splitlines()[0], r", encrypted, 2 high-risk files\)$")


if __name__ == "__main__":
    unittest.main()
