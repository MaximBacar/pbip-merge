"""Compare two versions of a PBIP project."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .model import Page, Report, Visual, find_reports, is_legacy, is_literal, load_report


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING: Any = _Missing()


# ---------------------------------------------------------------- generic JSON diff


@dataclass
class Change:
    parts: tuple[str | int, ...]
    old: Any
    new: Any

    @property
    def path(self) -> str:
        out = ""
        for p in self.parts:
            if isinstance(p, int):
                out += f"[{p}]"
            elif p.startswith("["):
                out += p
            else:
                out += f".{p}" if out else p
        return out


def _list_key(items: list[Any]) -> str | None:
    """Identity key for lists of objects (fields, filters), so a reorder or an
    insert at the top is reported as such rather than as every item changing."""
    if not items or not all(isinstance(i, dict) for i in items):
        return None
    for key in ("queryRef", "name"):
        values = [i.get(key) for i in items]
        if all(isinstance(v, str) for v in values) and len(set(values)) == len(values):
            return key
    return None


def json_diff(a: Any, b: Any, parts: tuple = ()) -> list[Change]:
    if a == b:
        return []
    if is_literal(a) or is_literal(b):
        return [Change(parts, a, b)]
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[Change] = []
        for k in list(a) + [k for k in b if k not in a]:
            out += json_diff(a.get(k, MISSING), b.get(k, MISSING), parts + (k,))
        return out
    if isinstance(a, list) and isinstance(b, list):
        ka, kb = _list_key(a), _list_key(b)
        key = (ka or kb) if (ka == kb or not a or not b) else None
        if key:
            old = {i[key]: i for i in a}
            new = {i[key]: i for i in b}
            out = []
            for k in list(old) + [k for k in new if k not in old]:
                out += json_diff(old.get(k, MISSING), new.get(k, MISSING), parts + (f"[{k}]",))
            if not out:  # same items, different order
                out.append(Change(parts + ("(order)",), [i[key] for i in a], [i[key] for i in b]))
            return out
        out = []
        for i in range(max(len(a), len(b))):
            out += json_diff(
                a[i] if i < len(a) else MISSING, b[i] if i < len(b) else MISSING, parts + (i,)
            )
        return out
    return [Change(parts, a, b)]


# ---------------------------------------------------------------- visuals

KIND_LABELS = {
    "moved": "Moved",
    "resized": "Resized",
    "layer": "Layer / tab order",
    "type": "Visual type",
    "fields": "Fields",
    "format": "Formatting",
    "filters": "Filters",
    "visibility": "Visibility",
    "group": "Grouping",
    "schema": "Schema version",
    "other": "Other",
}
LAYOUT_KINDS = {"moved", "resized"}
MINOR_KINDS = {"layer", "schema"}


def classify(change: Change) -> str:
    p = [str(x) for x in change.parts]
    head = p[0] if p else ""
    second = p[1] if len(p) > 1 else ""
    if head == "position":
        if second in ("x", "y", ""):
            return "moved"
        if second in ("width", "height"):
            return "resized"
        return "layer"
    if head == "$schema":
        return "schema"
    if head == "isHidden":
        return "visibility"
    if head in ("visualGroup", "parentGroupName"):
        return "group"
    if head == "filterConfig":
        return "filters"
    if head == "visual":
        if second == "visualType":
            return "type"
        if second == "query":
            return "fields"
        if second in ("objects", "visualContainerObjects", "vcObjects"):
            return "format"
    return "other"


@dataclass
class FieldChange:
    role: str
    added: list[str]
    removed: list[str]


@dataclass
class VisualDiff:
    name: str
    before: Visual | None
    after: Visual | None
    changes: list[Change] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    fields: list[FieldChange] = field(default_factory=list)

    @property
    def current(self) -> Visual:
        return self.after or self.before  # type: ignore[return-value]

    @property
    def status(self) -> str:
        if self.before is not None and self.after is not None and not self.kinds:
            return "unchanged"
        if self.after is not None and self.after.error:
            return "invalid"
        if self.before is None:
            return "added"
        if self.after is None:
            return "removed"
        if set(self.kinds) - LAYOUT_KINDS - MINOR_KINDS:
            return "changed"
        if set(self.kinds) & LAYOUT_KINDS:
            return "layout"
        return "minor"


def diff_visual(name: str, before: Visual | None, after: Visual | None) -> VisualDiff:
    vd = VisualDiff(name, before, after)
    if before is None or after is None:
        return vd
    vd.changes = json_diff(before.raw, after.raw)
    if before.error != after.error:
        vd.changes.append(Change(("(file)",), before.error or "valid JSON", after.error or "valid JSON"))
    seen: list[str] = []
    for c in vd.changes:
        k = classify(c)
        if k not in seen:
            seen.append(k)
    vd.kinds = sorted(seen, key=list(KIND_LABELS).index)

    bf, af = before.fields, after.fields
    for role in list(bf) + [r for r in af if r not in bf]:
        old, new = bf.get(role, []), af.get(role, [])
        added = [f for f in new if f not in old]
        removed = [f for f in old if f not in new]
        if added or removed:
            vd.fields.append(FieldChange(role, added, removed))
    return vd


# ---------------------------------------------------------------- pages / reports

STATUS_ORDER = ["invalid", "removed", "added", "changed", "layout", "minor", "unchanged"]


@dataclass
class PageDiff:
    name: str
    before: Page | None
    after: Page | None
    changes: list[Change]
    visuals: list[VisualDiff]

    @property
    def current(self) -> Page:
        return self.after or self.before  # type: ignore[return-value]

    @property
    def status(self) -> str:
        if self.before is None:
            return "added"
        if self.after is None:
            return "removed"
        if self.changes or any(v.status not in ("unchanged", "minor") for v in self.visuals):
            return "changed"
        return "unchanged"

    def count(self, status: str) -> int:
        return sum(1 for v in self.visuals if v.status == status)


def diff_page(name: str, before: Page | None, after: Page | None) -> PageDiff:
    braw = {k: v for k, v in (before.raw if before else {}).items() if k != "$schema"}
    araw = {k: v for k, v in (after.raw if after else {}).items() if k != "$schema"}
    changes = json_diff(braw, araw) if before and after else []
    bv = before.visuals if before else {}
    av = after.visuals if after else {}
    visuals = [diff_visual(n, bv.get(n), av.get(n)) for n in list(bv) + [n for n in av if n not in bv]]
    visuals.sort(key=lambda v: (STATUS_ORDER.index(v.status), v.current.y, v.current.x))
    return PageDiff(name, before, after, changes, visuals)


@dataclass
class ReportDiff:
    folder: str
    before: Report | None
    after: Report | None
    pages: list[PageDiff]
    order_changed: bool

    @property
    def status(self) -> str:
        if self.before is None:
            return "added"
        if self.after is None:
            return "removed"
        if self.order_changed or any(p.status != "unchanged" for p in self.pages):
            return "changed"
        return "unchanged"


def diff_report(folder: str, before: Report | None, after: Report | None) -> ReportDiff:
    bp = before.pages if before else {}
    ap = after.pages if after else {}
    order = [p.name for p in after.ordered_pages()] if after else []
    order += [p.name for p in before.ordered_pages() if p.name not in ap] if before else []
    pages = [diff_page(n, bp.get(n), ap.get(n)) for n in order]
    order_changed = bool(
        before and after
        and [n for n in before.page_order if n in ap] != [n for n in after.page_order if n in bp]
    )
    return ReportDiff(folder, before, after, pages, order_changed)


# ---------------------------------------------------------------- other files


@dataclass
class FileDiff:
    path: str
    status: str  # added | removed | changed
    old: list[str]
    new: list[str]
    binary: bool = False


_VISUAL_FILE = re.compile(r"^[^/]+\.Report/definition/pages/[^/]+/(page\.json|visuals/[^/]+/visual\.json)$")


def _decode(data: bytes | None) -> list[str] | None:
    if data is None:
        return []
    if b"\0" in data[:8000]:
        return None
    try:
        return data.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError:
        return None


def diff_files(base, head, base_files: list[str], head_files: list[str]) -> list[FileDiff]:
    out: list[FileDiff] = []
    bset, hset = set(base_files), set(head_files)
    for path in sorted(bset | hset):
        if _VISUAL_FILE.match(path):
            continue  # shown on the canvas instead
        old = base.read(path) if path in bset else None
        new = head.read(path) if path in hset else None
        if old == new:
            continue
        status = "added" if old is None else "removed" if new is None else "changed"
        a, b = _decode(old), _decode(new)
        if a is None or b is None:
            out.append(FileDiff(path, status, [], [], binary=True))
        else:
            out.append(FileDiff(path, status, a, b))
    return out


# ---------------------------------------------------------------- project


@dataclass
class ProjectDiff:
    base_label: str
    head_label: str
    reports: list[ReportDiff]
    files: list[FileDiff]
    warnings: list[str]


def diff_project(base, head) -> ProjectDiff:
    base_files, head_files = base.files(), head.files()
    warnings: list[str] = []
    reports: list[ReportDiff] = []
    for folder in sorted(find_reports(base_files) | find_reports(head_files)):
        if is_legacy(base_files, folder) or is_legacy(head_files, folder):
            warnings.append(
                f"{folder} uses the legacy report.json format; only PBIR is drawn. "
                "In Power BI Desktop enable 'Store reports using enhanced metadata format (PBIR)' "
                "and save again."
            )
        before = load_report(base, base_files, folder)
        after = load_report(head, head_files, folder)
        if before or after:
            reports.append(diff_report(folder, before, after))
    files = diff_files(base, head, base_files, head_files)
    return ProjectDiff(base.label, head.label, reports, files, warnings)
