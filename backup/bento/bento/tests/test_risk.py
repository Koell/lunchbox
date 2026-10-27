import unittest
from pathlib import Path

from bento import risk
from bento.archive import Bento
from bento.manifest import Module

KEY = b"-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n-----END OPENSSH PRIVATE KEY-----\n"
GPG_KEY = b"Created: 20261027T120000\nKey: (protected-key (ecc (curve Ed25519)))\n"
TOKEN = b"GITHUB_TOKEN=" + b"q7" * 12 + b"\n"


class ReasonTest(unittest.TestCase):
    def setUp(self):
        self.ssh = Module("ssh", Path("/ssh"), ("*",), (), secrets=True, plain=("config", "*.pub"))
        self.git = Module("git", Path("/git"), ("*",), ())

    def test_content_marks_files_in_any_module(self):
        self.assertEqual(risk.reason(self.git, "aliases", KEY), risk.PRIVATE_KEY)
        self.assertEqual(risk.reason(self.git, "aliases", GPG_KEY), risk.PRIVATE_KEY)
        self.assertEqual(risk.reason(self.git, "aliases", TOKEN), risk.SECRET_VALUE)
        self.assertIsNone(risk.reason(self.git, "aliases", b"[alias]\n"))

    def test_secrets_modules_mark_what_plain_does_not_cover(self):
        self.assertEqual(risk.reason(self.ssh, "work-key", b"opaque"), risk.UNLISTED)
        self.assertIsNone(risk.reason(self.ssh, "config", b"Host *\n"))
        self.assertIsNone(risk.reason(self.ssh, "work-key.pub", b"ssh-ed25519 AAAA\n"))

    def test_plain_never_clears_a_content_finding(self):
        self.assertEqual(risk.reason(self.ssh, "config", KEY), risk.PRIVATE_KEY)
        self.assertEqual(risk.reason(self.ssh, "key.pub", TOKEN), risk.SECRET_VALUE)

    def test_any_version_counts(self):
        self.assertEqual(risk.reason(self.git, "a", b"clean", KEY), risk.PRIVATE_KEY)
        self.assertEqual(risk.reason(self.git, "a", None, TOKEN), risk.SECRET_VALUE)
        self.assertIsNone(risk.reason(self.git, "a", None, None))
        self.assertEqual(risk.reason(self.ssh, "work-key", None), risk.UNLISTED)

    def test_reasons_across_sources(self):
        found = risk.reasons(
            self.ssh, {"config": b"Host *\n", "work-key": b"x"}, {"old-key": KEY, "config": b"Host *\n"}
        )
        self.assertEqual(found, {"old-key": risk.PRIVATE_KEY, "work-key": risk.UNLISTED})

    def test_count_in_a_bento(self):
        contents = Bento({
            "ssh/config": b"Host *\n",
            "ssh/work-key": KEY,
            "git/aliases": b"[alias]\n",
            "gone/token.env": TOKEN,
        })
        self.assertEqual(risk.count(contents, [self.ssh, self.git]), 2)

    def test_suffix(self):
        self.assertEqual(risk.suffix(risk.PRIVATE_KEY), "  (high risk: private key)")
        self.assertEqual(risk.suffix(None), "")


if __name__ == "__main__":
    unittest.main()
