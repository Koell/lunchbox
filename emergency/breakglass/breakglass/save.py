"""Saving what `scan` found: snapshots, branches and stashes pushed to the servers. No tkinter here.

Nothing local changes. The snapshot is built in a temporary copy of the index, so the working
tree, the index, the branches, the stashes and the upstream settings all stay as they were; the
only thing left behind locally are the snapshot's git objects, which git cleans up on its own.

Per repository:

- uncommitted changes become two commits on top of HEAD, like `git stash` does: one with the
  staged state, and on it one with the whole working tree including untracked files. That one is
  pushed to `emergency/<who>/<stamp>/wip-on-<branch>`;
- local branches on no remote get a normal push to their upstream (or their own name). When that
  is rejected, they go to `emergency/<who>/<stamp>/<branch>` instead. Never a force-push. A branch
  whose upstream was deleted on the server goes there straight away: that name stays deleted;
- stashes go to `emergency/<who>/<stamp>/stash-<n>`.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .proc import git
from .scan import Repo

WORKERS = 8
PUSH_TIMEOUT = 600
FALLBACK_IDENTITY = {
    "GIT_AUTHOR_NAME": "breakglass", "GIT_AUTHOR_EMAIL": "breakglass@localhost",
    "GIT_COMMITTER_NAME": "breakglass", "GIT_COMMITTER_EMAIL": "breakglass@localhost",
}


@dataclass
class Outcome:
    kind: str  # "snapshot", "branch" or "stash"
    source: str  # what was saved: the branch, "stash@{n}", or "uncommitted changes on <branch>"
    remote: str
    ref: str  # where it went, e.g. refs/heads/emergency/...
    ok: bool
    message: str = ""

    @property
    def emergency(self) -> bool:
        return self.ref.startswith("refs/heads/emergency/")


@dataclass
class Result:
    repo: str  # its path
    outcomes: list[Outcome] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and all(o.ok for o in self.outcomes)


def stamp(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d-%H%M")


def who(email: str | None) -> str:
    """The part of the email before the @, or the login name, made safe for a branch name."""
    name = email.split("@")[0] if email else getpass.getuser()
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-") or "someone"


def emergency_ref(repo: Repo, run_stamp: str, what: str) -> str:
    return f"refs/heads/emergency/{who(repo.email)}/{run_stamp}/{what}"


def save_all(repos: list[Repo], run_stamp: str, env: dict[str, str],
             started: Callable[[Repo], None], done: Callable[[Repo, Result], None]) -> list[Result]:
    """Save `repos` in parallel; the callbacks run on the worker threads."""
    def one(repo: Repo) -> Result:
        started(repo)
        try:
            result = save(repo, run_stamp, env)
        except Exception as error:  # reported per repository, not lost in the pool
            result = Result(str(repo.path), error=str(error))
        done(repo, result)
        return result

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return list(pool.map(one, repos))


def save(repo: Repo, run_stamp: str, env: dict[str, str]) -> Result:
    result = Result(str(repo.path))
    if repo.remote is None:
        result.error = "no remote to save to"
        return result

    if repo.changes:
        on = repo.head_branch or f"detached-{(repo.head_sha or 'none')[:7]}"
        source = f"uncommitted changes on {repo.head_branch or 'detached HEAD'}"
        try:
            sha = snapshot(repo)
        except RuntimeError as error:
            result.outcomes.append(Outcome("snapshot", source, repo.remote, "", False, str(error)))
        else:
            result.outcomes.append(
                push_new(repo, sha, emergency_ref(repo, run_stamp, f"wip-on-{on}"), "snapshot", source, env))

    for branch in repo.branches:
        if branch.gone:  # deleted on the server on purpose: keep the work, don't bring the name back
            outcome = push_new(repo, branch.sha, emergency_ref(repo, run_stamp, branch.name), "branch",
                               branch.name, env)
            if outcome.ok:
                outcome.message = "its branch on the server was deleted"
            result.outcomes.append(outcome)
            continue
        remote = branch.upstream_remote if branch.upstream_remote in repo.remotes else repo.remote
        ref = branch.upstream_ref or f"refs/heads/{branch.name}"
        ok, message = push(repo, remote, branch.sha, ref, env)
        if ok:
            result.outcomes.append(Outcome("branch", branch.name, remote, ref, True, message))
        else:
            fallback = push_new(repo, branch.sha, emergency_ref(repo, run_stamp, branch.name), "branch",
                                branch.name, env)
            fallback.message = f"normal push rejected ({message}); {fallback.message}".rstrip("; ")
            result.outcomes.append(fallback)

    for n, sha in enumerate(repo.stashes):
        result.outcomes.append(
            push_new(repo, sha, emergency_ref(repo, run_stamp, f"stash-{n}"), "stash", f"stash@{{{n}}}", env))
    return result


def snapshot(repo: Repo) -> str:
    """Commit the uncommitted changes without touching the index or the working tree: the staged
    state on HEAD, and the whole working tree (untracked files too, except `repo.large`) on that.
    Returns the second commit."""
    index = Path(git(repo.path, "rev-parse", "--git-path", "index").stdout.strip())
    index = index if index.is_absolute() else repo.path / index
    parents = ["-p", repo.head_sha] if repo.head_sha else []
    on = repo.head_branch or "detached HEAD"
    with tempfile.TemporaryDirectory(prefix="breakglass-") as tmp:
        copy = Path(tmp) / "index"
        if index.exists():
            shutil.copyfile(index, copy)
        env = dict(os.environ, GIT_INDEX_FILE=str(copy))
        staged = _commit(repo, _tree(repo, env), parents, f"breakglass: staged changes on {on}", env)
        exclude = [f":(exclude,literal){path}" for path, _ in repo.large]
        added = git(repo.path, "add", "--all", "--", ".", *exclude, env=env, timeout=600)
        if added.returncode != 0:
            raise RuntimeError(added.stderr.strip() or "git add failed")
        return _commit(repo, _tree(repo, env), parents + ["-p", staged],
                       f"breakglass: uncommitted changes on {on}", env)


def _tree(repo: Repo, env: dict[str, str]) -> str:
    proc = git(repo.path, "write-tree", env=env)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git write-tree failed")
    return proc.stdout.strip()


def _commit(repo: Repo, tree: str, parents: list[str], message: str, env: dict[str, str]) -> str:
    proc = git(repo.path, "commit-tree", tree, *parents, "-m", message, env=env)
    if proc.returncode != 0 and "tell me who you are" in proc.stderr.lower() + proc.stdout.lower():
        proc = git(repo.path, "commit-tree", tree, *parents, "-m", message, env=dict(env, **FALLBACK_IDENTITY))
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git commit-tree failed")
    return proc.stdout.strip()


def push(repo: Repo, remote: str, sha: str, ref: str, env: dict[str, str]) -> tuple[bool, str]:
    """Push `sha` to `ref` on `remote`; never forced."""
    proc = git(repo.path, "push", "--porcelain", remote, f"{sha}:{ref}", env=env, timeout=PUSH_TIMEOUT)
    if proc.returncode == 0:
        return True, ""
    return False, _reason(proc.stdout + "\n" + proc.stderr)


def push_new(repo: Repo, sha: str, ref: str, kind: str, source: str, env: dict[str, str]) -> Outcome:
    """Push to a new emergency branch on the repository's remote. If the name is taken (a second
    run in the same minute), the next free one: -2, -3, ..."""
    assert repo.remote is not None
    message = ""
    for attempt in range(1, 6):
        target = ref if attempt == 1 else f"{ref}-{attempt}"
        ok, message = push(repo, repo.remote, sha, target, env)
        if ok:
            return Outcome(kind, source, repo.remote, target, True)
        if "rejected" not in message and "already exists" not in message:
            break  # not a name clash: another name won't help
    return Outcome(kind, source, repo.remote, ref, False, message)


def _reason(output: str) -> str:
    """The line of git's push output that says why it failed."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for line in lines:
        if line.startswith("!"):
            return line.split("\t")[-1].strip() or line  # porcelain: "!\t<from>:<to>\t[rejected] (reason)"
    for line in lines:
        if "denied" in line.lower() or line.startswith(("fatal:", "error:")):
            return line
    return lines[0] if lines else "push failed"


def write_record(folder: Path, run_stamp: str, root: Path, results: list[Result], repos: list[Repo]) -> Path:
    """Keep what this run saved in `folder`/<stamp>.json, on this machine only."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{run_stamp}.json"
    n = 2
    while path.exists():
        path = folder / f"{run_stamp}-{n}.json"
        n += 1
    skipped = {str(r.path): [{"path": p, "bytes": size} for p, size in r.large] for r in repos if r.large}
    data = {
        "stamp": run_stamp,
        "folder": str(root),
        "repositories": [
            dict(asdict(result), skipped_large_files=skipped.get(result.repo, [])) for result in results
        ],
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path
