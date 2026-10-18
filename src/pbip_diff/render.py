"""Render a ProjectDiff as one self-contained HTML page (no JavaScript).

The layout follows GitHub's "Files changed" view: a changes tree on the left,
one collapsible block per page and per visual, a rich/source toggle and a
"Viewed" checkbox on each visual. Everything interactive is plain CSS
(checkboxes, radio buttons, :has, :target and counters).
"""

from __future__ import annotations

import json
import re
from html import escape
from typing import Any

from .diff import KIND_LABELS, MISSING, Change, FileDiff, PageDiff, ProjectDiff, ReportDiff, VisualDiff
from .model import Page, Visual, field_name, friendly_type, is_literal, literal, projection_name
from .textdiff import line_stats, split_table

STATUS_LABELS = {
    "added": "Added",
    "removed": "Removed",
    "changed": "Modified",
    "layout": "Moved / resized",
    "minor": "Minor",
    "unchanged": "Unchanged",
    "invalid": "Invalid JSON",
}
CHANGED = ("added", "removed", "changed", "layout", "invalid")


def _id(*parts: str) -> str:
    return "-".join(re.sub(r"[^A-Za-z0-9_-]", "_", p) for p in parts)


# ---------------------------------------------------------------- icons

_SI_INNER = {
    "added": '<path d="M8 5v6M5 8h6"/>',
    "removed": '<path d="M5 8h6"/>',
    "changed": '<circle cx="8" cy="8" r="2.2" fill="currentColor" stroke="none"/>',
    "layout": '<path d="M4.75 8h6M8.75 5.75 11 8l-2.25 2.25" fill="none"/>',
    "invalid": '<path d="M8 4.75v4"/><circle cx="8" cy="11.1" r=".85" fill="currentColor" stroke="none"/>',
}


def status_icon(status: str) -> str:
    inner = _SI_INNER.get(status, "")
    return (
        f"<svg class='si s-{status}' viewBox='0 0 16 16' role='img' aria-label='{STATUS_LABELS.get(status, status)}'>"
        "<rect x='1.75' y='1.75' width='12.5' height='12.5' rx='3' fill='none' stroke='currentColor' stroke-width='1.3'/>"
        f"<g stroke='currentColor' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'>{inner}</g></svg>"
    )


def _icon(path: str, cls: str = "ic") -> str:
    return (
        f"<svg class='{cls}' viewBox='0 0 16 16' aria-hidden='true' fill='none' stroke='currentColor' "
        f"stroke-width='1.4' stroke-linecap='round' stroke-linejoin='round'>{path}</svg>"
    )


CHEVRON = _icon('<path d="M4 6l4 4 4-4"/>', "ic chev")
BRANCH = _icon('<circle cx="4.5" cy="3.5" r="1.6"/><circle cx="4.5" cy="12.5" r="1.6"/><circle cx="11.5" cy="4.5" r="1.6"/><path d="M4.5 5.1v5.8M11.5 6.1c0 3.2-7 1.9-7 4.8"/>')
ARROW_LEFT = _icon('<path d="M12.5 8h-9M7 4.5 3.5 8 7 11.5"/>', "ic arrow")
FILE = _icon('<path d="M4 1.75h5.25L12.5 5v9.25H4z"/><path d="M9 1.75V5.25h3.5"/>')
PAGE = _icon('<rect x="1.75" y="3" width="12.5" height="10" rx="1.5"/><path d="M1.75 6h12.5M6 6v7"/>')
REPORT = _icon('<path d="M2.5 13.5V2.5M2.5 13.5h11"/><path d="M5.5 11V8M8.5 11V5.5M11.5 11V7"/>')
FOLDER = _icon('<path d="M1.75 4.25v8.5c0 .55.45 1 1 1h10.5c.55 0 1-.45 1-1v-6.5c0-.55-.45-1-1-1H8L6.5 3.25H2.75c-.55 0-1 .45-1 1z"/>')
RICH = _icon('<rect x="2" y="2.5" width="12" height="11" rx="1.5"/><path d="M5 10.5V8M8 10.5V5.5M11 10.5V7"/>')
CODE = _icon('<path d="M5.5 4.5 2 8l3.5 3.5M10.5 4.5 14 8l-3.5 3.5"/>')
LOGO = _icon('<rect x="2" y="9" width="2.6" height="5" rx=".6"/><rect x="6.7" y="5.5" width="2.6" height="8.5" rx=".6"/><rect x="11.4" y="2" width="2.6" height="12" rx=".6"/>', "ic logo")


# ---------------------------------------------------------------- values


def fmt_value(v: Any) -> str:
    if v is MISSING:
        return '<span class="none">—</span>'
    if is_literal(v):
        v = literal(v)
    if isinstance(v, dict) and "queryRef" in v:
        return f"<code>{escape(projection_name(v))}</code>"
    if isinstance(v, dict) and isinstance(v.get("filters"), list):
        return "<br>".join(fmt_value(f) for f in v["filters"]) or fmt_value([])
    if isinstance(v, dict) and "field" in v and "type" in v:  # one filter
        return f"<code>{escape(field_name(v['field']))} ({escape(str(v['type']))})</code>"
    text = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, separators=(", ", ": "))
    if len(text) > 240:
        text = text[:237] + "…"
    return f"<code>{escape(text)}</code>"


def change_table(changes: list[Change]) -> str:
    if not changes:
        return ""
    rows = "".join(
        f"<tr><td class='path'>{escape(c.path)}</td>"
        f"<td class='old'>{fmt_value(c.old)}</td><td class='new'>{fmt_value(c.new)}</td></tr>"
        for c in changes
    )
    return (
        "<table class='props'><thead><tr><th>Property</th><th>Before</th><th>After</th>"
        f"</tr></thead><tbody>{rows}</tbody></table>"
    )


