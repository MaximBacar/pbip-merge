"""Parse a PBIR report folder into pages and visuals.

PBIR layout (inside `<Name>.Report/`):

    definition/pages/pages.json                         page order
    definition/pages/<pageId>/page.json                 name, size
    definition/pages/<pageId>/visuals/<id>/visual.json  one visual
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

PAGES_DIR = "definition/pages"


@dataclass
class Visual:
    name: str
    raw: dict[str, Any]
    error: str | None = None  # set when visual.json could not be parsed
    source: str = ""  # file text, for the line diff

    @property
    def position(self) -> dict[str, Any]:
        return self.raw.get("position") or {}

    @property
    def x(self) -> float:
        return float(self.position.get("x", 0))

    @property
    def y(self) -> float:
        return float(self.position.get("y", 0))

    @property
    def z(self) -> float:
        return float(self.position.get("z", 0))

    @property
    def width(self) -> float:
        return float(self.position.get("width", 0))

    @property
    def height(self) -> float:
        return float(self.position.get("height", 0))

    @property
    def is_group(self) -> bool:
        return "visualGroup" in self.raw

    @property
    def parent(self) -> str | None:
        return self.raw.get("parentGroupName")

    @property
    def hidden(self) -> bool:
        return bool(self.raw.get("isHidden"))

    @property
    def visual_type(self) -> str:
        if self.error and not self.raw:
            return "invalid"
        if self.is_group:
            return "group"
        return (self.raw.get("visual") or {}).get("visualType", "unknown")

    @property
    def title(self) -> str | None:
        if self.is_group:
            return (self.raw.get("visualGroup") or {}).get("displayName")
        vis = self.raw.get("visual") or {}
        for key in ("visualContainerObjects", "vcObjects"):
            for entry in (vis.get(key) or {}).get("title") or []:
                text = literal((entry.get("properties") or {}).get("text"))
                if isinstance(text, str) and text:
                    return text
        return None

    @property
    def fields(self) -> dict[str, list[str]]:
        """Field names bound to each role (Category, Y, Values...)."""
        vis = self.raw.get("visual") or {}
        state = (vis.get("query") or {}).get("queryState") or {}
        return {
            role: [projection_name(p) for p in (bucket or {}).get("projections") or []]
            for role, bucket in state.items()
        }

    @property
    def text(self) -> str | None:
        """Text content of a textbox visual."""
        vis = self.raw.get("visual") or {}
        runs: list[str] = []
        for general in (vis.get("objects") or {}).get("general") or []:
            for para in (general.get("properties") or {}).get("paragraphs") or []:
                line = "".join(str(r.get("value", "")) for r in para.get("textRuns") or [])
                runs.append(line)
        return "\n".join(runs) if runs else None

    @property
    def label(self) -> str:
        """What a person would call this visual: its title, else what Power BI
        would show (text of a textbox, or the fields it displays)."""
        if self.title:
            return self.title
        if self.visual_type == "textbox" and self.text:
            return self.text.strip().splitlines()[0][:80]
        names = [f for fs in self.fields.values() for f in fs]
        if names and not self.is_group:
            return ", ".join(names[:2]) + (" …" if len(names) > 2 else "")
        return friendly_type(self.visual_type)


@dataclass
class Page:
    name: str
    raw: dict[str, Any]
    visuals: dict[str, Visual] = field(default_factory=dict)
    error: str | None = None

    @property
    def display_name(self) -> str:
        return self.raw.get("displayName") or self.name

    @property
    def width(self) -> float:
        return float(self.raw.get("width") or 1280)

    @property
    def height(self) -> float:
        return float(self.raw.get("height") or 720)

    @property
    def hidden(self) -> bool:
        return self.raw.get("visibility") == "HiddenInViewMode"


@dataclass
class Report:
    folder: str  # e.g. "Sales.Report"
    pages: dict[str, Page]
    page_order: list[str]

    def ordered_pages(self) -> list[Page]:
        known = [self.pages[n] for n in self.page_order if n in self.pages]
        rest = [p for n, p in sorted(self.pages.items()) if n not in self.page_order]
        return known + rest


# ---------------------------------------------------------------- helpers


def literal(value: Any) -> Any:
    """Unwrap a PBIR literal `{"expr": {"Literal": {"Value": "'Top'"}}}` -> Top."""
    if not is_literal(value):
        return value
    raw = value["expr"]["Literal"]["Value"]
    if isinstance(raw, str):
        if len(raw) >= 2 and raw[0] == raw[-1] == "'":
            return raw[1:-1].replace("''", "'")
        # Numbers carry a type suffix: 12D (double), 3L (long), 1.5M (decimal)
        m = re.fullmatch(r"(-?\d+(?:\.\d+)?)[DLM]", raw)
        if m:
            return m.group(1)
    return raw


def is_literal(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"expr"}
        and isinstance(value["expr"], dict)
        and set(value["expr"]) == {"Literal"}
        and isinstance(value["expr"]["Literal"], dict)
        and "Value" in value["expr"]["Literal"]
    )


AGGREGATIONS = {0: "Sum", 1: "Avg", 2: "Count", 3: "Min", 4: "Max", 5: "CountNonNull", 6: "Median"}


def field_name(expr: dict[str, Any]) -> str:
    """Readable name for a PBIR field expression (Column / Measure / Aggregation)."""
    if not isinstance(expr, dict):
        return "?"
    for kind in ("Column", "Measure"):
        if kind in expr:
            node = expr[kind]
            entity = ((node.get("Expression") or {}).get("SourceRef") or {}).get("Entity")
            return f"{entity}.{node.get('Property')}" if entity else str(node.get("Property"))
    if "Aggregation" in expr:
        node = expr["Aggregation"]
        fn = AGGREGATIONS.get(node.get("Function"), "Agg")
        return f"{fn}({field_name(node.get('Expression') or {})})"
    if "HierarchyLevel" in expr:
        return str(expr["HierarchyLevel"].get("Level"))
    return next(iter(expr), "?")


def projection_name(p: dict[str, Any]) -> str:
    """Name as Power BI shows it in the field well: "Total Revenue", "Sum of Sales"."""
    if p.get("displayName") or p.get("nativeQueryRef"):
        return p.get("displayName") or p["nativeQueryRef"]
    expr = p.get("field") or {}
    if "Column" in expr or "Measure" in expr:
        return str((expr.get("Column") or expr.get("Measure")).get("Property"))
    if "Aggregation" in expr:
        node = expr["Aggregation"]
        fn = AGGREGATIONS.get(node.get("Function"), "Agg")
        inner = node.get("Expression") or {}
        prop = (inner.get("Column") or inner.get("Measure") or {}).get("Property", "?")
        return f"{fn} of {prop}"
    return p.get("queryRef") or field_name(expr)


TYPE_NAMES = {
    "tableEx": "Table",
    "pivotTable": "Matrix",
    "cardVisual": "Card (new)",
    "textbox": "Text box",
    "actionButton": "Button",
    "advancedSlicerVisual": "Button slicer",
    "lineClusteredColumnComboChart": "Line & column chart",
    "lineStackedColumnComboChart": "Line & stacked column chart",
    "kpi": "KPI",
}


def friendly_type(visual_type: str) -> str:
    if visual_type in TYPE_NAMES:
        return TYPE_NAMES[visual_type]
    words = re.sub(r"(?<!^)(?=[A-Z])", " ", visual_type).lower()
    return words[:1].upper() + words[1:]


def parse_json(data: bytes | None) -> tuple[Any, str | None]:
    if data is None:
        return None, None
    try:
        return json.loads(data.decode("utf-8-sig")), None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        text = data.decode("utf-8", "replace")
        if "<<<<<<<" in text and ">>>>>>>" in text:
            # Still draw the visual, using "our" side of the conflict.
            ours = _conflict_side(text)
            try:
                return json.loads(ours), "Unresolved merge conflict markers"
            except json.JSONDecodeError:
                return None, "Unresolved merge conflict markers"
        return None, f"Invalid JSON: {exc}"


def _conflict_side(text: str) -> str:
    """Keep the first ("ours") side of every conflict block."""
    out, state = [], "both"
    for line in text.splitlines():
        if line.startswith("<<<<<<<"):
            state = "ours"
        elif line.startswith("|||||||") and state == "ours":
            state = "skip"
        elif line.startswith("=======") and state in ("ours", "skip"):
            state = "skip"
        elif line.startswith(">>>>>>>") and state == "skip":
            state = "both"
        elif state in ("both", "ours"):
            out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------- loading


def find_reports(files: list[str]) -> set[str]:
    """Report folders (e.g. "Sales.Report") present in a file listing."""
    return {
        f.split("/", 1)[0]
        for f in files
        if f.split("/", 1)[0].endswith(".Report") and "/" in f
    }


def is_legacy(files: list[str], folder: str) -> bool:
    """True for the older single-file format (report.json at the folder root)."""
    return f"{folder}/report.json" in files and not any(
        f.startswith(f"{folder}/{PAGES_DIR}/") for f in files
    )


def load_report(source, files: list[str], folder: str) -> Report | None:
    prefix = f"{folder}/{PAGES_DIR}/"
    if not any(f.startswith(prefix) for f in files):
        return None

    meta, _ = parse_json(source.read(prefix + "pages.json"))
    page_order = list((meta or {}).get("pageOrder") or [])

    pages: dict[str, Page] = {}
    visual_files: list[tuple[str, str, str]] = []
    for f in files:
        if not f.startswith(prefix):
            continue
        parts = f[len(prefix):].split("/")
        if len(parts) == 2 and parts[1] == "page.json":
            raw, err = parse_json(source.read(f))
            pages[parts[0]] = Page(parts[0], raw if isinstance(raw, dict) else {}, error=err)
        elif len(parts) == 4 and parts[1] == "visuals" and parts[3] == "visual.json":
            visual_files.append((parts[0], parts[2], f))

    for page_name, visual_name, path in visual_files:
        page = pages.setdefault(page_name, Page(page_name, {}, error="page.json missing"))
        data = source.read(path)
        raw, err = parse_json(data)
        page.visuals[visual_name] = Visual(
            visual_name, raw if isinstance(raw, dict) else {}, error=err,
            source=data.decode("utf-8-sig", "replace") if data else "",
        )

    return Report(folder, pages, page_order)
