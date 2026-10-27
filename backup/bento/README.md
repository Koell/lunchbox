# BENTO

**B**ackup, **E**ncrypt '**n**' **T**ake **O**ut: my configuration, packed once and unpacked on
every machine.

This folder holds the tool and the list of what it manages; the configuration itself is packed into
a private **bento**, a `.tar.gz` that never enters the repository and can be encrypted. A small,
dependency-free Python tool copies the bento onto a machine (**deploy**), merges a machine's
configuration into the bento (**capture**), and shows what either would change (**status**), from
the command line or in a [window](#the-gui).

## Prerequisites

- Python 3.11 or newer. Nothing else: no `pip install`, no virtualenv.
- Git, to clone.
- GnuPG (`gpg` on the PATH), only for an [encrypted bento](#encryption). Git for Windows ships one;
  on Linux it is almost always installed (Arch and Manjaro need it for pacman).
- Tk, only for [the GUI](#the-gui). It ships with Python on Windows and macOS; on Linux install
  `tk` (Manjaro, Arch) or `python3-tk` (Debian, Ubuntu).

## Quick start

All commands run from this folder, `backup/bento` in the lunchbox repository. On the first machine,
pack the bento:

```sh
git clone <lunchbox repository> lunchbox
cd lunchbox/backup/bento
python -m bento capture             # writes export/bento.tar.gz
python -m bento capture --encrypt   # optional: encrypt it with a passphrase
```

On every other machine, bring the bento along and unpack it:

```sh
git clone <lunchbox repository> lunchbox
cd lunchbox/backup/bento
python -m bento status --bento ~/Downloads/bento.tar.gz   # what would change?
python -m bento deploy --bento ~/Downloads/bento.tar.gz   # copy the configuration onto this machine
```

Use whichever command starts Python 3.11+ on the machine: `python3` on most Linux and macOS
systems, `py -3` or `python` on Windows. An older Python stops with one line saying so.

## Commands

| Command                   | What it does                                       |
|---------------------------|----------------------------------------------------|
| `python -m bento deploy`  | Copy the bento's configuration onto this machine.  |
| `python -m bento capture` | Merge this machine's configuration into the bento. |
| `python -m bento status`  | Show what deploy and capture would change.         |
| `python -m bento gui`     | Open [the window](#the-gui).                       |

All commands but `gui` take optional module names (none means all modules) and `--bento PATH` (see
[The bento](#the-bento)). `deploy` and `capture` take `--dry-run` to report without writing.
`capture` also takes `--force` (see [Guards](#guards)), `--prune` (see
[Retiring files](#retiring-files)), and `--encrypt` or `--decrypt` (see [Encryption](#encryption)).

```sh
python -m bento capture --dry-run
python -m bento deploy claude
python -m bento status claude --bento D:/bento.tar.gz
```

Each report starts with the bento, when it was captured and whether it is encrypted, then lists per
module the files `added` and `updated`, one per line, followed by the number of unchanged files.
Exit code 0 means success; anything else means an error or a refused file.

`status` also reports, per module, `excluded by selection: N`: the files on this machine that the
bento's [stored selection](#the-stored-selection) keeps out, so they don't silently disappear.

## The bento

The bento is a `.tar.gz` with two entries: a plain `label.json` (the bento's version, the capture
time in UTC, and the encryption if any), and the contents, every module's files as
`<module>/<path>` in one tar:

```text
bento.tar.gz
  label.json                          {"version": 1, "captured": "2026-10-23T15:11:41Z"}
  contents.tar   or  contents.tar.gpg (encrypted)
    .bento/selection.json             the stored selection, if anything is kept out
    claude/CLAUDE.md
    claude/skills/tdd/SKILL.md
    git/aliases
```

`.bento/` holds the bento's own data; a module name never starts with a dot, so the two can't
collide. A checkout from before the stored selection refuses a bento that holds one as having an
unsafe entry: pull on every machine before using a bento captured with a selection.

- **Where:** `export/bento.tar.gz` in this folder by default. `export/` is git-ignored and created
  on demand by the first capture, so the bento can never be committed by accident. `--bento PATH`
  uses another file; a relative path is relative to this folder, where the command runs.
- **Getting it to another machine** is up to you: copy the file (USB stick, `scp`, a private cloud
  folder) and either drop it into `export/` or point `--bento` at it. A plain bento is readable by
  anyone who gets the file; [encrypt it](#encryption) before it leaves your hands.
- **Backups:** every capture that changes the bento keeps the previous one next to it as
  `bento.<capture time>.tar.gz`, for example `bento.20261023T141502Z.tar.gz`; the five newest are
  kept. To undo a capture, deploy from a backup with `--bento`, or copy it over `bento.tar.gz`. A
  capture that changes nothing leaves the bento and its backups alone.
- **Inspecting:** a plain bento with `tar -xzOf export/bento.tar.gz contents.tar | tar -t` (list) or
  `… | tar -x -C /tmp/bento` (unpack and diff); an encrypted one with
  `tar -xzOf export/bento.tar.gz contents.tar.gpg | gpg -d | tar -t`. bento itself never unpacks
  the bento to disk; it reads it in memory and refuses a bento with absolute paths, `..`, links or
  other non-file entries.
- **Label:** `label.json` only describes the bento. If it is missing or damaged, the files are still
  used, the report shows a warning and "capture time unknown", and the next capture rewrites it. Only
  a bento `version` newer than this checkout is refused: pull the repository first.

## Encryption

`capture --encrypt` encrypts the contents with GnuPG (AES-256, passphrase stretched with
iterated-and-salted SHA-512). The label stays readable, so status shows the capture time; module
and file names are only inside the encrypted part.

- **It sticks.** Once encrypted, every later capture keeps the bento encrypted with the same
  passphrase. `capture --encrypt` on an encrypted bento changes the passphrase; `capture --decrypt`
  makes it plain again (refused while it holds a [module with secrets](#secrets)).
- **The passphrase is asked for**, hidden, once per command that reads or writes an encrypted bento,
  including `status`. It is never read from the environment or a file, and never stored. A new one
  needs at least 4 characters and is asked twice.
- **Nothing decrypted touches the disk**: gpg works through pipes, and bento keeps the contents
  in memory. A new encrypted bento is decrypted once in memory before it replaces the old one, so a
  bento that can't be opened again is never written.
- **Plain backups are deleted** after a successful `capture --encrypt`; they would otherwise keep
  the unencrypted contents next to the encrypted bento. Encrypted backups are kept as usual.
- **A forgotten passphrase cannot be recovered.** The bento and all its backups are lost with it;
  capture again from a machine that still has the configuration.
- A wrong passphrase, a damaged bento, or a missing `gpg` stop the command with one line before
  anything is written.

### Secrets

A module meant to hold credentials, such as SSH or GPG keys, says so in the manifest:

```toml
[modules.ssh]
target = "~/.ssh"
include = ["*"]
secrets = true
plain = ["config", "known_hosts", "*.pub"]
```

- Its files skip the secret guard; the portability guard still applies.
- It only ever goes into an encrypted bento: capture into a plain bento refuses the module, and
  `capture --decrypt` is refused while the bento holds it.
- On Linux and macOS, deploy writes its files as `0600` and its directories, the target included,
  as `0700`, which is what ssh and gpg demand. New files are created with that mode, never briefly
  readable by others.
- Every other module stays guarded even in an encrypted bento, so a token pasted into `CLAUDE.md`
  is still caught.

`bento.toml` is public, so it never names a private file: no key names, nothing that reveals a
naming scheme or an employer. A secrets module uses generic patterns, and which of the files it
matches actually travel is decided by unticking in the GUI's Capture tab, stored in the bento's
[selection](#the-stored-selection), inside the encrypted contents.

### High-risk files

Some files need more care than others, even inside a secrets module: an SSH `config` is harmless,
the private key next to it is not. A file is **high-risk** when

- its content holds a private key (PEM, OpenSSH, an exported GPG secret key, GnuPG's own key files)
  or a credential the [guards](#guards) recognise, in any module; or
- it belongs to a module with `secrets = true` and matches none of the module's `plain` patterns.

`plain` lists the harmless files of a secrets module. It can only lower that default: a `plain`
file whose content holds a key is still high-risk, so a forgotten pattern makes a file more
protected, never less. `plain` in a module without `secrets = true` is a warning and is ignored.

High-risk files are marked wherever they show: the CLI's reports end their lines in
`(high risk: private key)` and count them on the bento line; the GUI colours them, never shows their
diff (not even after **Show**), counts them in the bento bar, and asks before writing one, into the
bento or onto the machine, even when that is all a run does. Nothing beyond that is blocked.

## Deploy overwrites without backup — run `status` first

Deploy overwrites differing files on the machine and keeps no backup of them. If you may have
changed something locally, run `status` (or `capture`) before deploying, so local edits are not lost.
The GUI asks before overwriting, lists the files, and shows each one's diff.

## Capture merges: nothing is lost by accident

Capture merges this machine into the bento: files from this machine are added or updated, and
everything else in the bento stays. A file this machine does not have, a module whose target
directory does not exist here, and a file refused by the guards all keep their version in the
bento. So capturing on a machine that lacks a skill never drops it from the bento.

Deploy, likewise, only adds and overwrites files on the machine; it never deletes.

## The stored selection

The bento can remember files it keeps out: the **stored selection**. Untick a file or folder in the
GUI's Capture tab and capture, and the bento drops it and stores the untick, so every later
capture, from the GUI or the CLI, on any machine, keeps it out. An unticked folder also keeps out
files that appear in it later; a single file can be ticked again inside it.

- It only **narrows** `bento.toml`: it can keep managed files out, never bring in anything the
  manifest doesn't manage.
- It travels **inside the contents**, as `.bento/selection.json`, so an encrypted bento encrypts it
  too and no file names leak.
- The **CLI honours it but can't edit it**: `capture` keeps excluded files out and removes them from
  the bento, `deploy` never writes them, and `status` counts them as `excluded by selection`.
  Changing it is done in the GUI.
- A bento without one keeps everything the manifest manages.

## Retiring files

To retire a file, such as a skill, **untick it in the GUI's Capture tab and capture**. The bento
drops it and stores the untick, so a machine that still has the file doesn't bring it back on its
next capture. Deploy never deletes, so delete the file on the other machines by hand if you want it
gone there too.

Without the GUI, `capture --prune` drops what this machine no longer has: for every module whose
target exists here, the bento entries this machine lacks, plus entries the manifest no longer
manages; when every module is captured, also modules no longer in `bento.toml`. They are listed as
`removed`. Check first: `status` lists them as `only in bento`, and `capture --prune --dry-run`
shows exactly what goes. Prune stores nothing, so delete the file on every other machine too, or
the next capture from one that still has it brings it back.

## Guards

Capture refuses to store a file that contains

- this machine's home directory in any spelling (`C:\Users\me`, `C:\\Users\\me`, `C:/Users/me`,
  `/c/Users/me`, `/home/me`), because such configuration breaks on other machines — use `~` or an
  environment variable instead; or
- something that looks like a credential: Anthropic, GitHub and AWS keys; private key blocks (PEM,
  OpenSSH, exported GPG secret keys) and GnuPG's own key files; and fields named like `api_key`,
  `token`, `secret`, `password` or `*-key-data` with a long literal value, whether written as JSON,
  TOML, YAML, `.properties`/`.env` (`key=value`) or `export NAME=value` in a shell profile.
  References such as `${VAR}` or `$(command)`, paths and placeholders don't count. Modules with
  [`secrets = true`](#secrets) are exempt from this one.

Refused files are reported with file, line and reason; all other files are still captured, a refused
file keeps its previous version in the bento, and the exit code is non-zero. If a finding is a false
positive, `capture --force` captures the file anyway and prints the findings as warnings.

The guards are a safety net, not a guarantee: they miss credentials in formats they don't know.

## The GUI

```sh
python -m bento gui                          # the bento opened last on this machine
python -m bento gui --bento D:/bento.tar.gz  # a specific one
```

The window does what the commands do, with checkboxes instead of flags and a diff before anything is
written. It needs Tk (see [Prerequisites](#prerequisites)); without it, or without a display (over
SSH), `gui` says so in one line and the CLI keeps working.

**The bento bar** shows the open bento's path, capture time and whether it is encrypted.
**Open…** picks an existing bento, backups included, so you can deploy from an older capture;
**New…** picks where a first capture writes. The last opened bento is remembered per machine in
`local/gui.json` next to `export/` (git-ignored, never read by the CLI); if it is gone, the window
falls back to `export/bento.tar.gz` with a note. An encrypted bento asks for its passphrase once,
hidden, and keeps it in memory only until the window closes or another bento is opened.
**Encrypt…** sets or changes the passphrase (asked twice, at least 4 characters) and rewrites the
bento encrypted, dropping its plain backups; before the first capture it makes that capture write
an encrypted bento. **Decrypt** makes it plain, refused while it holds a module with secrets. Both
confirm first and keep the stored selection.

**Two tabs, one per direction**, each a tree of modules, folders and files with a state per file.
Unchanged files are hidden until **Show unchanged**; a module whose target directory doesn't exist
on this machine is greyed out as `skipped`. **Refresh** reads `bento.toml`, the machine and the
bento again. [High-risk files](#high-risk-files) are coloured and marked `high risk`, and the bento
bar counts them.

- **Capture** (this machine → bento) shows the files `bento.toml` manages here, plus those only in
  the bento. A tick means "in the bento": ticks start from the [stored selection](#the-stored-selection),
  new files start ticked, files only in the bento start ticked (kept). Unticking a file or folder
  removes it from the bento and stores the untick. Entries `bento.toml` no longer manages are
  greyed out as `not managed`; **Drop entries bento.toml no longer manages** removes them, and
  modules no longer in the manifest.
- **Deploy** (bento → this machine) shows the bento's files and what deploy would add or overwrite.
  A tick means "write it in this run": everything starts ticked, and an untick only skips the file
  this time, without changing the bento.

**Details:** selecting a file shows its state and its size here and in the bento, and for an added
or updated file a unified diff in which `+` is always what the action writes: this machine's
version in Capture, the bento's in Deploy. Binary files show both sizes instead, long diffs stop
after 2,000 lines, and files of a module with `secrets = true` show their diff only after **Show**.
A high-risk file never shows its diff, not even after Show: only whether it differs, and both sizes.

**Guards:** a file the guards refuse stays ticked but is marked `refused`; its details list each
finding with line and reason, highlighted in the diff. **Allow this file** captures it anyway in
this run only (the CLI's `--force` allows everything instead); allows are never stored and reset
after a capture and on Refresh. A module with secrets is refused as a whole while the bento is plain.

**Confirmations:** a capture that only adds and updates files, and a deploy that only adds files,
run directly. Capture asks first, listing them, when files leave the bento, something is refused,
files are allowed despite findings, a high-risk file is written, or the encryption changes; deploy
asks before overwriting files, since it keeps no backup, and before writing a high-risk file, even
a new one. The result panel at the bottom then shows what was written, the
backup kept, and any warnings, as the CLI would.

**Nothing is written over a surprise:** capture refuses to write when the bento file changed on disk
since it was read (a capture from a terminal, say), reads it again and keeps your ticks. Both
actions read the machine again when they run and stop, listing the files, if the result would
differ from what the tree showed.

## Modules

`bento.toml` in this folder lists the modules, one table each, processed in file order:

```toml
[modules.claude]
target = "${CLAUDE_CONFIG_DIR:-~/.claude}"
include = ["CLAUDE.md", "settings.json", "skills/**", "custom/**"]
exclude = ["skills/synced/**", "skills/.trash/**", "**/*.zip"]
```

- The name (`claude`) may use letters, digits, `-` and `_`; it is the folder in the bento.
- `target` is where the module lives on the machine. `${VAR:-default}` uses `VAR` when it is set
  and non-empty, otherwise `default`; a leading `~` is the home directory. A target that differs
  between Windows and Linux can lean on a variable only one of them sets, for example
  `${APPDATA:-~/.config}/JetBrains/IntelliJIdea2026.2`.
- A file is managed when it matches an `include` pattern and no `exclude` pattern. `*` matches
  within one path segment, `**` across directories. Nothing outside these patterns is ever read or
  written, so runtime state, history, caches and credentials stay out.
- New files under an included folder (a new skill, for example) are picked up automatically.
- `secrets = true` marks a module that holds credentials (see [Secrets](#secrets)), and `plain`
  lists its harmless files (see [High-risk files](#high-risk-files)).

Adding a module needs no code: add a table and run `capture`. The manifest is public and holds
patterns only; the files themselves only ever go into the bento. The GUI reads `bento.toml` again
on every **Refresh**; warnings go to its result panel (the CLI prints them on stderr), and a broken
manifest keeps the last good one while Capture and Deploy wait until it is fixed.

Deploy creates a module's target directory only when it has something to write there, so modules
for tools a machine doesn't use leave no empty folders behind.

### `claude`

My global Claude Code configuration: `CLAUDE.md`, `settings.json`, skills and `custom/` (the status
line script). Skills synced by claude.ai (`skills/synced/`), Claude Code's trash of deleted skills
(`skills/.trash/`) and stray archives are excluded. When `CLAUDE_CONFIG_DIR` is set, the module
targets that directory, as Claude Code does.

Not managed: `~/.claude.json` (Claude Code's own state, including user-scope MCP servers),
`settings.local.json`, plugins, credentials and project-level `.claude/` folders. MCP servers are
set up again on each machine: `claude mcp add` (or `claude mcp add-json`), or, for the JetBrains IDEs'
own servers, from the IDE's MCP settings. Keep a note of them in `CLAUDE.md`, which travels.

### `git`

Git aliases, global ignore rules and portable global settings, in `~/.config/git` (or
`$XDG_CONFIG_HOME/git`): `aliases`, `ignore` and `config`. Git reads `ignore` and `config` on its
own; `config` is read as global configuration in addition to `~/.gitconfig`, so identity
(`user.name`, `user.email`) and preferences go there, with
`git config --file ~/.config/git/config user.name '…'`, while per-machine settings (tool paths,
`maintenance.repo`, credential helpers) stay in `~/.gitconfig`. `aliases` is a separate file that `~/.gitconfig` includes;
set that up once on each machine:

```sh
./setup-git.sh       # or .\setup-git.ps1
```

It adds `include.path` for the aliases file to the global git config, honouring
`$XDG_CONFIG_HOME`. Running it again changes nothing, and other `include.path` entries are left
alone. By hand, it is `git config --global --add include.path ~/.config/git/aliases`.

`git config --global alias.<name> …` writes to `~/.gitconfig`, where capture does not look. Add
aliases to the shared file instead, by editing it or with
`git config --file ~/.config/git/aliases alias.<name> '<command>'`.

### `ssh`

The top level of `~/.ssh`: `config`, `known_hosts` and key pairs; `agent/` and anything in
subfolders stay out. A secrets module: everything except `config`, `known_hosts` and `*.pub` is
high-risk, so a key added later is protected without being listed. To keep a key from travelling,
untick it in the Capture tab. `config` must refer to keys as `~/.ssh/…`, or the portability guard
refuses it. Check a deployed key with `ssh -T git@github.com`.

### `gnupg`

A GnuPG identity in `$GNUPGHOME`, by default `~/.gnupg`: private keys (`private-keys-v1.d`),
revocation certificates, the public keyring in either format (`pubring.kbx`, or
`public-keys.d/pubring.db` with keyboxd), the trust database and `gpg.conf`. Private keys and
revocation certificates are high-risk. `gpg-agent.conf` and `common.conf` hold platform-specific
paths and stay out. Gpg4win on Windows keeps its keyring in `%APPDATA%\gnupg`: set `GNUPGHOME` to
that folder before capturing or deploying.

After a deploy, a running agent doesn't see the new keys yet; restart it, then check:

```sh
gpgconf --kill all
gpg -K
```

### `gradle`, `pypi`

`~/.gradle/gradle.properties` and `~/.pypirc`, both secrets modules with every file high-risk: they
usually hold repository and upload credentials.

### `shell`

Shell setup in the home folder, by exact name: `.zshrc`, `.zshenv`, `.zprofile`, `.bashrc`,
`.bash_profile`, `.profile`, `.inputrc`. History files are never included. A token exported in one
of them is refused by the guards; keep it in a separate file that isn't managed and source it.

### `junie`, `copilot`, `codex`, `gemini`

AI coding tools, settings and instructions only: Junie's `settings.json`; Copilot's
`instructions/` and `prompts/`; Codex's `config.toml`, `AGENTS.md` and `prompts/` (in
`$CODEX_HOME`, by default `~/.codex`); Gemini's `settings.json`, `GEMINI.md` and `commands/`.
Logins (`auth.json`, OAuth credentials), sessions and logs stay out: log in again on each machine.

## Environments

- **Windows, Linux, macOS** are all supported; the tool behaves the same everywhere.
- **WSL** has its own home and its own `~/.claude`. It is a separate environment: deploy there
  separately.
- **Line endings:** files go into the bento and back out byte for byte, so a file keeps the line
  endings it had on the machine it was captured from. Deploy makes `.sh` files executable on
  Linux and macOS.

## Not managed on purpose

`~/.gitconfig` stays out of the bento: credential helpers, tool paths and other per-machine values
must not travel between machines. What should travel goes into `~/.config/git/config` (see
[`git`](#git)).

Credentials issued per device or expiring on their own also stay out: a kube config, Docker
registry logins, Claude Code's `.credentials.json`, short-lived API tokens. Logging in again on the
new machine is safer than a copy that goes stale or, with rotating refresh tokens, logs the other
machine out.

## Development

```sh
python -m unittest
```

Tests use only the standard library and never touch your configuration or the real bento. The
encryption tests run the real `gpg` (skipped when it is missing) with test passphrases; gpg uses its
default home directory for its own state, but no keys or keyrings are involved. CI runs the tests on
Ubuntu, macOS and Windows with Python 3.11 and 3.14, and smoke-runs the commands and the setup-git
scripts: it captures a fake machine (`bento/tests/fixtures/machine`) into a temporary plain bento and
deploys that bento into a temporary `CLAUDE_CONFIG_DIR`, `XDG_CONFIG_HOME` and `GIT_CONFIG_GLOBAL`.
The encrypted path is not smoke-tested, because the passphrase can only be typed.

The GUI keeps its logic in a Tk-free model (`bento/gui/model.py`: the tree, ticks, the stored
selection they become, confirmations, diffs), covered by plain unit tests. A Tk smoke test builds
the window on a copy of the fixture machine and deploys, captures, encrypts and decrypts with
temporary bentos. It needs a display: it runs on the Windows and macOS runners and under `xvfb-run`
on Ubuntu, and is skipped locally without one. Which files are high-risk is decided in one place,
`bento/risk.py`, shared by the CLI and the GUI and tested on its own. The CI smoke test captures and
deploys only the fixture's modules (`claude git`); the others point at the runner's own home.
