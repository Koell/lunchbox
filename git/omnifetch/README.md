# omnifetch

Fetch all my git repositories at once, in a window.

omnifetch finds every repository below a folder, shows them in a checkbox tree, and runs
`git fetch --all` on the ticked ones. It only fetches: working trees, the index and local branches
are never touched, so it shows what is new on the remotes without taking any of it in.

## Prerequisites

- Python 3.11 or newer. Nothing else: no `pip install`, no virtualenv.
- Git. On Windows, Git for Windows: omnifetch uses its ssh, ssh-agent and ssh-add.
- Tk. It ships with Python on Windows and macOS; on Linux install `tk` (Manjaro, Arch) or
  `python3-tk` (Debian, Ubuntu).

## Usage

From this folder, `git/omnifetch` in the lunchbox repository:

```sh
python -m omnifetch                             # the project folder chosen last
python -m omnifetch --path ~/work --depth 1     # another folder, only its direct subfolders
```

The first start asks for the folder that holds your projects. It is remembered in
`local/gui.json`, which is never committed, and **Change…** in the window picks another one.

| Option        | What it does                                                          |
|---------------|-----------------------------------------------------------------------|
| `--path DIR`  | Folder to look for repositories in; wins over the remembered one and is remembered in its place. |
| `--depth N`   | How many folder levels below the folder to search; default all.       |

Folders such as `node_modules`, `target`, `build` and hidden folders are not searched, and nothing
inside a repository counts as a repository of its own.

## The window

- **Projects**: the folder omnifetch looks in; **Change…** picks another one.
- **Tree**: every repository below the folder, with its branch, remote host and how far the branch
  is behind its upstream. A folder holding only one subfolder shows as one row
  (`TextAdventure/Git`). Ticking a folder ticks everything below it.
- **Ticks** are remembered per folder in `local/gui.json`, so each folder keeps its own.
- **Fetch** fetches the ticked repositories in parallel. Each row's status follows live: up to
  date, *N behind*, failed or skipped; folders count the failed and behind ones below them.
- **Prune deleted remote branches** also removes `origin/...` branches that are gone on the server.
- **Details**: the selected repository's path, remotes, SSH key and git's output of the last fetch.

## SSH keys with a passphrase

When a ticked repository uses an SSH key with a passphrase, Fetch asks for it first, once per key.
The passphrase is never saved: it unlocks the key into a private ssh-agent that only this fetch
uses, and that agent is stopped when the fetch ends or the window closes (also when Python
crashes). The next fetch asks again. **Skip these** fetches everything else and marks the
repositories that need the key as skipped.

## Development

```sh
python -m unittest -v
```

The tests use throwaway local repositories and a throwaway SSH key; nothing goes over the network.
The window tests skip without a display unless `OMNIFETCH_GUI_TESTS=required`, as in CI
(`.github/workflows/omnifetch.yml`).
