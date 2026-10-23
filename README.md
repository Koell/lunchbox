# lunchbox

A collection of my personal scripts and tools, grouped by what they do. Each tool lives in its own
folder with its own README, and needs nothing from the others.

| Tool                      | What it does                                                         |
|---------------------------|----------------------------------------------------------------------|
| [bento](backup/bento)     | Packs my configuration into a private archive and unpacks it on every machine (Python). |

## Layout

```text
backup/
  bento/          configuration deploy and capture; see its README
.github/workflows/
  bento.yml       CI for bento, runs only when backup/bento changes
```

A new tool goes into the folder for its purpose (or a new one), gets its own README, and, if it has
tests, its own workflow filtered to its folder.
