# breakglass

In case of fire, break glass: one click pushes every repository's unsaved work to its server, so
nothing is lost with the machine.

breakglass finds every git repository below a folder and saves what exists only on this machine:

| Unsaved                         | Where it goes                                                        |
|---------------------------------|----------------------------------------------------------------------|
| Uncommitted changes             | a new branch `emergency/<you>/<YYYY-MM-DD-HHMM>/wip-on-<branch>`     |
| Commits on no remote            | a normal push to the branch's upstream, or its own name if it has none |
| ... whose normal push is rejected | `emergency/<you>/<stamp>/<branch>` instead; never a force-push      |
| ... whose server branch was deleted | `emergency/<you>/<stamp>/<branch>` straight away: a deleted name is never brought back |
| Stashes                         | `emergency/<you>/<stamp>/stash-<n>`                                  |

`<you>` is the part of the repository's `user.email` before the @. A second run in the same minute
gets `-2`, `-3`, ... added.

**Nothing local changes.** The snapshot of the uncommitted changes is built in a temporary copy of
the index; the working tree, the index, branches, stashes and upstream settings stay exactly as they
were. Like `git stash`, the snapshot is two commits on top of `HEAD`: one with what was staged, and
on it one with the whole working tree.

What is left out:

- **Ignored files**, never: that is where `.env` files, local credentials, dependencies and build
  output live.
- **Untracked files larger than 50 MB**: they would make the push fail or take forever. They are
  listed in the window, the output and the record.

## Prerequisites

- Python 3.11 or newer. Nothing else: no `pip install`, no virtualenv.
- Git. On Windows, Git for Windows: breakglass uses its ssh, ssh-agent and ssh-add.
- Tk for the window. It ships with Python on Windows and macOS; on Linux install `tk` (Manjaro,
  Arch) or `python3-tk` (Debian, Ubuntu). `save` works without it.

## Usage

From this folder, `emergency/breakglass` in the lunchbox repository:

```sh
python -m breakglass                       # the window
python -m breakglass save                  # save everything now, asking nothing but passphrases
python -m breakglass save --dry-run        # only show what would be saved
python -m breakglass install-shortcut      # a "Break glass" icon on the desktop that opens the window
```

| Option        | What it does                                                                      |
|---------------|-----------------------------------------------------------------------------------|
| `--path DIR`  | Folder to look for repositories in; wins over the remembered one and is remembered in its place. |
| `--depth N`   | How many folder levels below the folder to search; default all.                  |

The first start of the window asks for the folder that holds your projects; it is remembered in
`local/gui.json`, which is never committed. **Change…** picks another one. If `save` runs before a
folder was ever chosen, it searches the home folder instead, without AppData and without cloud
synced folders (OneDrive, SharePoint libraries, Dropbox, ...): git running there could make the
sync client download or upload files.

Do a `save --dry-run` once after setting up, to see that the folder and the remotes are right.

## The window

- **Tree**: only the repositories with unsaved work, with what is unsaved (*3 files · 1 branch ·
  1 stash*), the server it goes to and its status. A repository without a remote is shown red: it
  cannot be saved.
- **Ticks**: everything is ticked at every start and every Refresh. Unticking only affects the
  current run, so a repository left out once is never forgotten in the next emergency.
- **Save** asks for the SSH key passphrases the ticked repositories need, all at once, and then
  runs through on its own. Each row follows live: saving, saved, partly saved, failed or skipped.
- **Details**: what Save will do with the selected repository, and afterwards where everything
  went.

## SSH keys with a passphrase

Save asks for each needed key's passphrase right at the start, once per key. The passphrase is
never stored: it unlocks the key into a private ssh-agent that only this run uses, and that agent
is stopped when the run ends or the window closes (also when Python crashes). **Skip these** (or an
empty passphrase in `save`) leaves out the repositories that need the key; a wrong passphrase asks
again.

## The record

Every run writes `local/runs/<stamp>.json`: each repository, what was pushed to which branch on
which remote, what failed and why, and the large files that were left out. It stays on this machine.

## Good to know

- Pushing a branch that never was on the server creates it there under its own name, where others
  see it, and it may trigger the server's pipelines. A branch that was on the server and got
  deleted there (for example after a squash merge) goes to an emergency branch instead.
- Pre-push hooks run as for any push.
- Repositories are saved in parallel; one that fails does not stop the others.

## After the emergency

Restoring and cleaning up are deliberately not part of breakglass: they are careful, one-off work.

**Find what was saved.** The record lists every branch; without it (the machine is gone), ask each
server:

```sh
git ls-remote origin 'refs/heads/emergency/*'
```

**Restore uncommitted changes** into a clone, on top of the commit they were made on:

```sh
git fetch origin 'refs/heads/emergency/*:refs/remotes/origin/emergency/*'
git switch --detach origin/emergency/<you>/<stamp>/wip-on-<branch>^1   # the commit they were made on
git restore --source=origin/emergency/<you>/<stamp>/wip-on-<branch> --worktree -- .
git restore --source=origin/emergency/<you>/<stamp>/wip-on-<branch>^2 --staged -- .   # what was staged
```

**Restore a stash**: `git stash apply origin/emergency/<you>/<stamp>/stash-0`.

**Clean up** a branch once its content is safe again:

```sh
git push origin --delete emergency/<you>/<stamp>/wip-on-<branch>
```

## Development

```sh
python -m unittest -v
```

The tests use throwaway local repositories as servers and a throwaway SSH key; nothing goes over
the network. The window tests skip without a display unless `BREAKGLASS_GUI_TESTS=required`, as in
CI (`.github/workflows/breakglass.yml`).
