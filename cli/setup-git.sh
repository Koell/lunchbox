#!/usr/bin/env bash
# One-time setup for the git module: include the shared aliases from the global git config.
# Safe to run again; other include.path entries are left alone.

if [ -n "${XDG_CONFIG_HOME:-}" ]; then
    path="$XDG_CONFIG_HOME/git/aliases"
else
    path="~/.config/git/aliases"
fi

command -v git >/dev/null 2>&1 || { echo "lunchbox: git not found" >&2; exit 127; }

if git config --global --get-all include.path | grep -Fxq -- "$path"; then
    echo "include.path already set: $path"
else
    git config --global --add include.path "$path" || exit
    echo "added include.path: $path"
fi

file="${path/#\~/$HOME}"
if [ ! -f "$file" ]; then
    echo "note: $file does not exist yet; run cli/deploy.sh git"
fi
