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
git-subrepo-squash squash path/to/subrepo \
  --base origin/main \
  --head HEAD \
  --output /tmp/subrepo.patch \
  --stat
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

When no changes are found for the selected subrepo path, the command exits
successfully and prints a notice to stderr (unless `--quiet` is used).

