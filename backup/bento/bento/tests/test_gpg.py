import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bento import gpg


@unittest.skipUnless(gpg.available(), "gpg not installed")
class GpgHomeTest(unittest.TestCase):
    def test_home_is_a_path_of_this_system(self):
        home = gpg._home(shutil.which("gpg"))
        self.assertIsNotNone(home)
        self.assertTrue(home.is_absolute(), home)  # on Windows not Git's /c/Users/...
        self.assertTrue(home.parent.is_dir(), home)

    # Git for Windows' gpg wants GNUPGHOME in its own /c/... form; CI's Windows runner covers it
    # through the encryption tests, since it has no ~/.gnupg either.
    @unittest.skipIf(os.name == "nt", "GNUPGHOME takes an MSYS path with Git for Windows' gpg")
    def test_works_without_a_home_folder_and_creates_a_private_one(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name) / ".gnupg"  # a fresh machine, as on a CI runner
        with mock.patch.dict(os.environ, {"GNUPGHOME": str(home)}):
            gpgconf = shutil.which("gpgconf")
            if gpgconf:  # stop the agent gpg starts for this home
                self.addCleanup(subprocess.run, [gpgconf, "--kill", "gpg-agent"],
                                env=dict(os.environ), capture_output=True)
            data = gpg.encrypt(b"secret", "pw12")
            self.assertEqual(gpg.decrypt(data, "pw12"), b"secret")
        self.assertEqual(stat.S_IMODE(home.stat().st_mode), 0o700)
