## git-subrepo-squash

Inspect git-subrepo metadata and safely move the recorded parent commit for a
vendored subrepo.

### Install

```bash
uv tool install .
```

### Usage

```
git-subrepo-squash
git-subrepo-squash status --log-count 200
git-subrepo-squash squash path/to/subrepo [path/to/other] --target <target-sha>
```

Arguments:

- `paths` – one or more subrepo directories relative to the repo root.
- `--target` – target commit SHA to move the subrepo parent to.
- `--allow-dirty` – skip the clean working tree check.
- `--repo` – path anywhere inside the repository (defaults to `.`).
- `--log-count` – maximum number of commits to show in the history DAG (default: 200).
- `--pager` – force pager output (uses `$PAGER`).
- `--no-pager` – disable the pager even when output is a TTY.

Running `git-subrepo-squash` (or `git-subrepo-squash status`) prints an ASCII
tree of subrepo paths discovered via `git subrepo status`, along with each
subrepo's recorded head commit and a recent commit history DAG for the main repo
(controlled by `--log-count`). The history stops once all pull parents are shown
and tags commits that match a pull parent. ANSI color is enabled when output is
a TTY (or when `NO_COLOR` is unset). Output uses your configured pager
(`$PAGER`, defaults to `less -FRSX`) when stdout is a TTY. Use `--no-pager` to
disable it or `--pager` to force it.

`squash` rewrites the subrepo `.gitrepo` parent to an earlier commit after
verifying that no non-`.gitrepo` changes occurred under the subrepo path between
the target commit and the current parent. Multiple subrepo paths may be supplied.

When no changes are found for the selected subrepo path, the command exits
successfully and prints a notice to stderr (unless `--quiet` is used).
