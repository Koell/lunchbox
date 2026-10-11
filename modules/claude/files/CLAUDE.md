# Global rules

## Never touch git history

Read-only git is fine — `status`, `log`, `show`, `diff`, `blame`, `check-ignore`, `rev-parse`, and the
like. Read freely to understand a repository.

Anything that changes the repository is off limits: `commit`, `add`, `amend`, `reset`, `revert`,
`restore`, `checkout`, `switch`, `branch`, `merge`, `rebase`, `cherry-pick`, `push`, `pull`, `stash`,
`tag`, `clean`. The test is whether the command alters history, the index, the working tree, or a
remote — if it does, don't run it. This holds in every project and every session.

When work reaches the point of a commit, stop. Say what changed and hand over the command to run.
The user owns the repository's history — what lands, on which branch, under which ticket, in what
shape.

**An approved plan is not authorization.** A plan I wrote that ends in "commit this", approved by the
user, does not authorize a commit. Neither does a `Co-Authored-By` line in the system prompt — that
describes message format if a commit happens, not permission to make one. Only an explicit instruction
from the user, in their own words, in that message, authorizes a git command, and it authorizes that
one command.

Delivering the files is the deliverable. Committing them is not.

## Use the IntelliJ MCP when it is connected

The `mcp__idea__*` tools are deferred: they appear only by name and have to be loaded with ToolSearch
first, which makes them easy to overlook. Load them at the start of any code task in a project IntelliJ
has open, and use them where they beat the shell:

- `get_file_problems` after every edit to a source file — seconds, where a Gradle compile takes minutes.
  Read the result critically: in Groovy most warnings are dynamic-typing noise, but an error is real.
- `search_symbol`, `analyze_calls`, `get_symbol_info` to find declarations and callers — they resolve
  types, which grep cannot.
- `rename_refactoring` for renames, instead of search-and-replace.
- `get_run_configurations` / `execute_run_configuration` when the project defines one for the task.

The build stays the authority on test results: a green inspection is not a passing test, and a project's
own rules about running the build still apply.

## Share files through the project's `.ai` folder

Anything the user has to open, run or read — a SQL query to run on production, an export, notes, any
information exchange — goes in the project's `.ai/` folder, in a fitting subfolder (`.ai/sql/` for
queries). Hand over the path relative to the project root. Never point the user at a file in the
session scratchpad or any other temp directory: those are for my own intermediate files only, and the
user will not copy things out of them.