def stat_text(added: int, removed: int) -> str:
    return f"<span class='stat'><span class='plus'>+{added}</span> <span class='minus'>−{removed}</span></span>"


def diffstat_blocks(added: int, removed: int) -> str:
    """GitHub's five little squares."""
    total = added + removed
    g = round(5 * added / total) if total else 0
    r = min(5 - g, round(5 * removed / total)) if total else 0
    cells = ["a"] * g + ["d"] * r + ["n"] * (5 - g - r)
    return "<span class='blocks' aria-hidden='true'>" + "".join(f"<i class='{c}'></i>" for c in cells) + "</span>"


# ---------------------------------------------------------------- placeholder charts

KIND_BY_TYPE = {
    "bar": ["clusteredBarChart", "barChart", "stackedBarChart", "hundredPercentStackedBarChart"],
    "column": [
        "clusteredColumnChart", "columnChart", "stackedColumnChart",
        "hundredPercentStackedColumnChart", "ribbonChart", "waterfallChart",
        "lineClusteredColumnComboChart", "lineStackedColumnComboChart", "histogram",
    ],
    "line": ["lineChart", "areaChart", "stackedAreaChart", "hundredPercentStackedAreaChart"],
    "pie": ["pieChart", "donutChart", "gauge"],
    "card": ["card", "cardVisual", "multiRowCard", "kpi"],
    "table": ["tableEx", "pivotTable", "table", "matrix"],
    "slicer": ["slicer", "advancedSlicerVisual", "listSlicer", "textSlicer"],
    "map": ["map", "filledMap", "azureMap", "shapeMap", "esriVisual"],
    "scatter": ["scatterChart"],
    "funnel": ["funnel"],
    "treemap": ["treemap", "decompositionTreeVisual"],
    "text": ["textbox"],
    "shape": ["shape", "basicShape", "image", "actionButton", "pageNavigator", "bookmarkNavigator"],
}
_KIND = {t: k for k, types in KIND_BY_TYPE.items() for t in types}

_SVG = {
    "bar": '<svg viewBox="0 0 100 60" preserveAspectRatio="none"><rect x="0" y="4" width="85" height="9"/><rect x="0" y="18" width="62" height="9"/><rect x="0" y="32" width="44" height="9"/><rect x="0" y="46" width="25" height="9"/></svg>',
    "column": '<svg viewBox="0 0 100 60" preserveAspectRatio="none"><rect x="4" y="12" width="13" height="48"/><rect x="24" y="24" width="13" height="36"/><rect x="44" y="6" width="13" height="54"/><rect x="64" y="32" width="13" height="28"/><rect x="84" y="40" width="13" height="20"/></svg>',
    "line": '<svg viewBox="0 0 100 60" preserveAspectRatio="none"><polyline fill="none" stroke="currentColor" stroke-width="2.5" vector-effect="non-scaling-stroke" points="0,48 18,36 34,40 52,20 70,26 86,10 100,14"/></svg>',
    "pie": '<svg viewBox="0 0 60 60"><circle cx="30" cy="30" r="26" opacity=".45"/><path d="M30 30 L30 4 A26 26 0 0 1 54 40 Z"/></svg>',
    "scatter": '<svg viewBox="0 0 100 60" preserveAspectRatio="none"><circle cx="12" cy="44" r="3"/><circle cx="26" cy="36" r="3"/><circle cx="40" cy="40" r="3"/><circle cx="52" cy="24" r="3"/><circle cx="68" cy="28" r="3"/><circle cx="82" cy="12" r="3"/></svg>',
    "funnel": '<svg viewBox="0 0 100 60" preserveAspectRatio="none"><rect x="5" y="4" width="90" height="11"/><rect x="17" y="19" width="66" height="11"/><rect x="29" y="34" width="42" height="11"/><rect x="39" y="49" width="22" height="9"/></svg>',
    "treemap": '<svg viewBox="0 0 100 60" preserveAspectRatio="none"><rect x="0" y="0" width="58" height="60"/><rect x="60" y="0" width="40" height="34" opacity=".7"/><rect x="60" y="36" width="19" height="24" opacity=".5"/><rect x="81" y="36" width="19" height="24" opacity=".35"/></svg>',
    "map": '<svg viewBox="0 0 100 60" preserveAspectRatio="none"><path d="M8 20 Q20 6 36 14 T60 12 T88 18 L92 40 Q76 54 56 46 T22 50 Z" opacity=".55"/><circle cx="34" cy="28" r="3"/><circle cx="62" cy="30" r="4"/></svg>',
    "table": '<svg viewBox="0 0 100 60" preserveAspectRatio="none"><rect x="0" y="0" width="100" height="9"/><rect x="0" y="14" width="100" height="5" opacity=".4"/><rect x="0" y="24" width="100" height="5" opacity=".4"/><rect x="0" y="34" width="100" height="5" opacity=".4"/><rect x="0" y="44" width="100" height="5" opacity=".4"/></svg>',
    "slicer": '<svg viewBox="0 0 100 60" preserveAspectRatio="xMinYMin meet"><rect x="2" y="4" width="8" height="8" fill="none" stroke="currentColor"/><rect x="16" y="6" width="50" height="4"/><rect x="2" y="20" width="8" height="8"/><rect x="16" y="22" width="40" height="4"/><rect x="2" y="36" width="8" height="8" fill="none" stroke="currentColor"/><rect x="16" y="38" width="56" height="4"/></svg>',
}


