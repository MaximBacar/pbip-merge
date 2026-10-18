"""Read the files of a PBIP project from a folder or from a git revision.

Both sources expose the same two methods, with paths relative to the
project folder (the folder that holds the .pbip file):

    files() -> sorted list of posix paths
    read(path) -> bytes, or None if the file does not exist
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

# Only these top-level entries belong to a PBIP project. Everything else in the
# folder (virtualenvs, scripts, docs...) is ignored.
PROJECT_SUFFIXES = (".Report", ".SemanticModel", ".Dataset", ".pbip")
# Local-only folders Power BI keeps next to the definition (caches, settings).
SKIP_DIRS = {".pbi", ".git"}


def _is_project_path(rel: str) -> bool:
    parts = rel.split("/")
    if not parts[0].endswith(PROJECT_SUFFIXES):
        return False
    return not any(p in SKIP_DIRS for p in parts)


class FolderSource:
    def __init__(self, root: Path, label: str | None = None):
        self.root = Path(root).resolve()
        self.label = label or str(root)

    def files(self) -> list[str]:
        if not self.root.is_dir():
            return []
        out: list[str] = []
        for entry in self.root.iterdir():
            if not entry.name.endswith(PROJECT_SUFFIXES):
                continue
            if entry.is_file():
                out.append(entry.name)
                continue
            for dirpath, dirnames, filenames in os.walk(entry):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
                for name in filenames:
                    out.append((Path(dirpath) / name).relative_to(self.root).as_posix())
        return sorted(out)

    def read(self, rel: str) -> bytes | None:
        path = self.root / rel
        return path.read_bytes() if path.is_file() else None

    def close(self) -> None:
        pass


class GitError(RuntimeError):
    pass


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def repo_root(path: Path) -> Path:
    return Path(git(path, "rev-parse", "--show-toplevel").strip())


def resolve_rev(repo: Path, rev: str) -> str:
    """Return the full commit sha for a revision, or raise GitError."""
    return git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}").strip()


def merge_base(repo: Path, a: str, b: str) -> str:
    return git(repo, "merge-base", a, b).strip()


class GitSource:
    """Files of the project folder as they were in a given commit."""

    def __init__(self, repo: Path, rev: str, subdir: str, label: str | None = None):
        self.repo = Path(repo)
        self.sha = resolve_rev(self.repo, rev)
        self.subdir = subdir.strip("/")  # project folder relative to repo root
        self.label = label or f"{rev} ({self.sha[:7]})"
        self._batch: subprocess.Popen | None = None

    def _full(self, rel: str) -> str:
        return f"{self.subdir}/{rel}" if self.subdir else rel

    def files(self) -> list[str]:
        out = git(
            self.repo, "ls-tree", "-r", "-z", "--full-tree", "--name-only",
            self.sha, "--", self.subdir or ".",
        )
        prefix = f"{self.subdir}/" if self.subdir else ""
        rels = [n[len(prefix):] for n in out.split("\0") if n.startswith(prefix)]
        return sorted(r for r in rels if r and _is_project_path(r))

    def read(self, rel: str) -> bytes | None:
        # One long-running `git cat-file --batch` is much faster than one
        # `git show` per file on reports with hundreds of visuals.
        if self._batch is None:
            self._batch = subprocess.Popen(
                ["git", "-C", str(self.repo), "cat-file", "--batch"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            )
        assert self._batch.stdin and self._batch.stdout
        self._batch.stdin.write(f"{self.sha}:{self._full(rel)}\n".encode())
        self._batch.stdin.flush()
        header = self._batch.stdout.readline()
        if header.endswith(b"missing\n") or not header:
            return None
        size = int(header.split()[2])
        data = self._batch.stdout.read(size)
        self._batch.stdout.read(1)  # trailing newline
        return data

    def close(self) -> None:
        if self._batch is not None:
            self._batch.stdin.close()  # type: ignore[union-attr]
            self._batch.wait()
            self._batch = None
