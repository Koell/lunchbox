#!/usr/bin/env bash
# Internal runner for the cli/*.sh entry scripts: finds Python 3.11+ and runs
# `python -m lunchbox <command> [args...]` from the repo root.
# Usage: _run.sh <command> [args...]

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd) || exit 1

found=""
for candidate in python3 python; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    # Always probe with arguments: the Microsoft Store stub opens the Store when
    # run without any, and fails (exit 9009) when run with them.
    version=$("$candidate" -c 'import sys; sys.stdout.write("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null) || continue
    case $version in
        "" | *[!0-9.]*) continue ;;
    esac
    major=${version%%.*}
    minor=${version#*.}
    minor=${minor%%.*}
    if [ "$major" -gt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -ge 11 ]; }; then
        cd -- "$root" || exit 1
        exec "$candidate" -m lunchbox "$@"
    fi
    found="${found:+$found, }$candidate $version"
done

echo "lunchbox: Python 3.11+ required, found: ${found:-none}" >&2
[ -n "$found" ] && exit 1
exit 127