def visual_body(v: Visual) -> str:
    err = f"<div class='v-err'>{escape(v.error)}</div>" if v.error else ""
    if v.error and not v.raw.get("visual"):
        return err
    return err + _body(v)


def _body(v: Visual) -> str:
    if v.is_group:
        return ""
    kind = _KIND.get(v.visual_type, "generic")
    if kind == "text":
        return f"<div class='v-text'>{escape((v.text or '')[:400])}</div>"
    if kind == "card":
        values = [f for fs in v.fields.values() for f in fs]
        label = escape(values[0]) if values else ""
        return f"<div class='v-card'><b>123</b><span>{label}</span></div>"
    if kind in _SVG:
        return f"<div class='v-chart'>{_SVG[kind]}</div>"
    return ""


def fields_line(v: Visual) -> str:
    if _KIND.get(v.visual_type) in ("card", "text"):
        return ""  # the body already shows it
    parts = [
        f"<span><i>{escape(role)}</i> {escape(', '.join(names))}</span>"
        for role, names in v.fields.items()
        if names
    ]
    return f"<div class='v-fields'>{''.join(parts)}</div>" if parts else ""


# ---------------------------------------------------------------- canvas


def canvas(
    page: Page | None, diffs: list[VisualDiff], side: str, pid: str,
    override: dict[str, str] | None = None,
) -> str:
    """Wireframe of a page. `override` forces a status (colour) per visual name."""
    if page is None:
        msg = "Page does not exist in this version"
        return f"<div class='canvas empty'><p>{msg}</p></div>"

    W, H = page.width, page.height
    visuals = [(d, d.before if side == "before" else d.after) for d in diffs]
    visuals = [(d, v) for d, v in visuals if v is not None]
    z_rank = {z: i for i, z in enumerate(sorted({v.z for _, v in visuals}))}

    def box_style(v: Visual) -> str:
        return (
            f"left:{v.x / W * 100:.3f}%;top:{v.y / H * 100:.3f}%;"
            f"width:{v.width / W * 100:.3f}%;height:{v.height / H * 100:.3f}%;"
        )

    out: list[str] = []
    for d, v in visuals:
        status = d.status
        # Only the side where something happened gets the colour.
        if side == "before" and status == "added" or side == "after" and status == "removed":
            status = "unchanged"
        if override and d.name in override:
            status = override[d.name]
        classes = ["v", f"s-{status}"]
        if v.is_group:
            classes.append("group")
        if v.hidden:
            classes.append("is-hidden")
        z = 0 if v.is_group else z_rank[v.z] + 1
        tag = "a" if d.status in CHANGED or (override and d.name in override) else "div"
        href = f" href='#{_id(pid, d.name)}'" if tag == "a" else ""
        title = f"{v.label} — {friendly_type(v.visual_type)} ({d.name})"
        hidden = "<span class='flag'>hidden</span>" if v.hidden else ""
        head = (
            "" if v.visual_type == "textbox"
            else f"<div class='v-head'><span class='v-title'>{escape(v.label)}</span>{hidden}</div>"
        )
        out.append(
            f"<{tag} class='{' '.join(classes)}' data-v='{escape(d.name)}'{href} "
            f"style='{box_style(v)}z-index:{z}' title='{escape(title)}'>"
            f"{head}{visual_body(v)}{fields_line(v)}</{tag}>"
        )
        # Dashed outline where a moved/resized visual used to be.
        if side == "after" and d.before is not None and {"moved", "resized"} & set(d.kinds):
            out.append(
                f"<div class='ghost' data-v='{escape(d.name)}' "
                f"style='{box_style(d.before)}z-index:{z}'></div>"
            )
    return f"<div class='canvas' style='aspect-ratio:{W:g}/{H:g}'>{''.join(out)}</div>"


# ---------------------------------------------------------------- file blocks


def _lines(v: Visual | None) -> list[str]:
    """Lines for the source diff. Valid JSON is re-printed in one standard layout
    so whitespace-only reformatting doesn't show up as changes."""
    if v is None:
        return []
    if v.raw and not v.error:
        return json.dumps(v.raw, indent=2, ensure_ascii=False).splitlines()
    return v.source.splitlines()


def file_block(
    fid: str, status: str, title: str, path: str, stats: tuple[int, int],
    body: str, *, extra_head: str = "", folded: bool = False, viewed: bool = True,
    toggle: tuple[str, str] | None = None, cls: str = "", data_v: str = "",
) -> str:
    """One GitHub-style file: sticky header + collapsible body.

    `toggle` = (rich_html, source_html) adds the rich / source diff switch."""
    fold = f"<input type='checkbox' class='fold' id='fold-{fid}' hidden{' checked' if folded else ''}>"
    modes = seg = ""
    if toggle:
        modes = (
            f"<input type='radio' class='m-rich' name='m-{fid}' id='rich-{fid}' hidden checked>"
            f"<input type='radio' class='m-src' name='m-{fid}' id='src-{fid}' hidden>"
        )
        seg = (
            f"<span class='seg'><label for='rich-{fid}' class='l-rich' title='Display the rich diff'>{RICH}</label>"
            f"<label for='src-{fid}' class='l-src' title='Display the source diff'>{CODE}</label></span>"
        )
        body = f"<div class='rich'>{toggle[0]}</div><div class='src'>{toggle[1]}</div>"
    view = (
        f"<label class='viewed'><input type='checkbox' class='vw' id='vw-{fid}'>Viewed</label>" if viewed else ""
    )
    a, r = stats
    dv = f' data-v="{escape(data_v)}"' if data_v else ""
    return f"""<section class="file s-{status} {cls}" id="{fid}"{dv}>{fold}{modes}
<header class="file-head">
  <label for="fold-{fid}" class="chevbtn" title="Toggle">{CHEVRON}</label>
  {status_icon(status)}
  <span class="fname">{title}</span>
  <span class="fpath" title="{escape(path)}">{escape(path)}</span>
  {extra_head}
  <span class="grow"></span>
  {stat_text(a, r)}{diffstat_blocks(a, r)}{seg}{view}
</header>
<div class="file-body"><div class="file-inner">{body}</div></div>
</section>"""


