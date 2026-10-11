---
name: commit
description: Writes a short conventional-commit-style message summarising the current branch into .ai/commit. Use when the user asks for a commit message, runs /commit, or has finished a branch and is about to commit it. Does not run git commit.
---

# Commit message

Summarise the whole branch — every commit on it plus any uncommitted work — as one short message in
`.ai/commit`. Write the file and stop; the user commits.

## Workflow

1. **Gather the facts**: `bash <skill-dir>/scripts/branch-facts.sh`
   ⚠️ **Check the base it picked.** Normal work is cut from the default branch (`main` in one
   repo, `master` in another), hotfixes from `staging` — and a project that has stopped developing is
   hotfix-only. The script scores every candidate and takes the nearest **`origin/*`** one, ties going
   to the default, printing the table it chose from. If the pick is wrong the whole summary is wrong,
   so re-run with the right one: `branch-facts.sh origin/staging`.
   🔴 **A local branch is never the base while an `origin/*` ref resolves**, and the table marks the
   ones it skipped. A local `main` is only as fresh as the last fetch, and a stale one looks exactly
   like a legitimate base — it once offered fifty already-merged commits as the branch's own work.
   ⚠️ **`HEAD is ahead by 0` against a remote is the nearest base, not a miss**: the branch has
   nothing committed of its own, so the message covers the uncommitted work alone.
2. **Read the diff, not the commit subjects.** Subjects on a working branch are often wrong (a branch
   named for one ticket committed under another). The diff is what happened.
3. **Pick the type** from the dominant change (table below).
4. **Write `.ai/commit`**, overwriting it. Create `.ai/` if it does not exist.
5. **Report**: print the subject line, hand over `git commit -F .ai/commit`. Say so if the branch name
   and the existing commit subjects disagree about the ticket id.

🔴 **Never run a git command that writes** — no `add`, `commit`, `stash`, nothing. The file is the
deliverable.

## Format

```
<type>: <TICKET> <summary>

- <verb>
    - <what it covers>
        - <detail>
- <verb> <a change small enough to state in one line>
```

- 🔴 **Short — shorter than feels complete. But short means fewer WORDS, not fewer bullets.** The
  grouped shape — a bare verb with its changes nested under it — is the **normal** one, because the
  bullets are what make a message scannable. Flattening to prose-ish one-liners to save lines is the
  wrong economy: **cut content, never structure.**
- 🔴 **A verb never appears twice at the top level.** Two `- add …` lines are one `- add` with two
  sub-bullets. Group first, then shorten what is inside the group.
- **What to cut**, in order: measurements and counts (how many files, sites, specs, tests), the
  reasoning behind a fix, how it was verified, and anything a reader can see in the diff. No lead
  paragraph, no closing summary, no "still open" section. The message says *what changed*, and the
  branch's own documentation says the rest.
- **Every bullet opens with a verb.** The group verbs are **add**, **remove**, **fix**, **change**,
  **improve** — all five behave the same way. `fix` and `remove` are group verbs exactly as much as
  `add` is; nothing about them makes them belong inline.
- Each bullet is one of two shapes, never a mix:
    - **bare verb, detail nested** — `- fix` on its own line, everything else below it
    - **verb plus the whole change on one line** — `- improve error log for dynamic list`

  A verb followed by a sentence *and* sub-bullets is the mistake to avoid. If a `fix` or a `remove`
  needs explaining, make it a bare `- fix` / `- remove` and move the sentence down a level.
- **Nesting goes up to three levels**, 4 spaces per level: the verb, what it covers, then the
  specifics. Use the third level only when the second would otherwise carry a list.
- Concrete, with real identifiers. Skip the mechanism unless the mechanism *is* the change.
- Subject: no trailing period. Ticket id comes from the branch name.
- Wrap at ~100 columns.
- **No trailers.** No `Co-Authored-By`, no `Signed-off-by`, no generated-with line, no
  `BREAKING CHANGE:` footer — this house style has never used one.

## Type

| type | when |
|---|---|
| `fix:` | a defect a user could hit |
| `feat:` | a new user-visible capability |
| `refactor:` | restructuring, removal, migration — no new behaviour |
| `chore:` / `docs:` | tooling or docs only |

A branch that fixes defects on the way to a restructuring is still `refactor:`. No scope parens.

## Examples

Grouped, when several changes share a verb:

```
refactor: PROJ-1234 remove legacy v1 REST and export API

- remove
    - rest-api-v1 docs and dead /api/rest/v1.0 mappings (orders, getInvoices)
    - export API (v1+v2) controllers, ExportOrdersJob, OrderExportService, ExportJournal
    - unused Booking v1 API (BookingController); keep BookingService/domain used by checkout
- drop dead apiV2 v2.0 mapping and loginCheck entries; keep v2.5 REST login infra
```

Three levels, when a verb covers changes that each need their own detail — and note that `fix` and
`remove` head their groups the same way `remove` does above:

```
fix: PROJ-2345 load report charts asynchronously

- fix
    - the six report detail screens blocked on the metrics service before rendering
        - add ajaxGetReportChart on AbstractReportController, ArchivedReportController,
          UserReportController, TeamReportController and InvoiceReportController
        - fetch the series from includes/reportChart after the page paints
    - the latency/throughput chart had never drawn: ReportService answered latencyGraph and
      throughputGraph where the view reads latencyData and throughputData
- remove
    - dead loads from includes/reportChart: chartCurve.js, duplicate barRenderer, colorbox
    - the back button from includes/reportChart; four of the six screens print their own
- add defer to the eleven jqplot script tags, which blocked paint mid-body
```

Flat, when each change stands alone — the exception, not the target:

```
refactor: PROJ-3456 Product, Category, Supplier Controller

- change product and supplier list to dynamic list
- improve error log for dynamic list
```