from __future__ import annotations

import argparse
import os
import shlex
import re
import subprocess
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


@dataclass(frozen=True)
class SubrepoStatus:
    path: Path
    remote: str | None = None
    branch: str | None = None
    commit: str | None = None
    parent: str | None = None


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

    subparsers = parser.add_subparsers(dest="command")

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

    status = subparsers.add_parser(
        "status",
        help="Show subrepo paths and recent history as a tree.",
    )
    status.add_argument(
        "--log-count",
        type=int,
        default=200,
        help=(
            "Maximum number of commits to show in the history DAG (default: 200). "
            "Output stops early once all pull parents are found."
        ),
    )
    status.add_argument(
        "--pager",
        action="store_true",
        help="Force pager output (uses the PAGER environment variable).",
    )
    status.add_argument(
        "--no-pager",
        action="store_true",
        help="Disable the pager even when output is a TTY.",
    )

    squash_commit = subparsers.add_parser(
        "squash-commit",
        help="Rewrite a subrepo .gitrepo parent to an earlier commit after validation.",
    )
    squash_commit.add_argument(
        "paths",
        type=Path,
        nargs="+",
        help="One or more subrepo directories (relative to the repository root).",
    )
    squash_commit.add_argument(
        "--target",
        required=True,
        help="Target commit SHA (must be an ancestor of the current subrepo parent).",
    )
    squash_commit.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Permit running with uncommitted changes in the working tree.",
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


