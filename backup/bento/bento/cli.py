"""Command line: `python -m bento {deploy,capture,status} [module ...] [--bento PATH]`, and
`python -m bento [--bento PATH]` without a command for the window."""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Callable, Container, Mapping, Sequence
from pathlib import Path

from . import archive, guards, ops, picks, risk, sync
from .archive import Bento, BentoError
from .manifest import ManifestError, Module, read

EXIT_OK, EXIT_ERROR, EXIT_USAGE = 0, 1, 2
MIN_PASSPHRASE = 4
COMMANDS = ("deploy", "capture", "status", "help")

Prompt = Callable[[str], str]


class UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    """Answers a malformed command line with the full help rather than the one-line usage."""

    def error(self, message):
        self.print_help(sys.stderr)
        self.exit(EXIT_USAGE, f"\n{self.prog}: error: {message}\n")


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = _parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and not argv[0].startswith("--"):  # `-c` and `c` both stand for `capture`
        if (command := _expand(argv[0].removeprefix("-"))) in COMMANDS:
            argv[0] = command
    args, extra = parser.parse_known_args(argv)
    if extra:  # reported by the command's own parser, so the help shown is that command's
        getattr(args, "parser", parser).error(f"unrecognized arguments: {' '.join(extra)}")
    if args.command is not None and args.window_bento is not None:
        parser.error(f"--bento goes after the command: bento {args.command} --bento PATH")
    if args.command == "help":
        topic = args.topic and _expand(args.topic)
        if topic not in (None, "help"):
            parser.parse_args([topic, "--help"])  # prints that command's help and exits
        parser.print_help()
        return EXIT_OK
    try:
        manifest = read()
        for warning in manifest.warnings:
            print(f"bento: warning: {warning}", file=sys.stderr)
        known = manifest.modules
        if args.command is None:
            from .gui import run  # imports tkinter, which the CLI must work without

            return run(read, _bento_path(args.window_bento) if args.window_bento else None)
        modules = _select(known, args.modules)
        path = _bento_path(args.bento)
        if args.command == "deploy":
            return deploy(modules, path, dry_run=args.dry_run, known=known)
        if args.command == "capture":
            return capture(
                modules,
                path,
                dry_run=args.dry_run,
                force=args.force,
                prune=args.prune,
                known=known,
                encrypt=args.encrypt,
                decrypt=args.decrypt,
            )
        return status(modules, path, known=known)
    except UsageError as error:
        print(f"bento: error: {error}", file=sys.stderr)
        return EXIT_USAGE
    except (ManifestError, BentoError, OSError) as error:
        print(f"bento: error: {error}", file=sys.stderr)
        return EXIT_ERROR
    except (EOFError, KeyboardInterrupt):
        print("\nbento: error: no passphrase entered", file=sys.stderr)
        return EXIT_ERROR


def deploy(
    modules: list[Module],
    path: Path,
    *,
    dry_run: bool,
    known: list[Module] | None = None,
    prompt: Prompt = getpass.getpass,
) -> int:
    contents = archive.read(path, _Passphrase(prompt, path))
    _report_bento(
        path,
        contents,
        ops.unknown_modules(contents, known) if known is not None else [],
        high_risk=risk.count(contents, modules if known is None else known),
    )
    for item in ops.plan_deploy(contents, modules):
        module = item.module
        _report(f"deploy {module.name} -> {module.target}", item.plan, dry_run=dry_run,
                ignored=item.ignored, high_risk=item.high_risk)
        if not dry_run:
            ops.apply_deploy([item])
    return EXIT_OK


