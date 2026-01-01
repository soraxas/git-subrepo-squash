## git-subrepo-squash

Generate a single patch containing all changes made to a vendored `git subrepo`
directory so you can send them upstream without rewriting your main repository's
history.

### Install

```bash
pip install .
# or for development
pip install -e .
```

### Usage

```
git-subrepo-squash
git-subrepo-squash status --log-count 200
git-subrepo-squash squash path/to/subrepo \
  --base origin/main \
  --head HEAD \
  --output /tmp/subrepo.patch \
  --stat
git-subrepo-squash squash-commit path/to/subrepo [path/to/other] --target <target-sha>
```

Arguments:

- `path` – directory of the vendored subrepo relative to the repo root.
- `--base` – reference to diff against (default: `origin/main`).
- `--head` – reference containing your changes (default: `HEAD`).
- `--output` – optional patch file path. If omitted, the patch prints to stdout.
- `--stat` – show a diffstat after generating the patch.
- `--allow-dirty` – skip the clean working tree check.
- `--repo` – path anywhere inside the repository (defaults to `.`).
- `--quiet` – hide informational messages.

Running `git-subrepo-squash` (or `git-subrepo-squash status`) prints an ASCII
tree of subrepo paths discovered via `git subrepo status`, along with recent
commit history DAG for the main repo (controlled by `--log-count`). The history
stops once all pull parents are shown and tags commits that match a pull parent.
ANSI color is enabled when output is a TTY (or when `NO_COLOR` is unset).
Output uses your configured pager (`$PAGER`, defaults to `less -FRSX`) when stdout is a TTY.
Use `--no-pager` to disable it or `--pager` to force it.

`squash-commit` rewrites the subrepo `.gitrepo` parent to an earlier commit after
verifying that no non-`.gitrepo` changes occurred under the subrepo path between
the target commit and the current parent. Multiple subrepo paths may be supplied.

When no changes are found for the selected subrepo path, the command exits
successfully and prints a notice to stderr (unless `--quiet` is used).
