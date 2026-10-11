"""Command line: `python -m lunchbox {deploy,capture,status} [module ...]`."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import guards, sync
from .manifest import ManifestError, Module, discover
from .selection import select

EXIT_OK, EXIT_ERROR, EXIT_USAGE = 0, 1, 2


class UsageError(Exception):
    pass


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = _parser().parse_args(argv)
    try:
        modules = _resolve(args.modules)
        if args.command == "deploy":
            return deploy(modules, dry_run=args.dry_run)
        if args.command == "capture":
            return capture(modules, dry_run=args.dry_run, force=args.force)
        return status(modules)
    except UsageError as error:
        print(f"lunchbox: error: {error}", file=sys.stderr)
        return EXIT_USAGE
    except (ManifestError, OSError) as error:
        print(f"lunchbox: error: {error}", file=sys.stderr)
        return EXIT_ERROR


def deploy(modules: list[Module], *, dry_run: bool) -> int:
    for module in modules:
        plan = _deploy_plan(module)
        _report(f"deploy {module.name} -> {module.target}", plan, dry_run=dry_run)
        if not dry_run:
            module.target.mkdir(parents=True, exist_ok=True)
            sync.apply(plan, executable_scripts=True)
    return EXIT_OK


def capture(modules: list[Module], *, dry_run: bool, force: bool) -> int:
    refused = False
    for module in modules:
        header = f"capture {module.name} <- {module.target}"
        if not module.target.is_dir():
            print(f"{header}\n  skipped: target directory does not exist")
            continue
        plan = _capture_plan(module)
        findings = _scan(plan)
        if not force:
            plan = plan.without(findings)
            refused = refused or bool(findings)
        _report(header, plan, dry_run=dry_run, findings=findings, refused=not force)
        if not dry_run:
            sync.apply(plan)
    return EXIT_ERROR if refused else EXIT_OK


def status(modules: list[Module]) -> int:
    for module in modules:
        _report(f"deploy {module.name} -> {module.target}", _deploy_plan(module))
        header = f"capture {module.name} <- {module.target}"
        if not module.target.is_dir():
            print(f"{header}\n  skipped: target directory does not exist")
            continue
        plan = _capture_plan(module)
        findings = _scan(plan)
        _report(header, plan.without(findings), findings=findings, refused=True)
    return EXIT_OK


def _deploy_plan(module: Module) -> sync.Plan:
    paths = select(module.files, module.include, module.exclude)
    return sync.plan(module.files, module.target, paths)


def _capture_plan(module: Module) -> sync.Plan:
    paths = select(module.target, module.include, module.exclude)
    return sync.plan(module.target, module.files, paths)


def _scan(plan: sync.Plan) -> dict[str, list[guards.Finding]]:
    findings = {}
    for path in plan.changes:
        found = guards.scan(path, (plan.source / path).read_bytes())
        if found:
            findings[path] = found
    return findings


def _report(
    header: str,
    plan: sync.Plan,
    *,
    dry_run: bool = False,
    findings: dict[str, list[guards.Finding]] | None = None,
    refused: bool = False,
) -> None:
    print(header + (" (dry run)" if dry_run else ""))
    for path in plan.added:
        print(f"  added    {path}")
    for path in plan.updated:
        print(f"  updated  {path}")
    label = "refused " if refused else "warning "
    for path, found in (findings or {}).items():
        for finding in found:
            print(f"  {label} {path}:{finding.line}  {finding.kind}: {finding.excerpt}")
    print(f"  {len(plan.unchanged)} unchanged")


def _resolve(names: list[str]) -> list[Module]:
    modules = discover()
    if not names:
        return modules
    by_name = {module.name: module for module in modules}
    unknown = [name for name in names if name not in by_name]
    if unknown:
        available = ", ".join(by_name) or "none"
        raise UsageError(f"unknown module(s): {', '.join(unknown)} (available: {available})")
    return [by_name[name] for name in dict.fromkeys(names)]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lunchbox", description="Deploy and capture personal configuration."
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")
    for name, help_text in (
        ("deploy", "copy configuration from the repo onto this machine"),
        ("capture", "copy this machine's configuration into the repo"),
        ("status", "show what deploy and capture would change; writes nothing"),
    ):
        command = commands.add_parser(name, help=help_text, description=help_text)
        command.add_argument("modules", nargs="*", metavar="module", help="default: all modules")
        if name != "status":
            command.add_argument(
                "--dry-run", action="store_true", help="report changes without writing"
            )
        if name == "capture":
            command.add_argument(
                "--force", action="store_true", help="capture files flagged by the guards"
            )
    return parser
