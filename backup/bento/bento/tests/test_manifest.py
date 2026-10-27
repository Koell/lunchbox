import tempfile
import unittest
from pathlib import Path

from bento.manifest import MANIFEST_PATH, ManifestError, load, read

HOME = Path("/home/tester")


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "bento.toml"

    def load(self, text, env=None):
        self.path.write_text(text, encoding="utf-8")
        return load(self.path, env={} if env is None else env, home=HOME)

    def target(self, target, env):
        return self.load(f'[modules.m]\ntarget = "{target}"\ninclude = ["*"]\n', env)[0].target

    def test_variable_set_wins_over_default(self):
        self.assertEqual(self.target("${CONF:-~/.conf}", {"CONF": "/opt/conf"}), Path("/opt/conf"))

    def test_variable_unset_uses_default_with_tilde_expanded(self):
        self.assertEqual(self.target("${CONF:-~/.conf}", {}), HOME / ".conf")

    def test_variable_empty_uses_default(self):
        self.assertEqual(self.target("${CONF:-~/.conf}", {"CONF": ""}), HOME / ".conf")

    def test_tilde_alone_is_home(self):
        self.assertEqual(self.target("~", {}), HOME)

    def test_plain_path_is_kept(self):
        self.assertEqual(self.target("/etc/x", {}), Path("/etc/x"))

    def test_variable_value_is_tilde_expanded(self):
        self.assertEqual(self.target("${CONF:-/default}", {"CONF": "~/custom"}), HOME / "custom")

    def test_loads_name_and_patterns(self):
        (module,) = self.load(
            '[modules.claude]\ntarget = "~/.claude"\n'
            'include = ["CLAUDE.md", "skills/**"]\nexclude = ["**/*.zip"]\n'
        )
        self.assertEqual(module.name, "claude")
        self.assertEqual(module.include, ("CLAUDE.md", "skills/**"))
        self.assertEqual(module.exclude, ("**/*.zip",))

    def test_exclude_is_optional(self):
        (module,) = self.load('[modules.m]\ntarget = "~/x"\ninclude = ["*"]\n')
        self.assertEqual(module.exclude, ())

    def test_secrets_flag(self):
        modules = self.load(
            '[modules.ssh]\ntarget = "~/.ssh"\ninclude = ["id_*"]\nsecrets = true\n'
            '[modules.git]\ntarget = "~/g"\ninclude = ["*"]\n'
        )
        self.assertEqual([m.secrets for m in modules], [True, False])
        with self.assertRaisesRegex(ManifestError, "module 'm': 'secrets' must be true or false"):
            self.load('[modules.m]\ntarget = "~"\ninclude = ["*"]\nsecrets = "yes"\n')

    def test_plain_in_a_secrets_module(self):
        self.path.write_text(
            '[modules.ssh]\ntarget = "~/.ssh"\ninclude = ["*"]\nsecrets = true\n'
            'plain = ["config", "*.pub", "nothing-matches"]\n',
            encoding="utf-8",
        )
        manifest = read(self.path, env={}, home=HOME)
        self.assertEqual(manifest.modules[0].plain, ("config", "*.pub", "nothing-matches"))
        self.assertEqual(manifest.warnings, [])

    def test_plain_without_secrets_is_a_warning_and_ignored(self):
        self.path.write_text(
            '[modules.git]\ntarget = "~/g"\ninclude = ["*"]\nplain = ["aliases"]\n', encoding="utf-8"
        )
        manifest = read(self.path, env={}, home=HOME)
        self.assertEqual(manifest.modules[0].plain, ())
        self.assertEqual(
            manifest.warnings, ["module 'git': 'plain' only applies with secrets = true; ignored"]
        )

    def test_plain_must_be_a_list_of_patterns(self):
        with self.assertRaisesRegex(ManifestError, "module 'm': 'plain' must be a list"):
            self.load('[modules.m]\ntarget = "~"\ninclude = ["*"]\nsecrets = true\nplain = "config"\n')

    def test_modules_keep_file_order(self):
        modules = self.load(
            '[modules.zeta]\ntarget = "~/z"\ninclude = ["*"]\n'
            '[modules.alpha]\ntarget = "~/a"\ninclude = ["*"]\n'
        )
        self.assertEqual([m.name for m in modules], ["zeta", "alpha"])
        self.assertEqual(modules[0].target, HOME / "z")

    def test_bad_modules_name_the_module(self):
        cases = {
            "no_target": 'include = ["*"]',
            "empty_target": 'target = ""\ninclude = ["*"]',
            "target_not_string": 'target = 1\ninclude = ["*"]',
            "no_include": 'target = "~"',
            "empty_include": 'target = "~"\ninclude = []',
            "include_not_list": 'target = "~"\ninclude = "*"',
            "exclude_not_strings": 'target = "~"\ninclude = ["*"]\nexclude = [1]',
            "unknown_field": 'target = "~"\ninclude = ["*"]\nincludes = ["x"]',
        }
        for name, body in cases.items():
            with self.subTest(name):
                with self.assertRaisesRegex(ManifestError, f"module '{name}'"):
                    self.load(f"[modules.{name}]\n{body}\n")

    def test_module_names_are_restricted(self):
        for name in ('"a b"', '"a/b"', '"a.b"', '"."', '""'):
            with self.subTest(name):
                with self.assertRaisesRegex(ManifestError, "name may only use"):
                    self.load(f'[modules.{name}]\ntarget = "~"\ninclude = ["*"]\n')

    def test_module_must_be_a_table(self):
        with self.assertRaisesRegex(ManifestError, "module 'm': must be a table"):
            self.load('[modules]\nm = "x"\n')

    def test_bad_files(self):
        cases = {
            "not_toml": ("target = ", "invalid TOML"),
            "no_modules": ("", "at least one"),
            "empty_modules": ("[modules]\n", "at least one"),
            "modules_not_table": ('modules = "x"\n', "at least one"),
            "unknown_top_level": (
                '[modules.m]\ntarget = "~"\ninclude = ["*"]\n[box]\npath = "x"\n',
                r"unknown field\(s\): box",
            ),
        }
        for name, (text, message) in cases.items():
            with self.subTest(name):
                with self.assertRaisesRegex(ManifestError, message):
                    self.load(text)

    def test_missing_manifest(self):
        with self.assertRaisesRegex(ManifestError, "manifest not found"):
            load(self.path, env={}, home=HOME)

    def test_manifest_is_located_at_the_project_root(self):
        self.assertEqual(MANIFEST_PATH, Path(__file__).resolve().parents[2] / "bento.toml")

    def test_shipped_claude_module(self):
        modules = {m.name: m for m in load(env={"CLAUDE_CONFIG_DIR": ""}, home=HOME)}
        claude = modules["claude"]
        self.assertEqual(claude.target, HOME / ".claude")
        self.assertEqual(claude.include, ("CLAUDE.md", "settings.json", "skills/**", "custom/**"))
        self.assertEqual(claude.exclude, ("skills/synced/**", "skills/.trash/**", "**/*.zip"))

    def test_shipped_git_module(self):
        modules = {m.name: m for m in load(env={"XDG_CONFIG_HOME": ""}, home=HOME)}
        git = modules["git"]
        self.assertEqual(git.target, HOME / ".config" / "git")
        self.assertEqual(git.include, ("aliases", "ignore", "config"))
        self.assertEqual(git.exclude, ())

    def test_shipped_manifest_loads_without_warnings(self):
        manifest = read(env={}, home=HOME)
        self.assertEqual(manifest.warnings, [])
        self.assertEqual(
            [m.name for m in manifest.modules],
            ["claude", "git", "ssh", "gnupg", "gradle", "pypi", "shell", "junie", "copilot", "codex",
             "gemini"],
        )

    def test_shipped_secrets_modules(self):
        modules = {m.name: m for m in load(env={}, home=HOME)}
        self.assertEqual([n for n, m in modules.items() if m.secrets], ["ssh", "gnupg", "gradle", "pypi"])
        ssh = modules["ssh"]
        self.assertEqual((ssh.target, ssh.include), (HOME / ".ssh", ("*",)))
        self.assertEqual(ssh.plain, ("config", "known_hosts", "*.pub"))
        self.assertEqual(modules["gnupg"].target, HOME / ".gnupg")
        self.assertEqual(load(env={"GNUPGHOME": "/g"}, home=HOME)[3].target, Path("/g"))


if __name__ == "__main__":
    unittest.main()