def run_git(repo_root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or "unknown git error"
        raise SquashError(f"git {' '.join(args)} failed: {stderr}")
    return result.stdout


def page_output(text: str) -> None:
    pager_cmd = os.environ.get("PAGER") or "less -FRSX"
    try:
        pager_args = shlex.split(pager_cmd)
        env = os.environ.copy()
        if pager_args and pager_args[0].endswith("less"):
            env.setdefault("LESS", "-FRSX")
        with subprocess.Popen(
            pager_args,
            stdin=subprocess.PIPE,
            text=True,
            env=env,
        ) as proc:
            if proc.stdin:
                proc.stdin.write(text)
                proc.stdin.close()
            proc.wait()
    except (OSError, ValueError):
        print(text, end="")


def is_ancestor(repo_root: Path, older: str, newer: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", older, newer],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    stderr = result.stderr.strip() or "unknown git error"
    raise SquashError(f"git merge-base --is-ancestor failed: {stderr}")


def rev_parse(repo_root: Path, ref: str) -> str:
    output = run_git(repo_root, ["rev-parse", ref])
    return output.strip()


def parse_gitrepo_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise SquashError(f"Missing .gitrepo file at {path}")
    lines = path.read_text().splitlines()
    values: dict[str, str] = {}
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_section = stripped.lower() == "[subrepo]"
            continue
        if not in_section:
            continue
        if not stripped or stripped.startswith(("#", ";")):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip().lower()] = value.strip()
    return values


def update_gitrepo_parent(path: Path, new_parent: str) -> None:
    lines = path.read_text().splitlines()
    updated: list[str] = []
    in_section = False
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_section = stripped.lower() == "[subrepo]"
            updated.append(line)
            continue
        if in_section:
            match = re.match(r"^(\s*parent\s*)=(.*)$", line)
            if match:
                updated.append(f"{match.group(1)}= {new_parent}")
                replaced = True
            else:
                updated.append(line)
            continue
        updated.append(line)
    if not replaced:
        raise SquashError(f"Could not find parent entry in {path}")
    path.write_text("\n".join(updated) + "\n")


_SUBREPO_HEADER_QUOTED = re.compile(
    r"^(?:git\s+)?subrepo(?:\s+path)?\s+'([^']+)'\s*:",
    re.IGNORECASE,
)
_SUBREPO_HEADER_UNQUOTED = re.compile(
    r"^(?:git\s+)?subrepo(?:\s+path)?\s+([^:]+)\s*:",
    re.IGNORECASE,
)
_SUBREPO_KV = re.compile(r"^\s*([A-Za-z ]+?)\s*:\s*(.+)$")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def parse_subrepo_status(output: str) -> list[SubrepoStatus]:
    if "No subrepos." in output:
        return []

    entries: list[SubrepoStatus] = []
    current: SubrepoStatus | None = None

    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().endswith("subrepos:"):
            continue
        lowered = stripped.lower()
        if lowered.startswith(("git subrepo", "subrepo")):
            if lowered.startswith("subrepo branch"):
                continue
            header_match = _SUBREPO_HEADER_QUOTED.match(stripped)
            if not header_match:
                header_match = _SUBREPO_HEADER_UNQUOTED.match(stripped)
            if header_match:
                name = header_match.group(1).strip()
                if name.lower() == "branch":
                    continue
                if current:
                    entries.append(current)
                current = SubrepoStatus(path=Path(name))
                continue

        if not current:
            continue

        kv_match = _SUBREPO_KV.match(line)
        if not kv_match:
            continue

        key = kv_match.group(1).strip().lower()
        value = kv_match.group(2).strip()
        if key in {"remote", "remote url"}:
            current = SubrepoStatus(
                path=current.path,
                remote=value,
                branch=current.branch,
                commit=current.commit,
                parent=current.parent,
            )
        elif key in {"branch", "tracking branch"}:
            current = SubrepoStatus(
                path=current.path,
                remote=current.remote,
                branch=value,
                commit=current.commit,
                parent=current.parent,
            )
        elif key in {"commit", "pulled commit", "upstream ref"}:
            current = SubrepoStatus(
                path=current.path,
                remote=current.remote,
                branch=current.branch,
                commit=value,
                parent=current.parent,
            )
        elif key in {"parent", "pull parent"}:
            current = SubrepoStatus(
                path=current.path,
                remote=current.remote,
                branch=current.branch,
                commit=current.commit,
                parent=value,
            )

    if current:
        entries.append(current)

    return entries


class TreeNode:
    def __init__(self) -> None:
        self.children: dict[str, TreeNode] = {}
        self.subrepo: SubrepoStatus | None = None


def build_subrepo_tree(entries: list[SubrepoStatus]) -> TreeNode:
    root = TreeNode()
    for entry in entries:
        node = root
        for part in entry.path.parts:
            node = node.children.setdefault(part, TreeNode())
        node.subrepo = entry
    return root


def use_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def color(text: str, code: str, enable: bool) -> str:
    if not enable:
        return text
    return f"\033[{code}m{text}\033[0m"


def format_subrepo_summary(
    entry: SubrepoStatus,
    colorize: bool,
    missing_parents: set[str],
) -> str:
    details: list[str] = []
    if entry.commit:
        details.append(color(f"commit {entry.commit}", "33", colorize))
    if entry.parent:
        parent_color = "1;31" if entry.parent in missing_parents else "36"
        details.append(color(f"parent {entry.parent}", parent_color, colorize))
    if entry.branch:
        details.append(color(f"branch {entry.branch}", "35", colorize))
    if entry.remote:
        details.append(color(f"remote {entry.remote}", "90", colorize))
    if not details:
        return ""
    return " (" + ", ".join(details) + ")"


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def extract_sha(log_line: str) -> str | None:
    plain = strip_ansi(log_line)
    match = re.search(r"\b[0-9a-f]{7,40}\b", plain)
    if not match:
        return None
    return match.group(0)


def count_commits_until_parents(
    repo_root: Path,
    parents: set[str],
    max_count: int,
) -> tuple[int, set[str]]:
    if not parents:
        return 0, set()
    args = ["log", "--pretty=%h"]
    if max_count > 0:
        args.append(f"--max-count={max_count}")
    output = run_git(repo_root, args)
    remaining = set(parents)
    count = 0
    for line in output.splitlines():
        sha = line.strip()
        if not sha:
            continue
        count += 1
        for parent in list(remaining):
            if sha.startswith(parent) or parent.startswith(sha):
                remaining.discard(parent)
        if not remaining:
            break
    return count, remaining


def gather_repo_history(
    repo_root: Path,
    parent_map: dict[str, list[str]],
    max_count: int,
) -> tuple[list[str], set[str]]:
    parents = set(parent_map)
    count, remaining = count_commits_until_parents(repo_root, parents, max_count)
    if count <= 0:
        return [], remaining

    output = run_git(
        repo_root,
        [
            "log",
            "--graph",
            "--oneline",
            "--decorate",
            "--color=always",
            f"--max-count={count}",
        ],
    )
    colorize = use_color()
    lines: list[str] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        sha = extract_sha(line)
        if sha:
            tags: list[str] = []
            for parent, names in parent_map.items():
                if sha.startswith(parent) or parent.startswith(sha):
                    for name in names:
                        tags.append(f"[{name}]")
                        # tags.append(f"[parent {name}]")
            if tags:
                suffix = " " + " ".join((color(t, '7;36', colorize) for t in tags))
                line = f"{line}{suffix}"
        lines.append(line.rstrip())
    return lines, remaining


def render_tree(
    node: TreeNode,
    prefix: str = "",
    path_parts: tuple[str, ...] = (),
    missing_parents: set[str] | None = None,
) -> list[str]:
    lines: list[str] = []
    items = sorted(node.children.items(), key=lambda item: item[0])
    colorize = use_color()
    missing = missing_parents or set()
    for index, (name, child) in enumerate(items):
        is_last = index == len(items) - 1
        branch = "`-- " if is_last else "|-- "
        if child.subrepo:
            display_name = color(name, "1;36", colorize)
        else:
            display_name = color(name, "32", colorize)
        line = f"{prefix}{branch}{display_name}"
        current_parts = path_parts + (name,)
        if child.subrepo:
            line += format_subrepo_summary(child.subrepo, colorize, missing)
        lines.append(line)

        next_prefix = prefix + ("    " if is_last else "|   ")
        lines.extend(
            render_tree(
                child,
                prefix=next_prefix,
                path_parts=current_parts,
                missing_parents=missing,
            )
        )
    return lines


def run_status(args: argparse.Namespace) -> int:
    repo = find_repo(args.repo)
    repo_root = Path(repo.working_tree_dir).resolve()
    output = run_git(repo_root, ["subrepo", "status"])
    entries = parse_subrepo_status(output)

    if not entries:
        print("No subrepos detected.")
        return 0

    parent_map: dict[str, list[str]] = {}
    for entry in entries:
        if entry.parent:
            parent_map.setdefault(entry.parent, []).append(entry.path.as_posix())

    lines: list[str] = []
    warning_line: str | None = None
    log_lines: list[str] = []
    missing: set[str] = set()

    if parent_map and args.log_count > 0:
        log_lines, missing = gather_repo_history(repo_root, parent_map, args.log_count)
        if missing:
            colorize = use_color()
            details: list[str] = []
            for parent in sorted(missing):
                paths = ", ".join(sorted(parent_map.get(parent, [])))
                if paths:
                    details.append(f"{parent} ({paths})")
                else:
                    details.append(parent)
            warning = (
                "WARNING: pull parents not found in git history (within log limit): "
                + "; ".join(details)
            )
            warning_line = color(warning, "7;31", colorize)

    if warning_line:
        lines.append(warning_line)
        lines.append("")

    lines.append("Subrepos:")
    tree = build_subrepo_tree(entries)
    lines.extend(render_tree(tree, missing_parents=missing))

    if log_lines:
        lines.append("")
        lines.append("History (until all pull parents are found):")
        lines.extend(log_lines)

    output_text = "\n".join(lines) + "\n"
    use_pager = sys.stdout.isatty()
    if args.no_pager:
        use_pager = False
    if args.pager:
        use_pager = True
    if use_pager:
        page_output(output_text)
    else:
        print(output_text, end="")

    return 0


def run_squash_commit(args: argparse.Namespace) -> int:
    repo = find_repo(args.repo)
    ensure_clean(repo, allow_dirty=args.allow_dirty)
    repo_root = Path(repo.working_tree_dir).resolve()

    target_full = rev_parse(repo_root, args.target)
    updated_any = False

    for path in args.paths:
        target_path = resolve_subrepo_path(repo, path)
        rel_path = target_path.relative_to(repo_root)
        gitrepo_path = target_path / ".gitrepo"
        values = parse_gitrepo_file(gitrepo_path)
        current_parent = values.get("parent")
        if not current_parent:
            raise SquashError(f"No parent entry found in {gitrepo_path}")

        current_parent_full = rev_parse(repo_root, current_parent)

        if target_full == current_parent_full:
            print(f"Parent is already {current_parent_full} for {rel_path}.")
            continue

        if not is_ancestor(repo_root, target_full, current_parent_full):
            raise SquashError(
                f"Target {target_full} is not an ancestor of current parent "
                f"{current_parent_full} for {rel_path}."
            )

        diff_output = run_git(
            repo_root,
            [
                "diff",
                "--name-only",
                f"{target_full}..{current_parent_full}",
                "--",
                rel_path.as_posix(),
            ],
        )
        changed_files = [line.strip() for line in diff_output.splitlines() if line.strip()]
        gitrepo_rel = (rel_path / ".gitrepo").as_posix()
        non_gitrepo_changes = [
            file_path for file_path in changed_files if file_path != gitrepo_rel
        ]
        if non_gitrepo_changes:
            details = "\n".join(f" - {file_path}" for file_path in non_gitrepo_changes)
            raise SquashError(
                "Refusing to squash past modified history. "
                f"Found changes under {rel_path} between {target_full}..{current_parent_full} "
                f"outside {gitrepo_rel}:\n{details}"
            )

        update_gitrepo_parent(gitrepo_path, target_full)
        print(f"Updated {gitrepo_path} parent to {target_full}.")
        updated_any = True

    if not updated_any:
        print("No subrepo parents updated.")
    return 0


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
        if args.command is None:
            args.command = "status"
            if not hasattr(args, "log_count"):
                args.log_count = 200
            if not hasattr(args, "pager"):
                args.pager = False
            if not hasattr(args, "no_pager"):
                args.no_pager = False
        if args.command == "squash":
            return run_squash(args)
        if args.command == "status":
            return run_status(args)
        if args.command == "squash-commit":
            return run_squash_commit(args)
        parser.error(f"Unknown command: {args.command!r}")
    except SquashError as exc:
        parser.exit(status=1, message=f"error: {exc}\n")

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
