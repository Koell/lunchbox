# lunchbox

My configuration, packed once and unpacked on every machine.

This repository is the single source of truth for my personal configuration. A small,
dependency-free Python tool copies it onto a machine (**deploy**), copies local changes back into
the repository (**capture**), and shows what either would change (**status**). Committing is always
left to me.

## Prerequisites

- Python 3.11 or newer. Nothing else: no `pip install`, no virtualenv.
- Git, to clone and commit.

## Quick start

```sh
git clone <this repository> lunchbox
lunchbox/cli/status.sh    # what would change?
lunchbox/cli/deploy.sh    # copy the configuration onto this machine
```

On Windows, from PowerShell:

```powershell
.\lunchbox\cli\status.ps1
.\lunchbox\cli\deploy.ps1
```

If Windows PowerShell refuses to run scripts, call them with
`powershell -ExecutionPolicy Bypass -File .\lunchbox\cli\deploy.ps1` or loosen the execution policy
for your user (`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`).

## Commands

| Entry script                       | Equivalent                  | What it does                                           |
|------------------------------------|-----------------------------|--------------------------------------------------------|
| `cli/deploy.sh`, `cli/deploy.ps1`   | `python -m lunchbox deploy`  | Copy the repository's configuration onto this machine. |
| `cli/capture.sh`, `cli/capture.ps1` | `python -m lunchbox capture` | Copy this machine's configuration into the repository. |
| `cli/status.sh`, `cli/status.ps1`   | `python -m lunchbox status`  | Show what deploy and capture would change.             |

All commands take optional module names (none means all modules). `deploy` and `capture` take
`--dry-run` to report without writing; `capture` takes `--force` (see [Guards](#guards)).

```sh
cli/capture.sh --dry-run
cli/deploy.sh claude
python -m lunchbox status claude     # from the repository root
```

The entry scripts work from any directory and find a suitable Python themselves (`python3`, then
`python`; on PowerShell `py -3`, then `python`, then `python3`). If none is 3.11 or newer they say
so in one line and exit non-zero.

Each module's report lists the files `added` and `updated`, one per line, followed by the number of
unchanged files. Exit code 0 means success; anything else means an error or a refused file.

## Deploy overwrites without backup — run `status` first

Deploy overwrites differing files on the machine and keeps no backup. If you may have changed
something locally, run `status` (or `capture`) before deploying, so local edits are not lost.

## Additive only: nothing is ever deleted

Both directions only add and overwrite files; they never delete. To retire a file, such as a
skill, remove it by hand from the repository **and** from each machine. If it is still on a machine,
the next capture there brings it back: it shows up under `added` and as a new file in `git status`,
so check those before committing.

## Guards

Capture refuses to store a file that contains

- this machine's home directory in any spelling (`C:\Users\me`, `C:\\Users\\me`, `C:/Users/me`,
  `/c/Users/me`, `/home/me`), because such configuration breaks on other machines — use `~` or an
  environment variable instead; or
- something that looks like a credential: Anthropic, GitHub and AWS keys, private key blocks, and
  `api_key` / `token` / `secret` / `password` fields with a long literal value.

Refused files are reported with file, line and reason; all other files are still captured, and the
exit code is non-zero. If a finding is a false positive, `capture --force` captures the file anyway
and prints the findings as warnings.

The guards are a safety net, not a guarantee. This repository is public; GitHub secret scanning and
push protection are enabled as a second line of defence.

## Modules

Configuration is organized in modules. Each module is a folder under `modules/` holding a
`manifest.toml` and a `files/` folder that mirrors the target directory exactly, so repository and
machine can be compared with any diff tool.

```toml
# modules/claude/manifest.toml
target = "${CLAUDE_CONFIG_DIR:-~/.claude}"
include = ["CLAUDE.md", "settings.json", "skills/**", "custom/**"]
exclude = ["skills/synced/**", "skills/.trash/**", "**/*.zip"]
```

- `target` is where the module lives on the machine. `${VAR:-default}` uses `VAR` when it is set
  and non-empty, otherwise `default`; a leading `~` is the home directory.
- A file is managed when it matches an `include` pattern and no `exclude` pattern. `*` matches
  within one path segment, `**` across directories. Nothing outside these patterns is ever read or
  written, so runtime state, history, caches and credentials stay out.
- New files under an included folder (a new skill, for example) are picked up automatically.

Adding a module needs no code: create a folder with a manifest and its files.

### `claude`

My global Claude Code configuration: `CLAUDE.md`, `settings.json`, skills and `custom/` (the status
line script). Skills synced by claude.ai (`skills/synced/`), Claude Code's trash of deleted skills
(`skills/.trash/`) and stray archives are excluded. When
`CLAUDE_CONFIG_DIR` is set, the module targets that directory, as Claude Code does.

Not managed: `~/.claude.json` (including MCP servers), `settings.local.json`, plugins, credentials
and project-level `.claude/` folders.

### `git`

Git aliases and global ignore rules, in `~/.config/git` (or `$XDG_CONFIG_HOME/git`): `aliases` and
`ignore`. Git reads `ignore` on its own. `aliases` is a separate file that `~/.gitconfig` includes;
set that up once on each machine:

```sh
cli/setup-git.sh       # or cli/setup-git.ps1
```

It adds `include.path` for the aliases file to the global git config, honouring
`$XDG_CONFIG_HOME`. Running it again changes nothing, and other `include.path` entries are left
alone. By hand, it is `git config --global --add include.path ~/.config/git/aliases`.

`git config --global alias.<name> …` writes to `~/.gitconfig`, where capture does not look. Add
aliases to the shared file instead, by editing it or with
`git config --file ~/.config/git/aliases alias.<name> '<command>'`.

## Environments

- **Windows, Linux, macOS** are all supported; the tool behaves the same everywhere.
- **WSL** has its own home and its own `~/.claude`. It is a separate environment: deploy there
  separately.
- **Line endings:** `.gitattributes` keeps the working tree on LF (CRLF for `.ps1`) regardless of
  your git settings, and files are copied byte for byte, so captures from different machines do not
  produce spurious diffs.

## Not managed on purpose

`~/.gitconfig` and `~/.config/git/config` stay out of this repository: identity, credential helpers
and other per-machine values must never be shared or published. The `git` module manages only its
own files next to them.

## Development

```sh
python -m unittest
```

Tests use only the standard library and never touch the real home directory. CI runs them on
Ubuntu, macOS and Windows with Python 3.11 and 3.14, and smoke-runs the entry scripts against a
temporary `CLAUDE_CONFIG_DIR`, `XDG_CONFIG_HOME` and `GIT_CONFIG_GLOBAL`.
