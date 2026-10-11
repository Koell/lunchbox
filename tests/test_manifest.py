import tempfile
import unittest
from pathlib import Path

from lunchbox.manifest import MODULES_ROOT, ManifestError, discover, load_module

HOME = Path("/home/tester")


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def write_module(self, name, manifest):
        folder = self.root / name
        folder.mkdir()
        (folder / "manifest.toml").write_text(manifest, encoding="utf-8")
        return folder

    def module(self, target, env):
        folder = self.write_module("m", f'target = "{target}"\ninclude = ["*"]\n')
        return load_module(folder, env=env, home=HOME)

    def test_variable_set_wins_over_default(self):
        module = self.module("${CONF:-~/.conf}", {"CONF": "/opt/conf"})
        self.assertEqual(module.target, Path("/opt/conf"))

    def test_variable_unset_uses_default_with_tilde_expanded(self):
        module = self.module("${CONF:-~/.conf}", {})
        self.assertEqual(module.target, HOME / ".conf")

    def test_variable_empty_uses_default(self):
        module = self.module("${CONF:-~/.conf}", {"CONF": ""})
        self.assertEqual(module.target, HOME / ".conf")

    def test_tilde_alone_is_home(self):
        self.assertEqual(self.module("~", {}).target, HOME)

    def test_plain_path_is_kept(self):
        self.assertEqual(self.module("/etc/x", {}).target, Path("/etc/x"))

    def test_variable_value_is_tilde_expanded(self):
        module = self.module("${CONF:-/default}", {"CONF": "~/custom"})
        self.assertEqual(module.target, HOME / "custom")

    def test_loads_patterns_name_and_files_folder(self):
        folder = self.write_module(
            "claude",
            'target = "~/.claude"\ninclude = ["CLAUDE.md", "skills/**"]\nexclude = ["**/*.zip"]\n',
        )
        module = load_module(folder, env={}, home=HOME)
        self.assertEqual(module.name, "claude")
        self.assertEqual(module.include, ("CLAUDE.md", "skills/**"))
        self.assertEqual(module.exclude, ("**/*.zip",))
        self.assertEqual(module.files, folder / "files")

    def test_exclude_is_optional(self):
        module = self.module("~/x", {})
        self.assertEqual(module.exclude, ())

    def test_bad_manifests_name_the_module(self):
        cases = {
            "no_target": 'include = ["*"]',
            "empty_target": 'target = ""\ninclude = ["*"]',
            "target_not_string": 'target = 1\ninclude = ["*"]',
            "no_include": 'target = "~"',
            "empty_include": 'target = "~"\ninclude = []',
            "include_not_list": 'target = "~"\ninclude = "*"',
            "exclude_not_strings": 'target = "~"\ninclude = ["*"]\nexclude = [1]',
            "unknown_field": 'target = "~"\ninclude = ["*"]\nincludes = ["x"]',
            "not_toml": "target = ",
        }
        for name, manifest in cases.items():
            with self.subTest(name):
                folder = self.write_module(name, manifest)
                with self.assertRaisesRegex(ManifestError, f"module '{name}'"):
                    load_module(folder, env={}, home=HOME)

    def test_missing_manifest(self):
        (self.root / "empty").mkdir()
        with self.assertRaisesRegex(ManifestError, "module 'empty': missing manifest.toml"):
            load_module(self.root / "empty", env={}, home=HOME)

    def test_discover_loads_all_module_folders_sorted(self):
        self.write_module("zeta", 'target = "~/z"\ninclude = ["*"]')
        self.write_module("alpha", 'target = "~/a"\ninclude = ["*"]')
        (self.root / "README.md").write_text("not a module")
        (self.root / "__pycache__").mkdir()
        modules = discover(self.root, env={}, home=HOME)
        self.assertEqual([m.name for m in modules], ["alpha", "zeta"])
        self.assertEqual(modules[1].target, HOME / "z")

    def test_discover_rejects_folder_without_manifest(self):
        (self.root / "broken").mkdir()
        with self.assertRaisesRegex(ManifestError, "module 'broken'"):
            discover(self.root, env={}, home=HOME)

    def test_modules_root_is_located_next_to_the_package(self):
        self.assertEqual(MODULES_ROOT, Path(__file__).resolve().parent.parent / "modules")

    def test_shipped_claude_module(self):
        modules = {m.name: m for m in discover(env={"CLAUDE_CONFIG_DIR": ""}, home=HOME)}
        claude = modules["claude"]
        self.assertEqual(claude.target, HOME / ".claude")
        self.assertEqual(claude.include, ("CLAUDE.md", "settings.json", "skills/**", "custom/**"))
        self.assertEqual(claude.exclude, ("skills/synced/**", "skills/.trash/**", "**/*.zip"))

    def test_shipped_git_module(self):
        modules = {m.name: m for m in discover(env={"XDG_CONFIG_HOME": ""}, home=HOME)}
        git = modules["git"]
        self.assertEqual(git.target, HOME / ".config" / "git")
        self.assertEqual(git.include, ("aliases", "ignore"))
        self.assertEqual(git.exclude, ())


if __name__ == "__main__":
    unittest.main()