def visual_rich(d: VisualDiff) -> str:
    v = d.current
    out: list[str] = []
    if d.after is not None and d.after.error:
        out.append(f"<div class='flash err'>{escape(d.after.error)} — drawn from the first side of the conflict.</div>")
    if d.status in ("added", "removed"):
        rows = [
            ("Type", escape(friendly_type(v.visual_type))),
            ("Position", f"<code>x {v.x:g}, y {v.y:g}</code> · <code>{v.width:g} × {v.height:g}</code>"),
        ]
        rows += [(escape(role), ", ".join(f"<code>{escape(n)}</code>" for n in names)) for role, names in v.fields.items()]
        out.append("<dl class='kv'>" + "".join(f"<dt>{k}</dt><dd>{val}</dd>" for k, val in rows) + "</dl>")
    if d.fields:
        items = []
        for fc in d.fields:
            rem = "".join(f"<span class='tok del'>−&nbsp;{escape(f)}</span>" for f in fc.removed)
            add = "".join(f"<span class='tok add'>+&nbsp;{escape(f)}</span>" for f in fc.added)
            items.append(f"<li><span class='role'>{escape(fc.role)}</span>{rem}{add}</li>")
        out.append(f"<ul class='fieldchanges'>{''.join(items)}</ul>")
    out.append(change_table(d.changes))
    return "".join(out) or "<div class='blankslate'>No property changes.</div>"


def visual_block(d: VisualDiff, pid: str, rel_dir: str) -> str:
    v = d.current
    fid = _id(pid, d.name)
    a, b = _lines(d.before), _lines(d.after)
    chips = "".join(f"<span class='label'>{escape(KIND_LABELS[k])}</span>" for k in d.kinds)
    title = f"{escape(v.label)} <span class='muted'>{escape(friendly_type(v.visual_type))}</span>"
    return file_block(
        fid, d.status, title, f"{rel_dir}/visuals/{d.name}/visual.json", line_stats(a, b), "",
        extra_head=chips, toggle=(visual_rich(d), split_table(a, b)), cls="vfile", data_v=d.name,
    )


def page_block(p: PageDiff, rid: str, folder: str, base: str, head: str) -> str:
    pid = _id("p", rid, p.name)
    page = p.current
    changed = [d for d in p.visuals if d.status in CHANGED]
    added = removed = 0
    for d in changed:
        x, y = line_stats(_lines(d.before), _lines(d.after))
        added, removed = added + x, removed + y
    counts = "".join(
        f"<span class='cnt s-{s}'>{status_icon(s)}{n}</span>"
        for s in CHANGED if (n := p.count(s))
    )
    hidden = "<span class='label'>hidden page</span>" if page.hidden else ""
    settings = (
        f"<div class='subhead'>Page settings</div>{change_table(p.changes)}" if p.changes else ""
    )
    panes = f"""<div class="panes">
  <div class="pane"><div class="pane-head del"><span class="side">−</span><b>Before</b><code>{escape(base)}</code></div>{canvas(p.before, p.visuals, "before", pid)}</div>
  <div class="pane"><div class="pane-head add"><span class="side">+</span><b>After</b><code>{escape(head)}</code></div>{canvas(p.after, p.visuals, "after", pid)}</div>
</div>"""
    rel_dir = f"{folder}/definition/pages/{p.name}"
    vfiles = "".join(visual_block(d, pid, rel_dir) for d in changed)
    body = settings + panes + (f"<div class='vfiles'>{vfiles}</div>" if vfiles else "")
    block = file_block(
        pid, p.status, f"{PAGE} {escape(page.display_name)}", rel_dir, (added, removed), body,
        extra_head=hidden + counts, folded=p.status == "unchanged", viewed=False, cls="pfile",
    )
    return block + hover_css(pid, p.visuals)


def hover_css(pid: str, diffs: list[VisualDiff]) -> str:
    """Hovering a visual (on either canvas or its block) highlights it everywhere."""
    rules = []
    for d in diffs:
        n = d.name.replace('"', "")
        rules.append(
            f'#{pid}:has([data-v="{n}"]:hover) .v[data-v="{n}"],'
            f'#{pid}:has(#{_id(pid, d.name)}:target) .v[data-v="{n}"]'
        )
    if not rules:
        return ""
    return f"<style>{','.join(rules)}{{outline:2px solid var(--focus);outline-offset:1px;z-index:40!important}}</style>"


def other_file_block(f: FileDiff, index: int) -> str:
    fid = f"f{index}"
    body = (
        "<div class='blankslate'>Binary file not shown.</div>" if f.binary else split_table(f.old, f.new)
    )
    name = f.path.rsplit("/", 1)[-1]
    return file_block(fid, f.status, f"{FILE} {escape(name)}", f.path, line_stats(f.old, f.new), body)


# ---------------------------------------------------------------- sidebar tree


