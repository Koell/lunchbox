# lunchbox

A collection of my personal scripts and tools, grouped by what they do. Each tool lives in its own
folder with its own README, and needs nothing from the others.

| Tool                      | What it does                                                         |
|---------------------------|----------------------------------------------------------------------|
| [bento](backup/bento)     | Packs my configuration into a private archive and unpacks it on every machine (Python). |
| [omnifetch](git/omnifetch) | Fetches all my git repositories at once, picked in a checkbox tree (Python). |
| [breakglass](emergency/breakglass) | In an emergency, pushes all unsaved git work to the servers in one click (Python). |

## Layout

```text
backup/
  bento/          configuration deploy and capture; see its README
emergency/
  breakglass/     emergency push of all unsaved git work; see its README
git/
  omnifetch/      fetch many repositories at once; see its README
.github/workflows/
  bento.yml       CI for bento, runs only when backup/bento changes
  breakglass.yml  CI for breakglass, runs only when emergency/breakglass changes
  omnifetch.yml   CI for omnifetch, runs only when git/omnifetch changes
```

A new tool goes into the folder for its purpose (or a new one), gets its own README, and, if it has
tests, its own workflow filtered to its folder.
