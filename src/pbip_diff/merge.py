"""Three-way merge of PBIP files (base / current / incoming).

JSON files are merged property by property instead of line by line, so two
people editing different properties of the same visual never conflict. Lists of
fields and filters are matched by their identity (queryRef / name), and lists of
plain names (page order) are merged as ordered sets. Only a property changed in
different ways on both sides is a conflict, and the caller picks a side for it.

Other text files (TMDL...) use git's line-based merge; each conflicting hunk can
be resolved as current, incoming or both.

Terminology (same as VS Code):
    current  = your side (HEAD, the branch you are merging into)
    incoming = their side (the branch being merged in)
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .diff import MISSING, Change, _list_key
from .model import is_literal

CURRENT, INCOMING, BOTH, MERGE = "current", "incoming", "both", "merge"

# Properties where a conflict doesn't matter; current always wins.
AUTO_CURRENT_KEYS = {"activePageName"}


# ---------------------------------------------------------------- JSON merge


@dataclass
class Conflict:
    path: str
    base: Any
    current: Any
    incoming: Any


def _path(parts: tuple) -> str:
    return Change(parts, None, None).path or "(whole file)"


def _common_key(*lists: list) -> str | None:
    keys = {_list_key(lst) for lst in lists if lst}
    if len(keys) == 1:
        return keys.pop()
    return None


def _is_name_list(*lists: list) -> bool:
    items = [i for lst in lists for i in lst]
    if not all(isinstance(i, (str, int, float)) for i in items):
        return False
    return all(len(set(lst)) == len(lst) for lst in lists)


def _insert_like(order: list, source: list, key_of, item_key) -> None:
    """Insert item_key into order where it sits in source: just before the next
    item it had there, or at the end when it was last."""
    keys = [key_of(x) for x in source]
    for nxt in keys[keys.index(item_key) + 1:]:
        if nxt in order:
            order.insert(order.index(nxt), item_key)
            return
    order.append(item_key)


def merge_json(
    base: Any, cur: Any, inc: Any,
    choices: dict[str, str] | None = None,
    conflicts: list[Conflict] | None = None,
    parts: tuple = (),
) -> Any:
    """Merge three JSON values. Returns MISSING when the result is "deleted".

    Conflicts are appended to `conflicts`; each one takes the side named in
    `choices[path]`, or the current side while unresolved."""
    choices = choices if choices is not None else {}
    conflicts = conflicts if conflicts is not None else []

    if cur == inc:
        return cur
    if cur == base:
        return inc
    if inc == base:
        return cur

    atomic = is_literal(cur) or is_literal(inc)  # {"expr": {"Literal": ...}} is one value
    if not atomic and isinstance(cur, dict) and isinstance(inc, dict) and (base is MISSING or isinstance(base, dict)):
        b = base if isinstance(base, dict) else {}
        out = {}
        for k in list(cur) + [k for k in inc if k not in cur]:
            v = merge_json(b.get(k, MISSING), cur.get(k, MISSING), inc.get(k, MISSING), choices, conflicts, parts + (k,))
            if v is not MISSING:
                out[k] = v
        return out

    if isinstance(cur, list) and isinstance(inc, list) and (base is MISSING or isinstance(base, list)):
        b = base if isinstance(base, list) else []
        key = _common_key(b, cur, inc)
        if key:
            bm = {i[key]: i for i in b}
            cm = {i[key]: i for i in cur}
            im = {i[key]: i for i in inc}
            order = [i[key] for i in cur]
            for item in inc:
                if item[key] not in cm:
                    _insert_like(order, inc, lambda x: x[key], item[key])
            out = []
            for k in order:
                v = merge_json(bm.get(k, MISSING), cm.get(k, MISSING), im.get(k, MISSING), choices, conflicts, parts + (f"[{k}]",))
                if v is not MISSING:
                    out.append(v)
            return out
        if len(b) == len(cur) == len(inc):
            # Formatting lists ("labels": [{"properties": ...}]) have no ids:
            # merge them item by item so conflicts point at the real property.
            out = []
            for n in range(len(cur)):
                v = merge_json(b[n], cur[n], inc[n], choices, conflicts, parts + (n,))
                if v is not MISSING:
                    out.append(v)
            return out
        if _is_name_list(b, cur, inc):
            out = [x for x in cur if not (x in b and x not in inc)]
            for x in inc:
                if x not in out and x not in b:
                    _insert_like(out, inc, lambda y: y, x)
            return out

    if parts and parts[-1] in AUTO_CURRENT_KEYS:
        return cur
    path = _path(parts)
    conflicts.append(Conflict(path, base, cur, inc))
    return inc if choices.get(path) == INCOMING else cur


# ---------------------------------------------------------------- text merge


@dataclass
class TextConflict:
    current: list[str]
    base: list[str]
    incoming: list[str]


def merge_text(base: str, cur: str, inc: str) -> list[str | TextConflict]:
    """Line-based 3-way merge (git merge-file). Returns plain text chunks and conflicts."""
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for name, text in (("current", cur), ("base", base), ("incoming", inc)):
            p = Path(d) / name
            p.write_text(text, encoding="utf-8")
            paths.append(str(p))
        proc = subprocess.run(
            ["git", "merge-file", "-p", "--diff3", "-L", "current", "-L", "base", "-L", "incoming", *paths],
            capture_output=True,
        )
    if proc.returncode < 0 or proc.returncode > 127:
        raise RuntimeError(proc.stderr.decode(errors="replace"))
    out: list[str | TextConflict] = []
    state, c, b, i = "text", [], [], []
    for line in proc.stdout.decode("utf-8", "replace").splitlines(keepends=True):
        if state == "text" and line.startswith("<<<<<<< current"):
            state, c, b, i = "current", [], [], []
        elif state == "current" and line.startswith("||||||| base"):
            state = "base"
        elif state in ("current", "base") and line.rstrip("\r\n") == "=======":
            state = "incoming"
        elif state == "incoming" and line.startswith(">>>>>>> incoming"):
            out.append(TextConflict(c, b, i))
            state = "text"
        elif state == "current":
            c.append(line)
        elif state == "base":
            b.append(line)
        elif state == "incoming":
            i.append(line)
        else:
            out.append(line)
    return out


def resolve_text(chunks: list[str | TextConflict], choices: dict[str, str]) -> str:
    out: list[str] = []
    n = 0
    for ch in chunks:
        if isinstance(ch, str):
            out.append(ch)
            continue
        side = choices.get(f"#{n}", CURRENT)
        if side == INCOMING:
            out += ch.incoming
        elif side == BOTH:
            out += ch.current + ch.incoming
        else:
            out += ch.current
        n += 1
    return "".join(out)


# ---------------------------------------------------------------- file merge


def _parse(data: bytes | None) -> Any:
    if data is None:
        return MISSING
    try:
        return json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError


def _text(data: bytes | None) -> str | None:
    if data is None:
        return ""
    if b"\0" in data[:8000]:
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


@dataclass
class FileMerge:
    path: str
    base: bytes | None
    current: bytes | None
    incoming: bytes | None
    kind: str = "binary"  # json | text | binary
    reason: str = "Changed on both sides"
    conflicts: list = field(default_factory=list)  # Conflict | TextConflict
    _chunks: list | None = None

    @property
    def can_merge(self) -> bool:
        return self.kind != "binary" and self.current is not None and self.incoming is not None

    @property
    def needs_choice(self) -> bool:
        return bool(self.conflicts) or not self.can_merge

    @property
    def conflict_ids(self) -> list[str]:
        if self.kind == "json":
            return [c.path for c in self.conflicts]
        return [f"#{n}" for n in range(len(self.conflicts))]

    def merged_json(self, choices: dict[str, str]) -> Any:
        return merge_json(_parse(self.base), _parse(self.current), _parse(self.incoming), choices, [])

    def result(self, strategy: str, choices: dict[str, str] | None = None) -> bytes | None:
        """Bytes to write (None = delete the file)."""
        if strategy == CURRENT:
            return self.current
        if strategy == INCOMING:
            return self.incoming
        if not self.can_merge:
            raise ValueError(f"{self.path} can't be merged; keep current or incoming")
        choices = choices or {}
        if self.kind == "json":
            merged = self.merged_json(choices)
            if merged is MISSING:
                return None
            # Keep the original bytes (and formatting) when one side wins as a whole.
            for side in (self.current, self.incoming):
                if merged == _parse(side):
                    return side
            nl = "\n" if (self.current or b"").endswith(b"\n") else ""
            return (json.dumps(merged, indent=2, ensure_ascii=False) + nl).encode("utf-8")
        return resolve_text(self._chunks or [], choices).encode("utf-8")


def merge_file(path: str, base: bytes | None, cur: bytes | None, inc: bytes | None) -> FileMerge:
    fm = FileMerge(path, base, cur, inc)
    if cur is None:
        fm.reason = "Deleted in current, changed in incoming"
    elif inc is None:
        fm.reason = "Changed in current, deleted in incoming"
    elif base is None:
        fm.reason = "Added on both sides"
    try:
        b, c, i = _parse(base), _parse(cur), _parse(inc)
        fm.kind = "json"
        if fm.can_merge:
            merge_json(b, c, i, {}, fm.conflicts)
        return fm
    except ValueError:
        pass
    texts = [_text(x) for x in (base, cur, inc)]
    if any(t is None for t in texts):
        fm.kind = "binary"
        return fm
    fm.kind = "text"
    if fm.can_merge:
        fm._chunks = merge_text(*texts)  # type: ignore[arg-type]
        fm.conflicts = [ch for ch in fm._chunks if isinstance(ch, TextConflict)]
    return fm


# ---------------------------------------------------------------- session


class OverlaySource:
    """A source with some files replaced (bytes) or deleted (None)."""

    def __init__(self, inner, overrides: dict[str, bytes | None], label: str = "result"):
        self.inner, self.overrides, self.label = inner, overrides, label

    def files(self) -> list[str]:
        out = set(self.inner.files()) | {p for p, v in self.overrides.items() if v is not None}
        return sorted(p for p in out if self.overrides.get(p, b"") is not None)

    def read(self, rel: str) -> bytes | None:
        if rel in self.overrides:
            return self.overrides[rel]
        return self.inner.read(rel)

    def close(self) -> None:
        pass


@dataclass
class Decision:
    strategy: str | None  # current | incoming | merge | None (undecided)
    choices: dict[str, str] = field(default_factory=dict)


@dataclass
class MergeSession:
    mode: str  # git | preview | folders
    base: Any
    current: Any
    incoming: Any
    files: list[FileMerge]
    incoming_only: dict[str, bytes | None]  # files only incoming changed (auto-taken)
    worktree: Any = None  # git mode: FolderSource of the project in the working tree
    project: Path | None = None  # git mode: project folder to write into
    repo: Path | None = None

    def default_decisions(self) -> dict[str, Decision]:
        return {f.path: Decision(MERGE if f.can_merge else None) for f in self.files}

    def overrides(self, decisions: dict[str, Decision]) -> dict[str, bytes | None]:
        """File contents of the merge result. Undecided files use their best guess
        (merged with conflicts on the current side, or current)."""
        out: dict[str, bytes | None] = dict(self.incoming_only)
        for f in self.files:
            d = decisions.get(f.path) or Decision(None)
            strategy = d.strategy or (MERGE if f.can_merge else CURRENT)
            out[f.path] = f.result(strategy, d.choices)
        return out

    def result_source(self, decisions: dict[str, Decision]) -> OverlaySource:
        inner = self.worktree if self.mode == "git" else self.current
        return OverlaySource(inner, self.overrides(decisions))

    def problems(self, decisions: dict[str, Decision]) -> list[str]:
        out = []
        for f in self.files:
            d = decisions.get(f.path)
            if d is None or d.strategy is None:
                out.append(f"{f.path}: choose current, incoming or merge")
            elif d.strategy == MERGE:
                if not f.can_merge:
                    out.append(f"{f.path}: can't be merged")
                missing = [c for c in f.conflict_ids if c not in d.choices]
                if missing:
                    out.append(f"{f.path}: {len(missing)} conflict(s) without a choice")
        return out

    # ---- writing

    def apply_git(self, decisions: dict[str, Decision]) -> list[str]:
        assert self.project and self.repo
        written = []
        for f in self.files:
            d = decisions[f.path]
            data = f.result(d.strategy or CURRENT, d.choices)
            target = self.project / f.path
            if data is None:
                if target.exists():
                    target.unlink()
                _git(self.repo, "rm", "-q", "--cached", "--ignore-unmatch", "--", str(target))
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                _git(self.repo, "add", "--", str(target))
            written.append(f.path)
        return written

    def apply_folders(self, decisions: dict[str, Decision], out_dir: Path) -> list[str]:
        src = Path(self.current.root)
        if out_dir.resolve() != src.resolve():
            if out_dir.exists() and any(out_dir.iterdir()):
                raise FileExistsError(f"{out_dir} is not empty")
            shutil.copytree(src, out_dir, dirs_exist_ok=True)
        written = []
        for path, data in self.overrides(decisions).items():
            target = out_dir / path
            if data is None:
                if target.exists():
                    target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            written.append(path)
        return written


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip())
    return proc.stdout


def build_session(mode: str, base, current, incoming, only: set[str] | None = None, **extra) -> MergeSession:
    """Compare the three versions. `only` limits the files looked at (git's
    unmerged paths); otherwise every file changed on both sides is included."""
    paths = sorted(set(base.files()) | set(current.files()) | set(incoming.files()))
    files: list[FileMerge] = []
    incoming_only: dict[str, bytes | None] = {}
    for p in paths:
        if only is not None and p not in only:
            continue
        b, c, i = base.read(p), current.read(p), incoming.read(p)
        if c == i and only is None:
            continue
        if c == b and only is None:
            incoming_only[p] = i
            continue
        if i == b and only is None:
            continue
        files.append(merge_file(p, b, c, i))
    return MergeSession(mode, base, current, incoming, files, incoming_only, **extra)


# ---------------------------------------------------------------- git helpers


def unmerged_paths(repo: Path) -> list[str]:
    """Paths with merge conflicts (relative to the repo root)."""
    out = _git(repo, "ls-files", "-u", "-z")
    return sorted({entry.split("\t", 1)[1] for entry in out.split("\0") if "\t" in entry})


def merge_in_progress(repo: Path) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "-q", "--verify", "MERGE_HEAD"], capture_output=True
    )
    return proc.returncode == 0


def ref_label(repo: Path, rev: str) -> str:
    if rev == "HEAD":
        name = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    else:
        proc = subprocess.run(
            ["git", "-C", str(repo), "name-rev", "--name-only", "--exclude=tags/*", rev],
            capture_output=True, text=True,
        )
        name = proc.stdout.strip() if proc.returncode == 0 else rev
        name = re.sub(r"~\d+$", "", name) if name and name != "undefined" else rev
    sha = _git(repo, "rev-parse", "--short", rev).strip()
    return f"{name} ({sha})"