def tree(diff: ProjectDiff) -> str:
    out: list[str] = []
    for i, r in enumerate(diff.reports):
        rid = f"r{i}"
        rows = []
        for p in r.pages:
            pid = _id("p", rid, p.name)
            changed = [d for d in p.visuals if d.status in CHANGED]
            link = (
                f"<a href='#{pid}' class='row-link'>{PAGE}<span class='nm'>{escape(p.current.display_name)}</span></a>"
                f"{status_icon(p.status)}"
            )
            if not changed:
                rows.append(f"<div class='row l1 leaf s-{p.status}'><span class='sp'></span>{link}</div>")
                continue
            kids = "".join(
                f"<a class='row l2 s-{d.status}' href='#{_id(pid, d.name)}' title='{escape(d.current.label)}'>"
                f"{FILE}<span class='nm'>{escape(d.current.label)}</span>{status_icon(d.status)}</a>"
                for d in changed
            )
            rows.append(
                f"<details open><summary class='row l1 s-{p.status}'>{CHEVRON}{link}</summary>{kids}</details>"
            )
        out.append(
            f"<details open><summary class='row l0'>{CHEVRON}{REPORT}<span class='nm'>{escape(r.folder)}</span></summary>"
            f"{''.join(rows)}</details>"
        )
    if diff.files:
        kids = "".join(
            f"<a class='row l1 s-{f.status}' href='#f{i}' title='{escape(f.path)}'>{FILE}"
            f"<span class='nm'>{escape(f.path.rsplit('/', 1)[-1])}</span>{status_icon(f.status)}</a>"
            for i, f in enumerate(diff.files)
        )
        out.append(
            f"<details open><summary class='row l0'>{CHEVRON}{FOLDER}<span class='nm'>Other files</span></summary>{kids}</details>"
        )
    return "".join(out)


def viewed_css(diff: ProjectDiff, total: int) -> str:
    """Dim viewed items in the tree, and show the "n / total viewed" counter."""
    ids = [
        _id(_id("p", f"r{i}", p.name), d.name)
        for i, r in enumerate(diff.reports) for p in r.pages for d in p.visuals if d.status in CHANGED
    ] + [f"f{i}" for i in range(len(diff.files))]
    rules = ",".join(f'body:has(#vw-{x}:checked) .tree a[href="#{x}"]' for x in ids)
    css = f'.viewed-count::after{{content:counter(viewed) " / {total} viewed"}}'
    if rules:
        css += f"{rules}{{opacity:.45}}"
    return f"<style>{css}</style>"


# ---------------------------------------------------------------- page


def render(diff: ProjectDiff, title: str = "PBIP diff") -> str:
    visual_diffs = [(r, p, d) for r in diff.reports for p in r.pages for d in p.visuals if d.status in CHANGED]
    added = removed = 0
    for _, _, d in visual_diffs:
        x, y = line_stats(_lines(d.before), _lines(d.after))
        added, removed = added + x, removed + y
    for f in diff.files:
        x, y = line_stats(f.old, f.new)
        added, removed = added + x, removed + y
    total = len(visual_diffs) + len(diff.files)

    counts = {s: sum(1 for *_, d in visual_diffs if d.status == s) for s in CHANGED}
    count_html = "".join(
        f"<span class='cnt s-{s}' title='{STATUS_LABELS[s]}'>{status_icon(s)}{n}</span>"
        for s, n in counts.items() if n
    )

    warnings = "".join(f"<div class='flash warn'>{escape(w)}</div>" for w in diff.warnings)
    blocks = []
    for i, r in enumerate(diff.reports):
        if len(diff.reports) > 1:
            blocks.append(f"<h2 class='group-title'>{REPORT}{escape(r.folder)}</h2>")
        if r.order_changed:
            blocks.append("<div class='flash'>Page order changed (see <code>pages.json</code> below).</div>")
        blocks += [page_block(p, f"r{i}", r.folder, diff.base_label, diff.head_label) for p in r.pages]
    if diff.files:
        blocks.append(f"<h2 class='group-title'>{FOLDER}Other files</h2>")
        blocks += [other_file_block(f, i) for i, f in enumerate(diff.files)]

    empty = ""
    if not diff.reports:
        empty = "<div class='blankslate big'>No PBIR report found in either version.</div>"
    elif total == 0:
        empty = "<div class='blankslate big'>No differences between these versions.</div>"

    legend = "".join(
        f"<span>{status_icon(s)}{STATUS_LABELS[s]}</span>" for s in ("added", "removed", "changed", "layout")
    ) + "<span><i class='ghostkey'></i>Previous position</span>"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>{CSS}</style></head>
<body>
<header class="appbar">
  <div class="brand">{LOGO}<b>{escape(title)}</b></div>
  <div class="refs"><span class="ref" title="base">{BRANCH}<span>{escape(diff.base_label)}</span></span>{ARROW_LEFT}<span class="ref" title="compare">{BRANCH}<span>{escape(diff.head_label)}</span></span></div>
  <span class="grow"></span>
  <div class="totals">{count_html}{stat_text(added, removed)}{diffstat_blocks(added, removed)}</div>
</header>
<div class="layout">
  <aside class="sidebar">
    <div class="side-title"><span>Changes</span><span class="counter">{total}</span></div>
    <nav class="tree">{tree(diff)}</nav>
    <div class="legend">{legend}</div>
  </aside>
  <main>{warnings}{empty}{''.join(blocks)}</main>
</div>
<footer class="statusbar">
  <span>{BRANCH}{escape(diff.head_label)}</span>
  <span>{count_html}</span>
  <span class="grow"></span>
  <span class="viewed-count"></span>
