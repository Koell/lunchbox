"""The command line.

    python -m breakglass                     the window
    python -m breakglass save [--dry-run]    save everything now, no questions but passphrases
    python -m breakglass install-shortcut    put a "Break glass" icon on the desktop

tkinter is imported only for the window, so `save` works without it.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Sequence
from pathlib import Path

from . import save, scan, shortcut, ssh
from .proc import quiet_env
from .state import State

NO_TK = (
    "breakglass: error: the window needs tkinter, which this Python lacks"
    " (Manjaro/Arch: pacman -S tk; Debian/Ubuntu: apt install python3-tk);"
    " `python -m breakglass save` works without it"
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="breakglass",
        description="Emergency save: push every repository's unsaved work to its server.",
    )
    sub = parser.add_subparsers(dest="command")
    for name, text in (("gui", "open the window (the default)"), ("save", "save everything now")):
        command = sub.add_parser(name, help=text)
        command.add_argument("--path", type=Path, default=None,
                             help="folder to look for repositories in (default: the one chosen last)")
        command.add_argument("--depth", type=int, default=None,
                             help="how many folder levels below the folder to search (default: all)")
        if name == "save":
            command.add_argument("--dry-run", action="store_true", help="only show what would be saved")
    sub.add_parser("install-shortcut", help='put a "Break glass" icon on the desktop that opens the window')
    argv = list(argv or [])
    if not argv or argv[0] not in {*sub.choices, "-h", "--help"}:
        argv.insert(0, "gui")  # no command: the window, with whatever options were given
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "install-shortcut":
        try:
            print(f"breakglass: shortcut created: {shortcut.install()}")
        except RuntimeError as error:
            print(f"breakglass: error: {error}", file=sys.stderr)
            return 1
        return 0

    folder = args.path.expanduser().resolve() if args.path else None
    if folder is not None and not folder.is_dir():
        print(f"breakglass: error: no such folder: {folder}", file=sys.stderr)
        return 1
    if args.command == "save":
        return run_save(folder, args.depth, args.dry_run)
    return run_gui(folder, args.depth)


def run_gui(folder: Path | None, depth: int | None) -> int:
    try:
        import tkinter
    except ImportError:
        print(NO_TK, file=sys.stderr)
        return 1
    try:
        root = tkinter.Tk()
    except tkinter.TclError as error:
        print(f"breakglass: error: no display for the window ({error});"
              " use `python -m breakglass save`", file=sys.stderr)
        return 1

    from .app import App

    App(root, folder, depth)
    root.mainloop()
    return 0


def run_save(folder: Path | None, depth: int | None, dry_run: bool, state: State | None = None,
             ask: object = getpass.getpass) -> int:
    """Save everything below `folder` (or the one chosen last, or the home folder) right away."""
    state = state or State()
    skip: set[Path] = set()
    folder = folder or state.folder()
    if folder is None or not folder.is_dir():
        folder, skip = scan.home_fallback()
        say(f"No project folder chosen yet: searching {folder}, without AppData and cloud folders.")
    else:
        state.remember_folder(folder)

    say(f"Looking for repositories in {folder} …")
    repos = scan.scan(folder, depth, skip)
    try:
        tools = ssh.find_ssh_tools()
    except RuntimeError as error:
        say(f"SSH keys can't be unlocked ({error}); SSH remotes may fail.")
        tools = None
    scan.inspect_all(repos, tools, lambda repo: None)
    unsaved = [r for r in repos if r.unsaved]
    for repo in repos:
        if repo.error:
            say(f"  ! {repo.name}: could not be read: {repo.error}")
    say(f"{len(repos)} repositories, {len(unsaved)} with unsaved work.")
    for repo in unsaved:
        where = repo.remote or "NO REMOTE: can't be saved"
        say(f"  {repo.name}: {repo.summary()} -> {where}")
        for path, size in repo.large:
            say(f"      left out, {size // (1024 * 1024)} MB: {path}")
    if dry_run or not unsaved:
        return 0

    agent = None
    skipped_keys: set[Path] = set()
    keys = list(dict.fromkeys(k for r in unsaved for k in r.keys))
    if keys and tools is not None:
        agent = ssh.Agent(tools)
    try:
        for key in keys:
            users = sum(key in r.keys for r in unsaved)
            error = None
            while True:
                hint = f" ({error})" if error else ""
                passphrase = ask(f"Passphrase for {key}, {users} repositories need it{hint}; empty to skip: ")
                if not passphrase:
                    skipped_keys.add(key)
                    break
                error = agent.add(key, passphrase)
                passphrase = None
                if error is None:
                    break

        run = [r for r in unsaved if not skipped_keys.intersection(r.keys)]
        for repo in unsaved:
            if repo not in run:
                say(f"  - {repo.name}: skipped, no passphrase for its key")
        env = quiet_env(**({"GIT_SSH_COMMAND": ssh.ssh_command(tools)} if tools else {}),
                        **(agent.env if agent else {}))
        run_stamp = save.stamp()
        say(f"Saving {len(run)} repositories …")
        results = save.save_all(run, run_stamp, env, started=lambda repo: None,
                                done=lambda repo, result: report(repo, result))
    finally:
        if agent is not None:
            agent.close()

    record = save.write_record(state.runs, run_stamp, folder, results, run)
    saved = sum(r.ok for r in results)
    say(f"Saved {saved} of {len(unsaved)} repositories. Record: {record}")
    return 0 if saved == len(unsaved) else 1


def report(repo: scan.Repo, result: save.Result) -> None:
    mark = "ok" if result.ok else "FAILED"
    lines = [f"  {mark}: {repo.name}"]
    if result.error:
        lines.append(f"      {result.error}")
    for o in result.outcomes:
        where = f"{o.remote} {o.ref.removeprefix('refs/heads/')}" if o.ok else f"not saved: {o.message}"
        lines.append(f"      {o.source} -> {where}")
    say("\n".join(lines))


def say(text: str) -> None:
    print(text, flush=True)
