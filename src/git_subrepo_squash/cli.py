from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from git import GitCommandError, InvalidGitRepositoryError, Repo


class SquashError(Exception):
    """Raised when the CLI cannot complete the requested operation."""


@dataclass(frozen=True)
class DiffResult:
    patch: str
    stat: str | None
    rel_path: Path
    base: str
    head: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="git-subrepo-squash",
        description="Generate a single patch for changes inside a vendored git subrepo directory.",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="Path inside the repository. Defaults to the current working directory.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    squash = subparsers.add_parser(
        "squash",
        help="Collect all changes for a subrepo path between two refs into one patch.",
    )
    squash.add_argument(
        "path",
        type=Path,
        help="Path to the subrepo directory (relative to the repository root).",
    )
    squash.add_argument(
        "--base",
        default="origin/main",
        help="Base ref to diff against. Defaults to origin/main.",
    )
    squash.add_argument(
        "--head",
        default="HEAD",
        help="Ref containing your local changes. Defaults to HEAD.",
    )
    squash.add_argument(
        "--output",
        type=Path,
        help="Optional file to write the patch to. Prints to stdout when omitted.",
    )
    squash.add_argument(
        "--stat",
        action="store_true",
        help="Also print a diffstat after generating the patch.",
    )
    squash.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Permit running with uncommitted changes in the working tree.",
    )
    squash.add_argument(
        "--quiet",
        action="store_true",
        help="Silence informational messages (patch output is never suppressed).",
    )
    return parser


def find_repo(path: Path) -> Repo:
    try:
        return Repo(path, search_parent_directories=True)
    except InvalidGitRepositoryError as exc:
        raise SquashError(f"{path} is not inside a git repository") from exc


def ensure_clean(repo: Repo, allow_dirty: bool) -> None:
    if allow_dirty:
        return
    if repo.is_dirty(untracked_files=True):
        raise SquashError(
            "Working tree has uncommitted changes. Commit, stash, or rerun with --allow-dirty."
        )


def resolve_subrepo_path(repo: Repo, subrepo_path: Path) -> Path:
    root = Path(repo.working_tree_dir).resolve()
    target = (root / subrepo_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise SquashError("Subrepo path must stay inside the repository root.") from exc
    if not target.exists():
        raise SquashError(f"Subrepo path does not exist: {target}")
    return target


def collect_diff(repo: Repo, rel_path: Path, base: str, head: str) -> DiffResult:
    try:
        patch = repo.git.diff(f"{base}..{head}", "--", rel_path.as_posix())
        stat = repo.git.diff("--stat", f"{base}..{head}", "--", rel_path.as_posix())
    except GitCommandError as exc:
        raise SquashError(f"git diff failed: {exc}") from exc
    return DiffResult(patch=patch, stat=stat, rel_path=rel_path, base=base, head=head)


def run_squash(args: argparse.Namespace) -> int:
    repo = find_repo(args.repo)
    ensure_clean(repo, allow_dirty=args.allow_dirty)

    target = resolve_subrepo_path(repo, args.path)
    rel_path = target.relative_to(Path(repo.working_tree_dir).resolve())

    diff = collect_diff(repo, rel_path, base=args.base, head=args.head)

    if not diff.patch.strip():
        if not args.quiet:
            print(
                f"No changes detected in {rel_path} between {args.base}..{args.head}.",
                file=sys.stderr,
            )
        return 0

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        content = diff.patch
        if content and not content.endswith("\n"):
            content += "\n"
        args.output.write_text(content)
        if not args.quiet:
            print(f"Wrote patch for {rel_path} to {args.output}")
    else:
        sys.stdout.write(diff.patch)
        if diff.patch and not diff.patch.endswith("\n"):
            sys.stdout.write("\n")

    if args.stat:
        print("\nDiffstat:")
        print(diff.stat or " (no file-level changes detected)")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "squash":
            return run_squash(args)
        parser.error(f"Unknown command: {args.command!r}")
    except SquashError as exc:
        parser.exit(status=1, message=f"error: {exc}\n")

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
