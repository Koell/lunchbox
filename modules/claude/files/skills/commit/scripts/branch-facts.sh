#!/usr/bin/env bash
# Read-only facts about the current branch, for writing a commit message.
#
# Nothing here alters history, the index, the working tree or a remote.
#
# Usage: branch-facts.sh [base-ref]
#   base-ref defaults to the nearest long-lived REMOTE branch — see resolve_base. Pass one explicitly
#   when the guess is wrong; the candidate table is printed so it can be checked.

set -uo pipefail

if ! command -v git >/dev/null 2>&1; then
  echo "branch-facts: git not found." >&2
  exit 2
fi

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "branch-facts: not inside a git repo." >&2
  exit 2
}
cd "$REPO_ROOT"

BASE=""
BASE_TABLE=""

# 🔴 The default branch is NOT enough to identify the base: branches are cut from `staging` as well as
# from the default one, and origin/HEAD only ever names the latter. Both repos this is used in carry a
# staging branch, and they do not even agree on the default (one uses main, the other master).
#
# Normal development cuts branches from the default; hotfixes are cut from staging, and a project that
# has stopped developing is hotfix-only, so staging is then the base for everything.
#
# So: score every long-lived candidate by how many commits HEAD has that it does not, and take the
# smallest — the nearest ancestor, which is the branch this one was actually cut from. A base further
# back would fold in commits that are already merged and are not this branch's work. Ties go to the
# default branch, which is listed first.
#
# 🔴 REMOTE REFS WIN OVER LOCAL ONES: a local branch is never picked while any origin/* resolves. A
# local `main` is only as fresh as the last fetch, and a stale one scores a LARGE count — which under
# "smallest non-zero" reads as a legitimate base rather than as the stale ref it is. Seen for real on
# 2026-09-15: HEAD equalled origin/main (count 0, discarded for being zero) while local main sat 69
# behind (count 69, picked), so this script offered fifty other people's already-merged commits as the
# branch's own work. Locals are still printed — they are evidence the checkout is behind — never chosen.
#
# 🔴 A remote scoring ZERO is the nearest base, not a miss: HEAD is already contained in that ref, so
# this branch's work is whatever is uncommitted. Seen on 2026-10-05: a fresh hotfix branch sat on
# origin/staging (count 0), the zero was discarded, and origin/master (count 13) offered thirteen
# other tickets as the branch's own work.
resolve_base() {
  if [ "$#" -gt 0 ] && [ -n "${1:-}" ]; then
    if git rev-parse --verify --quiet "$1" >/dev/null; then
      BASE="$1"
      BASE_TABLE="  ${1} (given explicitly)"
      return 0
    fi
    echo "branch-facts: base ref '$1' does not resolve." >&2
    return 1
  fi

  local default_branch=""
  default_branch="$(git symbolic-ref --quiet refs/remotes/origin/HEAD 2>/dev/null)"
  default_branch="${default_branch#refs/remotes/}"

  local candidates=()
  [ -n "$default_branch" ] && candidates+=("$default_branch")
  candidates+=(origin/main origin/master origin/staging origin/develop main master staging)

  local seen=" " candidate count note is_remote best_count=-1
  local local_best="" local_best_count=-1
  for candidate in "${candidates[@]}"; do
    case "$seen" in *" ${candidate} "*) continue ;; esac
    git rev-parse --verify --quiet "$candidate" >/dev/null || continue
    seen="${seen}${candidate} "

    count="$(git rev-list --count "${candidate}..HEAD" 2>/dev/null)" || continue

    is_remote=0
    case "$candidate" in origin/*) is_remote=1 ;; esac
    note=""

    if [ "$is_remote" -eq 1 ]; then
      if [ "$best_count" -lt 0 ] || [ "$count" -lt "$best_count" ]; then
        best_count="$count"
        BASE="$candidate"
      fi
    else
      note="  (local - skipped while an origin/* ref resolves)"
      if [ "$count" -gt 0 ] && { [ "$local_best_count" -lt 0 ] || [ "$count" -lt "$local_best_count" ]; }; then
        local_best_count="$count"
        local_best="$candidate"
      fi
    fi

    BASE_TABLE="${BASE_TABLE}$(printf '
  %-24s HEAD is ahead by %-6s%s' "$candidate" "$count" "$note")"
  done

  # No origin/* resolved at all - a repo with no remote, or one never fetched. Locals are all there is.
  [ -z "$BASE" ] && BASE="$local_best"
  if [ -z "$BASE" ]; then
    BASE="$(printf '%s' "$seen" | awk '{print $1}')"
  fi

  if [ -z "$BASE" ]; then
    echo "branch-facts: no base branch resolves (tried origin/HEAD, main, master, staging, develop)." >&2
    return 1
  fi

  BASE_TABLE="${BASE_TABLE#$'\n'}"
  return 0
}

resolve_base "${1:-}" || exit 2
BRANCH="$(git rev-parse --abbrev-ref HEAD)"

# The ticket the BRANCH claims, and the ticket(s) its commits claim. When they disagree, the branch
# name is usually right and the commit subject is the typo — report both and let the caller judge.
BRANCH_TICKET="$(printf '%s' "$BRANCH" | grep -oE '[A-Z]+-[0-9]+' | head -1)"
COMMIT_TICKETS="$(git log --format='%s' "${BASE}..HEAD" | grep -oE '[A-Z]+-[0-9]+' | sort -u | tr '\n' ' ')"

echo "── branch ──"
echo "branch:           ${BRANCH}"
echo "base:             ${BASE}"
echo "ticket (branch):  ${BRANCH_TICKET:-none found in branch name}"
echo "ticket (commits): ${COMMIT_TICKETS:-none}"
if [ -n "$BRANCH_TICKET" ] && [ -n "$COMMIT_TICKETS" ] && \
   ! printf '%s' " $COMMIT_TICKETS" | grep -q " ${BRANCH_TICKET} "; then
  echo "⚠️  MISMATCH: no commit subject mentions ${BRANCH_TICKET} — say so when reporting."
fi

echo
echo "── base candidates (nearest origin/* wins, ties to the default; pass one as \$1 to override) ──"
echo "${BASE_TABLE}"

echo
echo "── commits on this branch (${BASE}..HEAD) ──"
git log --format='%h %an %x7c %s' "${BASE}..HEAD" || true
[ -z "$(git log --format='%h' "${BASE}..HEAD")" ] && echo "(none — the work is uncommitted)"

echo
echo "── committed changes (${BASE}...HEAD) ──"
git diff --stat "${BASE}...HEAD" || true

echo
echo "── uncommitted ──"
if [ -n "$(git status --porcelain)" ]; then
  git status --short
  echo
  git diff --stat HEAD
else
  echo "(clean)"
fi

echo
echo "── next ──"
echo "Read the diffs before writing. Whole branch incl. uncommitted:"
echo "  git diff ${BASE} -- <path>"
exit 0