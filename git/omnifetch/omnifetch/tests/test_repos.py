import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from omnifetch import repos
from omnifetch.app import _folders

from .helpers import clone, git, origin, push_new_commit


class SshDestinationTest(unittest.TestCase):
    def test_scp_like(self):
        self.assertEqual(repos.ssh_destination("git@ssh.bitbucket.org:team/x.git"), ("git@ssh.bitbucket.org", None))

    def test_ssh_url_with_port(self):
        self.assertEqual(repos.ssh_destination("ssh://git@host:2222/x.git"), ("git@host", "2222"))

    def test_not_ssh(self):
        for url in ("https://github.com/a/b.git", "C:/repos/x", "/srv/x.git", "file:///srv/x.git"):
            with self.subTest(url=url):
                self.assertIsNone(repos.ssh_destination(url))

    def test_host_of(self):
        self.assertEqual(repos.host_of("git@github.com:a/b.git"), "github.com")
        self.assertEqual(repos.host_of("https://github.com/a/b.git"), "github.com")


class ErrorLineTest(unittest.TestCase):
    def test_prefers_the_explaining_line(self):
        output = "Fetching origin\ngit@x: Permission denied (publickey).\nfatal: Could not read"
        self.assertEqual(repos.error_line(output), "git@x: Permission denied (publickey).")

    def test_empty(self):
        self.assertEqual(repos.error_line(""), "no output")


class FoldersTest(unittest.TestCase):
    def test_nested_repositories_hang_below_their_folders(self):
        children = _folders(["a", "priv/b", "priv/deep/er/c"])
        self.assertEqual(children["."], ["a", "priv"])
        self.assertEqual(children["priv"], ["priv/b", "priv/deep"])
        self.assertEqual(children["priv/deep/er"], ["priv/deep/er/c"])


class RepositoryTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.bare = origin(self.dir)
        self.root = self.dir / "projects"
        clone(self.bare, self.root / "a")
        clone(self.bare, self.root / "priv" / "deep" / "b")
        clone(self.bare, self.root / "a" / "inner")  # inside a repository: not a project of its own
        clone(self.bare, self.root / "web" / "node_modules" / "pkg")  # skipped folder
        (self.root / "empty").mkdir()

    def test_scan_finds_repositories_at_any_depth(self):
        self.assertEqual([r.name for r in repos.scan(self.root)], ["a", "priv/deep/b"])

    def test_depth_limits_the_search(self):
        self.assertEqual([r.name for r in repos.scan(self.root, max_depth=1)], ["a"])

    def test_inspect_and_fetch(self):
        push_new_commit(self.bare, self.dir / "other")
        repo = repos.scan(self.root)[0]
        repos._inspect(repo)
        self.assertEqual(repo.branch, "main")
        self.assertEqual(repo.remotes, {"origin": str(self.bare)})
        self.assertEqual(repo.behind, 0)  # the new commit isn't fetched yet
        head = git(repo.path, "rev-parse", "HEAD")

        result = repos.fetch(repo, prune=False, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
        self.assertTrue(result.ok, result.output)
        self.assertEqual(repo.behind, 1)
        self.assertEqual(git(repo.path, "rev-parse", "HEAD"), head)  # only fetched

    def test_no_upstream(self):
        repo = repos.scan(self.root)[0]
        git(repo.path, "switch", "-q", "-c", "local-only")
        repos._inspect(repo)
        self.assertEqual((repo.branch, repo.behind), ("local-only", None))

    def test_failed_fetch(self):
        repo = repos.scan(self.root)[0]
        git(repo.path, "remote", "set-url", "origin", str(self.dir / "gone.git"))
        repos._inspect(repo)
        result = repos.fetch(repo, prune=False, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
        self.assertFalse(result.ok)


def _ssh_tools():
    try:
        return repos.find_ssh_tools()
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return None


@unittest.skipUnless(_ssh_tools(), "no ssh, ssh-agent and ssh-add")
class AgentTest(unittest.TestCase):
    def setUp(self):
        self.tools = _ssh_tools()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.key = Path(tmp.name) / "key"
        subprocess.run(
            [str(self.tools.keygen), "-q", "-t", "ed25519", "-N", "right", "-C", "test", "-f", str(self.key)],
            check=True,
        )

    def test_unlock_with_the_passphrase(self):
        agent = repos.Agent(self.tools)
        self.addCleanup(agent.close)
        self.assertEqual(agent.add(self.key, "wrong"), "Wrong passphrase.")
        self.assertIsNone(agent.add(self.key, "right"))
        listed = subprocess.run([str(self.tools.add), "-l"], env=dict(os.environ, **agent.env),
                                capture_output=True, text=True)
        self.assertIn("test (ED25519)", listed.stdout)

    def test_close_forgets_everything(self):
        agent = repos.Agent(self.tools)
        agent.add(self.key, "right")
        agent.close()
        self.assertFalse(agent.folder.exists())
        self.assertIsNotNone(agent._process.poll())

    def test_passphrase_is_never_written(self):
        agent = repos.Agent(self.tools)
        self.addCleanup(agent.close)
        agent.add(self.key, "right")
        for path in agent.folder.rglob("*"):
            if path.is_file():
                self.assertNotIn(b"right", path.read_bytes())

    def test_key_with_passphrase_is_detected(self):
        self.assertTrue(repos._has_passphrase(self.tools, self.key))
        plain = self.key.with_name("plain")
        subprocess.run([str(self.tools.keygen), "-q", "-t", "ed25519", "-N", "", "-f", str(plain)], check=True)
        self.assertFalse(repos._has_passphrase(self.tools, plain))


if shutil.which("git") is None:
    raise unittest.SkipTest("git is not installed")
