import json
import unittest

from bento import picks
from bento.archive import Bento, BentoError
from bento.picks import ModulePicks, Picks


class ModulePicksTest(unittest.TestCase):
    def test_file_and_folder_excludes(self):
        selection = ModulePicks(exclude=("custom/x.sh", "skills/old/"))
        self.assertTrue(selection.excludes("custom/x.sh"))
        self.assertTrue(selection.excludes("skills/old/SKILL.md"))
        self.assertTrue(selection.excludes("skills/old/deep/new-later.md"))
        self.assertFalse(selection.excludes("custom/x.sh.bak"))
        self.assertFalse(selection.excludes("skills/older/SKILL.md"))
        self.assertFalse(selection.excludes("CLAUDE.md"))

    def test_include_brings_back_one_file_inside_an_excluded_folder(self):
        selection = ModulePicks(exclude=("skills/old/",), include=("skills/old/keep.md",))
        self.assertFalse(selection.excludes("skills/old/keep.md"))
        self.assertTrue(selection.excludes("skills/old/other.md"))


class StorageTest(unittest.TestCase):
    def test_round_trip_through_dump_and_parse(self):
        selection = Picks({"claude": ModulePicks(("skills/old/", "a.md"), ("skills/old/keep.md",))})
        parsed = picks.parse(picks.dump(selection))
        self.assertEqual(parsed.module("claude").exclude, ("a.md", "skills/old/"))
        self.assertEqual(parsed.module("claude").include, ("skills/old/keep.md",))

    def test_dump_has_the_documented_shape_and_drops_empty_modules(self):
        selection = Picks({"claude": ModulePicks(("a.md",)), "git": ModulePicks()})
        self.assertEqual(
            json.loads(picks.dump(selection)),
            {"version": 1, "modules": {"claude": {"exclude": ["a.md"], "include": []}}},
        )

    def test_a_bento_without_a_selection_keeps_everything(self):
        selection = picks.stored(Bento({"claude/a.md": b"x"}))
        self.assertFalse(selection)
        self.assertFalse(selection.excludes("claude", "a.md"))

    def test_with_picks_stores_or_removes_the_entry(self):
        stored = picks.with_picks({"other": b"1"}, Picks({"claude": ModulePicks(("a.md",))}))
        self.assertEqual(set(stored), {"other", picks.ENTRY})
        self.assertEqual(picks.with_picks(stored, Picks()), {"other": b"1"})

    def test_damaged_selection_refuses_the_bento(self):
        for raw in (
            b"{broken",
            b'{"version": 2, "modules": {}}',
            b'{"version": 1, "modules": {"claude": {"exclude": "a.md"}}}',
            b'{"version": 1, "modules": {"claude": {"exclude": ["../a.md"]}}}',
            b'{"version": 1, "modules": {"claude": {"exclude": ["/etc/passwd"]}}}',
            b'{"version": 1, "modules": {"claude": {"include": ["folder/"]}}}',
            b'{"version": 1, "modules": {"bad name": {"exclude": []}}}',
        ):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(BentoError, "stored selection is damaged"):
                    picks.stored(Bento(meta={picks.ENTRY: raw}))


if __name__ == "__main__":
    unittest.main()
