import tempfile
import unittest
from pathlib import Path

from lunchbox.selection import matches, select

CLAUDE_INCLUDE = ("CLAUDE.md", "settings.json", "skills/**", "custom/**")
CLAUDE_EXCLUDE = ("skills/synced/**", "skills/.trash/**", "**/*.zip")


class MatchesTest(unittest.TestCase):
    def test_literal_pattern_matches_only_that_path(self):
        self.assertTrue(matches("CLAUDE.md", ["CLAUDE.md"]))
        self.assertFalse(matches("sub/CLAUDE.md", ["CLAUDE.md"]))
        self.assertFalse(matches("CLAUDE.md.bak", ["CLAUDE.md"]))

    def test_single_star_stays_within_a_segment(self):
        self.assertTrue(matches("a.json", ["*.json"]))
        self.assertFalse(matches("dir/a.json", ["*.json"]))

    def test_double_star_spans_directories(self):
        self.assertTrue(matches("skills/a/b/c.md", ["skills/**"]))
        self.assertTrue(matches("skills/c.md", ["skills/**"]))
        self.assertFalse(matches("skillset/c.md", ["skills/**"]))

    def test_leading_double_star_matches_at_any_depth_including_top(self):
        self.assertTrue(matches("x.zip", ["**/*.zip"]))
        self.assertTrue(matches("skills/a/x.zip", ["**/*.zip"]))
        self.assertFalse(matches("skills/a/x.zipped", ["**/*.zip"]))

    def test_question_mark_matches_one_character(self):
        self.assertTrue(matches("a1.txt", ["a?.txt"]))
        self.assertFalse(matches("a/.txt", ["a?.txt"]))

    def test_exclude_wins(self):
        self.assertFalse(matches("skills/synced/x.md", CLAUDE_INCLUDE, CLAUDE_EXCLUDE))
        self.assertFalse(matches("skills/commit.zip", CLAUDE_INCLUDE, CLAUDE_EXCLUDE))
        self.assertTrue(matches("skills/commit/SKILL.md", CLAUDE_INCLUDE, CLAUDE_EXCLUDE))

    def test_regex_characters_are_literal(self):
        self.assertTrue(matches("a+b (1).md", ["a+b (1).md"]))
        self.assertFalse(matches("aab (1).md", ["a+b (1).md"]))


class SelectTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def touch(self, *paths):
        for path in paths:
            file = self.root / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b"x")

    def test_selects_claude_configuration_and_nothing_else(self):
        self.touch(
            "CLAUDE.md",
            "settings.json",
            "settings.local.json",
            ".credentials.json",
            "history.jsonl",
            "projects/p/session.jsonl",
            "custom/statusline.sh",
            "skills/commit/SKILL.md",
            "skills/commit.zip",
            "skills/tdd/build/out.zip",
            "skills/synced/remote/SKILL.md",
            "skills/.trash/1791730088697-x/old-skill/SKILL.md",
            "skills/new-skill/deep/nested/ref.md",
        )
        self.assertEqual(
            select(self.root, CLAUDE_INCLUDE, CLAUDE_EXCLUDE),
            [
                "CLAUDE.md",
                "custom/statusline.sh",
                "settings.json",
                "skills/commit/SKILL.md",
                "skills/new-skill/deep/nested/ref.md",
            ],
        )

    def test_directories_named_like_literal_files_are_not_selected(self):
        self.touch("CLAUDE.md/inner.txt")
        self.assertEqual(select(self.root, ["CLAUDE.md"]), [])

    def test_nested_literal_include(self):
        self.touch("a/b/c.txt", "a/b/d.txt", "a/x.txt")
        self.assertEqual(select(self.root, ["a/b/c.txt"]), ["a/b/c.txt"])

    def test_missing_root_yields_nothing(self):
        self.assertEqual(select(self.root / "missing", CLAUDE_INCLUDE, CLAUDE_EXCLUDE), [])


if __name__ == "__main__":
    unittest.main()