def capture(
    modules: list[Module],
    path: Path,
    *,
    dry_run: bool,
    force: bool,
    prune: bool,
    known: list[Module] | None = None,
    encrypt: bool = False,
    decrypt: bool = False,
    prompt: Prompt = getpass.getpass,
) -> int:
    """Merge this machine's files into the bento; see `ops.plan_capture` for the rules."""
    if encrypt and decrypt:
        raise UsageError("--encrypt and --decrypt exclude each other")
    passphrase = _Passphrase(prompt, path)
    previous = ops.load(path, passphrase)
    plan = ops.plan_capture(
        path,
        previous,
        modules,
        known,
        allowed=ops.ALL if force else frozenset(),
        prune=prune,
        encrypt=encrypt,
        decrypt=decrypt,
    )
    _report_bento(
        path,
        previous,
        plan.unknown,
        prune=plan.pruned,
        high_risk=risk.count(previous, modules if known is None else known) if previous else 0,
    )
    for item in plan.modules:
        _report_capture(item, dry_run=dry_run)
    if plan.blocked:
        raise BentoError(plan.blocked)

    if not plan.changed:
        print("bento unchanged")
    elif dry_run:
        print("bento would be written (dry run)" + (", encrypted" if plan.bento.encrypted else ""))
    else:
        result = ops.apply_capture(
            plan, path, passphrase=passphrase, new_passphrase=lambda: _new_passphrase(prompt)
        )
        print(f"bento written: {path}" + (" (encrypted)" if plan.bento.encrypted else ""))
        if result.backup:
            print(f"  previous kept as {result.backup.name}")
        for dropped in result.dropped:
            print(f"  removed  plain backup {dropped.name}")
    return EXIT_ERROR if plan.refused else EXIT_OK


def status(
    modules: list[Module],
    path: Path,
    *,
    known: list[Module] | None = None,
    prompt: Prompt = getpass.getpass,
) -> int:
    contents = ops.load(path, _Passphrase(prompt, path))
    unknown = ops.unknown_modules(contents, known) if contents is not None and known is not None else []
    _report_bento(
        path,
        contents,
        unknown,
        high_risk=risk.count(contents, modules if known is None else known) if contents else 0,
    )
    deploys = ops.plan_deploy(contents, modules) if contents is not None else [None] * len(modules)
    for module, item in zip(modules, deploys):
        if item is None:
            print(f"deploy {module.name} -> {module.target}\n  skipped: no bento")
        else:
            _report(f"deploy {module.name} -> {module.target}", item.plan, ignored=item.ignored,
                    high_risk=item.high_risk)
        packed = contents.module(module.name) if contents is not None else {}
        selection = picks.stored(contents) if contents is not None else picks.Picks()
        _report_capture(
            ops.capture_module(module, packed, selection=selection.module(module.name))
        )
    return EXIT_OK


class _Passphrase:
    """Asks for an existing bento's passphrase on first use, then remembers it."""

    def __init__(self, prompt: Prompt, path: Path) -> None:
        self._prompt = prompt
        self._path = path
        self._value: str | None = None

    def __call__(self) -> str:
        if self._value is None:
            self._value = self._prompt(f"Passphrase for {self._path}: ")
        return self._value


def _new_passphrase(prompt: Prompt) -> str:
    first = prompt("New passphrase for the bento: ")
    if len(first) < MIN_PASSPHRASE:
        raise UsageError(f"the passphrase needs at least {MIN_PASSPHRASE} characters")
    if prompt("Repeat the passphrase: ") != first:
        raise UsageError("the passphrases do not match")
    return first


def _report_bento(
    path: Path,
    contents: Bento | None,
    unknown: Sequence[str] = (),
    *,
    prune: bool = False,
    high_risk: int = 0,
) -> None:
    if contents is None:
        print(f"bento {path}\n  not found")
        return
    when = (
        "capture time unknown"
        if contents.captured is None
        else f"captured {contents.captured:%Y-%m-%d %H:%M:%S} UTC"
    )
    flags = (", encrypted" if contents.encrypted else "") + (
        f", {high_risk} high-risk file{'s' * (high_risk != 1)}" if high_risk else ""
    )
    print(f"bento {path} ({when}{flags})")
    for warning in contents.warnings:
        print(f"  warning  {warning}")
    for name in unknown:
        if prune:
            print(f"  removed  module {name}")
        else:
            print(f"  unknown module  {name}  (not in bento.toml; capture --prune drops it)")


def _report_capture(item: ops.CaptureModule, *, dry_run: bool = False) -> None:
    header = f"capture {item.module.name} <- {item.module.target}"
    if item.skipped:
        print(f"{header}\n  skipped: target directory does not exist")
    elif item.refused:
        print(f"{header}\n  refused  module holds secrets; a plain bento can't take it"
              " (capture --encrypt)")
    else:
        _report(
            header,
            item.plan,
            dry_run=dry_run,
            findings=item.findings,
            allowed=item.allowed,
            removed=item.removed,
            only_in_bento=item.only_in_bento,
            excluded=len(item.excluded),
            high_risk=item.high_risk,
        )


