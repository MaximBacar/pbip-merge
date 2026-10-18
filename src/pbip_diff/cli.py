"""pbip-diff: draw a before/after view of a PBIP report between two git revisions.

Examples
    pbip-diff                       last commit vs. your uncommitted changes
    pbip-diff main                  main vs. working tree
    pbip-diff main feature          main vs. feature
    pbip-diff main...feature        what feature changed since it branched (PR view)
    pbip-diff HEAD~1 HEAD           the last commit
    pbip-diff --folders old/ new/   two folders, no git needed
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import webbrowser
from pathlib import Path

from .diff import diff_project
from .render import render
from .source import FolderSource, GitError, GitSource, merge_base, repo_root

WORKTREE = "WORKTREE"
_SKIP = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pbi"}


def find_project(start: Path) -> Path:
    """Folder holding the .pbip file / *.Report folder, starting from `start`."""
    start = start.resolve()
    if start.is_file() or start.suffix in (".Report", ".SemanticModel", ".Dataset"):
        return start.parent

    def is_project(d: Path) -> bool:
        return any(c.suffix == ".pbip" or (c.suffix == ".Report" and c.is_dir()) for c in d.iterdir())

    if is_project(start):
        return start
    found: list[Path] = []

    def walk(d: Path, depth: int) -> None:
        if depth > 4:
            return
        for c in sorted(d.iterdir()):
            if c.is_dir() and c.name not in _SKIP and not c.name.endswith((".Report", ".SemanticModel")):
                if is_project(c):
                    found.append(c)
                else:
                    walk(c, depth + 1)

    walk(start, 0)
    if not found:
        raise SystemExit(f"No PBIP project (.pbip or *.Report folder) found under {start}")
    if len(found) > 1:
        listing = "\n  ".join(str(f.relative_to(start)) for f in found)
        raise SystemExit(f"Several PBIP projects found, pick one with --project:\n  {listing}")
    return found[0]


def build_sources(args: argparse.Namespace):
    if args.folders:
        old, new = (Path(p) for p in args.folders)
        # Short labels when the folder names tell them apart, full paths otherwise.
        short = old.resolve().name != new.resolve().name
        labels = (old.resolve().name, new.resolve().name) if short else (str(old), str(new))
        return FolderSource(find_project(old), labels[0]), FolderSource(find_project(new), labels[1])

    project = find_project(Path(args.project or "."))
    try:
        repo = repo_root(project)
    except GitError:
        raise SystemExit(f"{project} is not inside a git repository (use --folders to compare folders).")
    subdir = project.relative_to(repo).as_posix()
    subdir = "" if subdir == "." else subdir

    base, head = args.base or "HEAD", args.head or WORKTREE
    if "..." in base:
        if args.head:
            raise SystemExit("Use either 'A...B' or two revisions, not both.")
        a, b = base.split("...", 1)
        a, b = a or "HEAD", b or "HEAD"
        try:
            mb = merge_base(repo, a, b)
        except GitError as exc:
            raise SystemExit(str(exc))
        base_src = GitSource(repo, mb, subdir, label=f"merge-base of {a} and {b} ({mb[:7]})")
        head = b
    else:
        base_src = _git_source(repo, base, subdir)

    if head == WORKTREE:
        head_src = FolderSource(project, "working tree")
    else:
        head_src = _git_source(repo, head, subdir)
    return base_src, head_src


def _git_source(repo: Path, rev: str, subdir: str) -> GitSource:
    try:
        return GitSource(repo, rev, subdir)
    except GitError:
        hint = ""
        if rev == "HEAD":
            hint = " If the repository has no commits yet, commit the report first, or use --folders."
        raise SystemExit(f"Unknown revision '{rev}'.{hint}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pbip-diff",
        description=__doc__.split("\n\n")[0],
        epilog=__doc__.split("\n\n", 1)[1],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("base", nargs="?", help="Before revision (default HEAD). 'A...B' compares B with its merge-base.")
    parser.add_argument("head", nargs="?", help=f"After revision (default: working tree, or {WORKTREE}).")
    parser.add_argument("-p", "--project", help="PBIP project folder, .pbip file or .Report folder (default: search from cwd).")
    parser.add_argument("--folders", nargs=2, metavar=("OLD", "NEW"), help="Compare two project folders instead of git revisions.")
    parser.add_argument("-o", "--output", help="Where to write the HTML (default: a temp file).")
    parser.add_argument("--no-open", action="store_true", help="Don't open the result in a browser.")
    parser.add_argument("--exit-code", action="store_true", help="Exit with 1 when there are differences (for CI).")
    args = parser.parse_args(argv)

    base_src, head_src = build_sources(args)
    try:
        result = diff_project(base_src, head_src)
    finally:
        base_src.close()
        head_src.close()

    names = ", ".join(r.folder.removesuffix(".Report") for r in result.reports) or "PBIP"
    html = render(result, title=f"{names} — report diff")

    out = Path(args.output) if args.output else Path(tempfile.gettempdir()) / "pbip-diff" / "diff.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")

    for w in result.warnings:
        print(f"warning: {w}", file=sys.stderr)
    visuals = [v for r in result.reports for p in r.pages for v in p.visuals]
    changed = sum(1 for v in visuals if v.status not in ("unchanged", "minor"))
    print(f"{changed} visual change(s), {len(result.files)} other file(s) -> {out}")
    if not args.no_open:
        webbrowser.open(out.resolve().as_uri())

    has_changes = changed > 0 or bool(result.files)
    return 1 if args.exit_code and has_changes else 0


if __name__ == "__main__":
    raise SystemExit(main())