</footer>
{viewed_css(diff, total)}
</body></html>"""


CSS = """
:root{
  --bg:#ffffff;--bg-subtle:#f6f8fa;--bg-inset:#f6f8fa;--canvas:#ffffff;
  --fg:#1f2328;--muted:#59636e;--border:#d1d9e0;--border-muted:#d1d9e0b3;
  --accent:#0969da;--focus:#0969da;
  --add:#1a7f37;--del:#d1242f;--mod:#9a6700;--move:#0969da;--bad:#8250df;--neutral:#818b98;
  --add-bg:#dafbe1;--del-bg:#ffebe9;--mod-bg:#fff8c5;--move-bg:#ddf4ff;--bad-bg:#fbefff;
  --add-line:#e6ffec;--add-num:#d1f8d9;--add-word:#abf2bc;
  --del-line:#ffebe9;--del-num:#ffcecb;--del-word:#ff818266;
  --hunk:#ddf4ff;--hunk-fg:#59636e;--empty:#f6f8fa;
  --status:#0969da;--status-fg:#ffffff;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
  --appbar:56px;
  color-scheme:light;
}
@media (prefers-color-scheme: dark){:root{
  --bg:#0d1117;--bg-subtle:#151b23;--bg-inset:#010409;--canvas:#161b22;
  --fg:#f0f6fc;--muted:#9198a1;--border:#3d444d;--border-muted:#3d444db3;
  --accent:#4493f8;--focus:#4493f8;
  --add:#3fb950;--del:#f85149;--mod:#d29922;--move:#4493f8;--bad:#ab7df8;--neutral:#656c76;
  --add-bg:#2ea04326;--del-bg:#f8514926;--mod-bg:#bb800926;--move-bg:#388bfd26;--bad-bg:#ab7df826;
  --add-line:#2ea04326;--add-num:#3fb9504d;--add-word:#2ea04366;
  --del-line:#f851491a;--del-num:#f851494d;--del-word:#f8514966;
  --hunk:#388bfd1a;--hunk-fg:#9198a1;--empty:#151b23;
  --status:#1f6feb;--status-fg:#ffffff;
  color-scheme:dark;
}}
*{box-sizing:border-box}
html{scroll-padding-top:calc(var(--appbar) + 52px)}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans",Helvetica,Arial,sans-serif;counter-reset:viewed}
code{font-family:var(--mono);font-size:12px}
a{color:inherit;text-decoration:none}
.grow{flex:1}
.muted{color:var(--muted);font-weight:400}
.ic{width:16px;height:16px;flex:none;color:var(--muted)}
.si{width:16px;height:16px;flex:none;color:var(--c,var(--neutral))}
.s-added{--c:var(--add);--cbg:var(--add-bg)}.s-removed{--c:var(--del);--cbg:var(--del-bg)}
.s-changed{--c:var(--mod);--cbg:var(--mod-bg)}.s-layout{--c:var(--move);--cbg:var(--move-bg)}
.s-invalid{--c:var(--bad);--cbg:var(--bad-bg)}.s-unchanged,.s-minor{--c:var(--neutral);--cbg:transparent}

/* app bar */
.appbar{position:sticky;top:0;z-index:60;height:var(--appbar);display:flex;align-items:center;gap:16px;padding:0 16px;
  background:var(--bg-subtle);border-bottom:1px solid var(--border);white-space:nowrap}
.brand{display:flex;align-items:center;gap:8px;font-size:14px;min-width:0}
.brand b{overflow:hidden;text-overflow:ellipsis}
.logo{width:20px;height:20px;color:var(--fg);fill:currentColor;stroke:none}
.refs{display:flex;align-items:center;gap:6px;min-width:0}
.ref{display:inline-flex;align-items:center;gap:4px;max-width:340px;padding:0 6px;border-radius:6px;font:12px/20px var(--mono);
  color:var(--accent);background:color-mix(in srgb,var(--accent) 12%,transparent)}
.ref span{overflow:hidden;text-overflow:ellipsis}
.ref .ic{color:inherit;width:14px;height:14px}
.arrow{width:14px;height:14px}
.totals{display:flex;align-items:center;gap:12px}
.cnt{display:inline-flex;align-items:center;gap:4px;font-size:12px;font-weight:600;color:var(--c)}
.stat{font:600 12px var(--mono)}.stat .plus{color:var(--add)}.stat .minus{color:var(--del)}
.blocks{display:inline-flex;gap:1px}.blocks i{width:8px;height:8px;border-radius:1px;background:var(--neutral);opacity:.35}
.blocks i.a{background:var(--add);opacity:1}.blocks i.d{background:var(--del);opacity:1}

/* layout */
.layout{display:grid;grid-template-columns:300px minmax(0,1fr)}
.sidebar{position:sticky;top:var(--appbar);height:calc(100vh - var(--appbar) - 24px);overflow:auto;
  border-right:1px solid var(--border);background:var(--bg);display:flex;flex-direction:column;font-size:13px}