def _report(
    header: str,
    plan: sync.Plan,
    *,
    dry_run: bool = False,
    findings: Mapping[str, Sequence[guards.Finding]] | None = None,
    allowed: Container[str] = (),
    removed: Sequence[str] = (),
    only_in_bento: Sequence[str] = (),
    ignored: Sequence[str] = (),
    excluded: int = 0,
    high_risk: Mapping[str, str] | None = None,
) -> None:
    def note(path: str) -> str:
        return risk.suffix((high_risk or {}).get(path))

    print(header + (" (dry run)" if dry_run else ""))
    for path in plan.added:
        print(f"  added    {path}{note(path)}")
    for path in plan.updated:
        print(f"  updated  {path}{note(path)}")
    for path in removed:
        print(f"  removed  {path}{note(path)}")
    for path, found in (findings or {}).items():
        label = "warning " if path in allowed else "refused "
        for finding in found:
            print(f"  {label} {path}:{finding.line}  {finding.kind}: {finding.excerpt}")
    for path in only_in_bento:
        print(f"  only in bento  {path}{note(path)}")
    for path in ignored:
        print(f"  ignored  {path}  (in the bento, not managed by bento.toml){note(path)}")
    if excluded:
        print(f"  excluded by selection: {excluded}")
    print(f"  {len(plan.unchanged)} unchanged")


def _select(known: list[Module], names: list[str]) -> list[Module]:
    if not names:
        return known
    by_name = {module.name: module for module in known}
    unknown = [name for name in names if name not in by_name]
    if unknown:
        available = ", ".join(by_name) or "none"
        raise UsageError(f"unknown module(s): {', '.join(unknown)} (available: {available})")
    return [by_name[name] for name in dict.fromkeys(names)]


def _bento_path(raw: str | None) -> Path:
    if raw is None:
        return archive.DEFAULT_PATH
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _expand(word: str) -> str:
    """The command `word` is a unique prefix of; otherwise `word`, for argparse to reject."""
    matches = [name for name in COMMANDS if name.startswith(word)]
    return matches[0] if len(matches) == 1 else word


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="bento",
        description="Deploy and capture personal configuration. Without a command, open the window"
        " to choose, capture and deploy.",
    )
    parser.add_argument(
        "--bento", dest="window_bento", metavar="PATH",
        help="without a command: the bento to open in the window (default: the one opened last)",
    )
    commands = parser.add_subparsers(dest="command", metavar="command")
    for name, help_text in (
        ("deploy", "copy configuration from the bento onto this machine"),
        ("capture", "merge this machine's configuration into the bento"),
        ("status", "show what deploy and capture would change; writes nothing"),
    ):
        command = commands.add_parser(name, help=help_text, description=help_text)
        command.set_defaults(parser=command)
        command.add_argument("modules", nargs="*", metavar="module", help="default: all modules")
        command.add_argument(
            "--bento", metavar="PATH", help=f"the bento to use (default: {archive.DEFAULT_PATH})"
        )
        if name != "status":
            command.add_argument(
                "--dry-run", action="store_true", help="report changes without writing"
            )
        if name == "capture":
            command.add_argument(
                "--force", action="store_true", help="capture files flagged by the guards"
            )
            command.add_argument(
                "--prune",
                action="store_true",
                help="drop bento entries that are no longer on this machine or no longer managed",
            )
            encryption = command.add_mutually_exclusive_group()
            encryption.add_argument(
                "--encrypt",
                action="store_true",
                help="encrypt the bento with a new passphrase (also changes the passphrase)",
            )
            encryption.add_argument(
                "--decrypt", action="store_true", help="store the bento unencrypted again"
            )
    # no help= keeps `help` out of the command list; `-h` is the documented way
    help_command = commands.add_parser("help")
    help_command.set_defaults(parser=parser)
    help_command.add_argument("topic", nargs="?")
    return parser