.side-title{display:flex;align-items:center;gap:8px;padding:12px 16px 8px;font-size:11px;font-weight:600;
  letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.counter{display:inline-block;min-width:20px;padding:0 6px;border-radius:2em;background:var(--border-muted);color:var(--fg);
  font-size:11px;line-height:18px;text-align:center;letter-spacing:0}
.tree{padding:0 8px 12px;flex:1}
.tree details>summary{list-style:none}.tree details>summary::-webkit-details-marker{display:none}
.row{display:flex;align-items:center;gap:6px;height:26px;padding-right:8px;border-radius:6px;cursor:pointer;color:var(--fg);min-width:0}
.row:hover{background:var(--bg-subtle)}
.row .nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.row-link{display:flex;align-items:center;gap:6px;flex:1;min-width:0}
.row.l0{padding-left:4px;font-weight:600}.row.l1{padding-left:20px}.row.l2{padding-left:46px}
.row.leaf{color:var(--muted)}.row.leaf .sp{width:0}
.row .chev{transition:transform .12s}
.tree details:not([open])>summary .chev{transform:rotate(-90deg)}
.legend{display:grid;gap:4px;padding:12px 16px;border-top:1px solid var(--border);color:var(--muted);font-size:12px}
.legend span{display:flex;align-items:center;gap:8px}
.ghostkey{display:inline-block;width:16px;height:12px;border:1.5px dashed var(--move);border-radius:3px}

main{padding:16px 24px 64px;min-width:0}
.group-title{display:flex;align-items:center;gap:8px;font-size:14px;margin:24px 0 8px}

/* file blocks */
.file{border:1px solid var(--border);border-radius:6px;margin-bottom:16px;background:var(--bg)}
.file-head{position:sticky;top:var(--appbar);z-index:30;display:flex;align-items:center;gap:8px;min-height:44px;padding:6px 8px 6px 4px;
  background:var(--bg-subtle);border-bottom:1px solid var(--border);border-radius:6px 6px 0 0;font-size:13px;min-width:0;flex-wrap:wrap}
.vfile .file-head{position:static;min-height:40px}
.fname{display:inline-flex;align-items:center;gap:6px;font-weight:600;white-space:nowrap}
.fpath{font:12px var(--mono);color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;flex:0 1 auto;max-width:48ch}
.chevbtn{display:grid;place-items:center;width:28px;height:28px;border-radius:6px;cursor:pointer}
.chevbtn:hover{background:var(--border-muted)}
.chevbtn .chev{transition:transform .12s}
.fold:checked~.file-head .chev,.file:has(>.file-head .vw:checked)>.file-head .chev{transform:rotate(-90deg)}
.fold:checked~.file-body,.file:has(>.file-head .vw:checked)>.file-body{height:0;overflow:hidden}
.fold:checked~.file-head,.file:has(>.file-head .vw:checked)>.file-head{border-bottom-color:transparent;border-radius:6px}
.file-inner{padding:0}
.pfile>.file-body>.file-inner{padding:16px}
.label{display:inline-block;padding:0 7px;border:1px solid var(--border);border-radius:2em;font-size:12px;line-height:18px;color:var(--muted);white-space:nowrap}
.seg{display:inline-flex;border:1px solid var(--border);border-radius:6px;overflow:hidden}
.seg label{display:grid;place-items:center;width:30px;height:26px;cursor:pointer;background:var(--bg)}
.seg label+label{border-left:1px solid var(--border)}
.m-rich:checked~.file-head .l-rich,.m-src:checked~.file-head .l-src{background:var(--border-muted)}
.m-rich:checked~.file-head .l-rich .ic,.m-src:checked~.file-head .l-src .ic{color:var(--fg)}
.m-rich:checked~.file-body .src,.m-src:checked~.file-body .rich{display:none}
.viewed{display:inline-flex;align-items:center;gap:6px;height:28px;padding:0 10px;border:1px solid var(--border);border-radius:6px;
  font-size:12px;cursor:pointer;background:var(--bg);user-select:none}
.viewed input{margin:0;accent-color:var(--accent)}
.vw:checked{counter-increment:viewed}
.viewed:has(input:checked){background:color-mix(in srgb,var(--accent) 12%,var(--bg));border-color:var(--accent)}
.file:target{box-shadow:0 0 0 2px var(--focus)}

/* page panes (VS Code diff editor) */
.subhead{font-weight:600;font-size:13px;margin-bottom:8px}
.panes{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.pane{min-width:0;border:1px solid var(--border);border-radius:6px;overflow:hidden;background:var(--bg-inset)}
.pane-head{display:flex;align-items:center;gap:8px;height:30px;padding:0 10px;font-size:12px;border-bottom:1px solid var(--border);white-space:nowrap;overflow:hidden}
.pane-head code{color:var(--muted);overflow:hidden;text-overflow:ellipsis}
.pane-head .side{font:700 13px var(--mono);width:12px}
.pane-head.del{background:var(--del-line)}.pane-head.del .side{color:var(--del)}
.pane-head.add{background:var(--add-line)}.pane-head.add .side{color:var(--add)}
.canvas{position:relative;width:100%;background:var(--canvas);overflow:hidden;container-type:inline-size}
.canvas.empty{aspect-ratio:16/9;display:grid;place-items:center;color:var(--muted);font-size:13px;
  background:repeating-linear-gradient(-45deg,transparent 0 8px,var(--bg-subtle) 8px 16px)}
.v{position:absolute;display:flex;flex-direction:column;overflow:hidden;color:var(--fg);
  background:var(--bg);border:1px solid var(--border);border-radius:3px;padding:max(2px,.5cqw);gap:max(1px,.3cqw);font-size:max(8px,1cqw)}
.v.s-unchanged,.v.s-minor{opacity:.7}
.v:not(.s-unchanged):not(.s-minor){border:2px solid var(--c);background:color-mix(in srgb,var(--cbg) 70%,var(--bg))}
a.v:hover{cursor:pointer}
.v.group{background:transparent!important;border-style:dashed;pointer-events:none}
.v.is-hidden{opacity:.4;background-image:repeating-linear-gradient(-45deg,transparent 0 6px,var(--bg-subtle) 6px 12px)}
.v-head{display:flex;align-items:center;min-width:0;font-weight:600}
.v-title{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.flag{font-size:.85em;padding:0 4px;border-radius:3px;border:1px solid var(--border);color:var(--muted);margin-left:4px;font-weight:400}
.v-chart{flex:1;min-height:0;color:var(--c,var(--neutral));opacity:.5}
.v.s-unchanged .v-chart,.v.s-minor .v-chart{color:var(--neutral)}
.v-chart svg{width:100%;height:100%;display:block;fill:currentColor}
.v-card{flex:1;display:flex;flex-direction:column;justify-content:center;align-items:center;min-height:0}
.v-card b{font-size:2.2em;line-height:1;color:var(--muted)}.v-card span{color:var(--muted);font-size:.9em}
.v-text{flex:1;overflow:hidden;white-space:pre-wrap;color:var(--muted)}
.v-err{color:var(--bad);font-weight:600}
.v-fields{display:flex;flex-wrap:wrap;gap:0 .8em;color:var(--muted);font-size:.85em;max-height:2.8em;overflow:hidden}
.v-fields i{font-style:normal;opacity:.7;margin-right:.3em}
.ghost{position:absolute;border:2px dashed var(--move);border-radius:3px;pointer-events:none;opacity:.8}
.vfiles{margin-top:16px}
.vfiles .file{margin-bottom:12px}

/* rich diff */
.rich{padding:12px 16px}
.kv{display:grid;grid-template-columns:max-content 1fr;gap:4px 16px;margin:0 0 8px;font-size:13px}
.kv dt{color:var(--muted)}.kv dd{margin:0}
.fieldchanges{list-style:none;margin:0 0 12px;padding:0;display:grid;gap:6px}
.fieldchanges li{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.role{font-size:12px;color:var(--muted);min-width:64px}
.tok{font:12px/20px var(--mono);padding:0 6px;border-radius:6px}
.tok.add{background:var(--add-line);color:var(--add);box-shadow:inset 0 0 0 1px var(--add-num)}
.tok.del{background:var(--del-line);color:var(--del);box-shadow:inset 0 0 0 1px var(--del-num);text-decoration:line-through}
table.props{width:100%;border-collapse:separate;border-spacing:0;font-size:12px;table-layout:fixed;border:1px solid var(--border);border-radius:6px;overflow:hidden}
table.props th{text-align:left;font-weight:600;padding:6px 10px;background:var(--bg-subtle);border-bottom:1px solid var(--border)}
table.props td{padding:6px 10px;vertical-align:top;border-top:1px solid var(--border-muted);overflow-wrap:anywhere}
table.props tr:first-child td{border-top:0}
table.props td.path{font-family:var(--mono);color:var(--muted);width:44%}
td.old{background:var(--del-line)}td.new{background:var(--add-line)}
td.old code{color:var(--del)}td.new code{color:var(--add)}
.none{color:var(--muted)}

/* source diff (GitHub split view) */
.splitwrap{overflow-x:auto}
table.split{width:100%;border-collapse:collapse;table-layout:fixed;font:12px/20px var(--mono)}
table.split col.cn{width:52px}
table.split td{padding:0 10px;vertical-align:top;white-space:pre-wrap;overflow-wrap:anywhere}
table.split td.n{text-align:right;color:var(--muted);user-select:none;padding:0 8px;cursor:default}
table.split td.c.ctx+td.n,table.split td.c.del+td.n,table.split td.c.empty+td.n{border-left:1px solid var(--border-muted)}
td.c.del{background:var(--del-line)}td.n.del{background:var(--del-num)}
td.c.add{background:var(--add-line)}td.n.add{background:var(--add-num)}
td.c.empty,td.n.empty{background:var(--empty)}
td.c.del .x{background:var(--del-word);border-radius:2px}
td.c.add .x{background:var(--add-word);border-radius:2px}
tr.hunk td{background:var(--hunk);color:var(--hunk-fg);padding-top:4px;padding-bottom:4px}

/* misc */
.flash{padding:12px 16px;border:1px solid color-mix(in srgb,var(--accent) 40%,transparent);background:color-mix(in srgb,var(--accent) 8%,var(--bg));border-radius:6px;margin-bottom:16px;font-size:13px}
.flash.warn{border-color:color-mix(in srgb,var(--mod) 40%,transparent);background:var(--mod-bg)}
.flash.err{border-color:color-mix(in srgb,var(--bad) 40%,transparent);background:var(--bad-bg);margin-bottom:12px}
.blankslate{padding:24px;text-align:center;color:var(--muted);font-size:13px}
.blankslate.big{padding:64px;border:1px solid var(--border);border-radius:6px}

/* VS Code status bar */
.statusbar{position:fixed;left:0;right:0;bottom:0;z-index:70;height:24px;display:flex;align-items:center;gap:16px;padding:0 10px;
  background:var(--status);color:var(--status-fg);font-size:12px;white-space:nowrap;overflow:hidden}
.statusbar span{display:inline-flex;align-items:center;gap:6px}
.statusbar .ic,.statusbar .si{color:var(--status-fg)}
.statusbar .cnt{color:var(--status-fg);font-weight:400}
main{padding-bottom:56px}

@media (max-width:1100px){.panes{grid-template-columns:1fr}}
@media (max-width:900px){
  :root{--appbar:0px}
  .appbar{position:static;height:auto;flex-wrap:wrap;padding:10px 16px;white-space:normal}
  .layout{display:block}
  .sidebar{position:static;height:auto;max-height:45vh;border-right:0;border-bottom:1px solid var(--border)}
  main{padding:16px}
  .fpath{display:none}
  .file-head{position:static}
}
"""
