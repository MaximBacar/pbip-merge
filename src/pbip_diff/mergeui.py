"""HTML for the merge resolver (served by mergecli)."""

from __future__ import annotations

import json
import re
from html import escape
from typing import Any

from .diff import MISSING, diff_page, json_diff
from .merge import BOTH, CURRENT, INCOMING, MERGE, Conflict, Decision, FileMerge, MergeSession, TextConflict
from .model import Page, Report, find_reports, load_report
from .render import (
    BRANCH, CHEVRON, CSS, FILE, FOLDER, LOGO, PAGE, REPORT, _icon, _id, _theme_attr, canvas, change_table,
    fmt_value, hover_css,
)
from .textdiff import split_table

PAGE_FILE = re.compile(
    r"^(?P<report>[^/]+\.Report)/definition/pages/(?P<page>[^/]+)/"
    r"(?:visuals/(?P<visual>[^/]+)/visual\.json|page\.json)$"
)

CHECK = _icon('<rect x="1.75" y="1.75" width="12.5" height="12.5" rx="3"/><path d="M5 8.2l2 2 4-4.4"/>', "si s-ok")
ALERT = _icon('<rect x="1.75" y="1.75" width="12.5" height="12.5" rx="3"/><path d="M8 4.75v4"/><circle cx="8" cy="11.1" r=".5"/>', "si s-conflict")
MERGE_IC = _icon('<circle cx="4.5" cy="3.5" r="1.6"/><circle cx="4.5" cy="12.5" r="1.6"/><circle cx="11.5" cy="12.5" r="1.6"/><path d="M4.5 5.1v5.8M4.5 5.5c0 4 7 2.5 7 5.4"/>')


def file_state(f: FileMerge, d: Decision) -> str:
    if d.strategy is None:
        return "conflict"
    if d.strategy == MERGE and any(c not in d.choices for c in f.conflict_ids):
        return "conflict"
    return "ok"


def _parse(data: bytes | None) -> Any:
    if data is None:
        return MISSING
    try:
        return json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return MISSING


def _lines(data: bytes | None) -> list[str]:
    return data.decode("utf-8-sig", "replace").splitlines() if data else []


def _side_view(f: FileMerge, side: bytes | None, what: str) -> str:
    if side is None:
        return f"<div class='blankslate'>The file is deleted in {what}.</div>"
    if f.kind == "json" and f.base is not None:
        table = change_table(json_diff(_parse(f.base), _parse(side)))
        return table or "<div class='blankslate'>Same as base.</div>"
    if f.kind == "binary":
        return "<div class='blankslate'>Binary file.</div>"
    return split_table(_lines(f.base), _lines(side))


def _json_option(name: str, value: str, checked: bool, title: str, body: str) -> str:
    return (
        f"<label class='opt opt-{value}'><input type='radio' name='{name}' value='{value}'{' checked' if checked else ''}>"
        f"<span class='opt-title'>{title}</span><span class='opt-body'>{body}</span></label>"
    )


def _merge_view(n: int, f: FileMerge, d: Decision, labels: tuple[str, str]) -> str:
    if not f.can_merge:
        return "<div class='blankslate'>This file can't be merged: keep the current or the incoming version.</div>"
    out: list[str] = []
    if f.conflicts:
        rows = []
        for k, c in enumerate(f.conflicts):
            name = f"c{n}_{k}"
            cid = f.conflict_ids[k]
            chosen = d.choices.get(cid)
            if isinstance(c, Conflict):
                rows.append(
                    f"<div class='conflict'><div class='c-path'>{ALERT}<code>{escape(c.path)}</code>"
                    f"<span class='muted'>base {fmt_value(c.base)}</span></div><div class='opts'>"
                    + _json_option(name, CURRENT, chosen == CURRENT, f"Current <span class='muted'>{escape(labels[0])}</span>", fmt_value(c.current))
                    + _json_option(name, INCOMING, chosen == INCOMING, f"Incoming <span class='muted'>{escape(labels[1])}</span>", fmt_value(c.incoming))
                    + "</div></div>"
                )
            else:
                pre = lambda lines: f"<pre>{escape(''.join(lines)) or '<span class=none>(nothing)</span>'}</pre>"
                rows.append(
                    f"<div class='conflict'><div class='c-path'>{ALERT}<span>Conflict {k + 1}</span></div><div class='opts three'>"
                    + _json_option(name, CURRENT, chosen == CURRENT, f"Current <span class='muted'>{escape(labels[0])}</span>", pre(c.current))
                    + _json_option(name, INCOMING, chosen == INCOMING, f"Incoming <span class='muted'>{escape(labels[1])}</span>", pre(c.incoming))
                    + _json_option(name, BOTH, chosen == BOTH, "Both <span class='muted'>current, then incoming</span>", pre(c.current + c.incoming))
                    + "</div></div>"
                )
        out.append(f"<div class='subhead'>{len(f.conflicts)} conflict{'s' * (len(f.conflicts) != 1)} — pick a side for each</div>{''.join(rows)}")
    else:
        out.append(f"<div class='flash ok'>{CHECK}No conflicts: changes from both sides combine cleanly.</div>")

    # What the merge brings on top of the current version
    result = f.result(MERGE, d.choices)
    if f.kind == "json":
        table = change_table(json_diff(_parse(f.current), _parse(result)))
        what = table or "<div class='blankslate'>Result is the same as current.</div>"
    else:
        what = split_table(_lines(f.current), _lines(result))
    out.append(f"<div class='subhead'>Result compared with current</div>{what}")
    return "".join(out)


def merge_block(n: int, f: FileMerge, d: Decision, fid: str, title: str, labels: tuple[str, str], data_v: str = "") -> str:
    state = file_state(f, d)
    icon = CHECK if state == "ok" else ALERT
    radios = "".join(
        f"<input type='radio' class='st st-{v}' name='s{n}' value='{v}' id='s{n}-{v}' hidden{' checked' if d.strategy == v else ''}>"
        for v in (CURRENT, INCOMING, MERGE) if v != MERGE or f.can_merge
    )
    seg = (
        f"<span class='seg text'><label for='s{n}-current' class='l-current'>Keep current</label>"
        f"<label for='s{n}-incoming' class='l-incoming'>Keep incoming</label>"
        + (f"<label for='s{n}-merge' class='l-merge'>{MERGE_IC}Merge</label>" if f.can_merge else "")
        + "</span>"
    )
    nconf = len(f.conflicts)
    badge = (
        f"<span class='label conflict-label'>{nconf} conflict{'s' * (nconf != 1)}</span>" if nconf
        else ("<span class='label ok-label'>auto-merge</span>" if f.can_merge else "")
    )
    dv = f' data-v="{escape(data_v)}"' if data_v else ""
    return f"""<section class="file mfile st-{state}" id="{fid}"{dv}>{radios}
<header class="file-head">
  <span class="chevbtn"></span>{icon}
  <span class="fname">{title}</span>
  <span class="fpath" title="{escape(f.path)}">{escape(f.path)}</span>
  <span class="label">{escape(f.reason)}</span>{badge}
  <span class="grow"></span>{seg}
</header>
<div class="file-body"><div class="file-inner">
  <div class="pane-hint blankslate">Choose how to resolve this file.</div>
  <div class="pane pane-current"><div class="subhead">Current changes compared with base</div>{_side_view(f, f.current, "current")}</div>
  <div class="pane pane-incoming"><div class="subhead">Incoming changes compared with base</div>{_side_view(f, f.incoming, "incoming")}</div>
  <div class="pane pane-merge">{_merge_view(n, f, d, labels)}</div>
</div></div>
</section>"""


# ---------------------------------------------------------------- page canvases


def _overlaps(res: Page, cur: Page | None, inc: Page | None) -> list[str]:
    def boxes(p: Page | None) -> dict[str, tuple[float, float, float, float]]:
        if p is None:
            return {}
        return {
            n: (v.x, v.y, v.width, v.height)
            for n, v in p.visuals.items() if not v.is_group and not v.hidden and not v.parent
        }

    def ratio(a, b) -> float:
        ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
        iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
        return ix * iy / max(1.0, min(a[2] * a[3], b[2] * b[3]))

    r, c, i = boxes(res), boxes(cur), boxes(inc)
    out = []
    names = sorted(r)
    for x in range(len(names)):
        for y in range(x + 1, len(names)):
            a, b = names[x], names[y]
            if ratio(r[a], r[b]) < 0.2:
                continue
            if (a in c and b in c and ratio(c[a], c[b]) >= 0.2) or (a in i and b in i and ratio(i[a], i[b]) >= 0.2):
                continue  # already overlapping before the merge
            out.append(f"“{res.visuals[a].label}” and “{res.visuals[b].label}” overlap in the merged page.")
    return out


def page_section(
    report: str, page: str, reports: dict[str, dict[str, Report | None]], items: list[str], unresolved: set[str],
    labels: tuple[str, str],
) -> str:
    def get(side: str) -> Page | None:
        rep = reports[side].get(report)
        return rep.pages.get(page) if rep else None

    base, cur, inc, res = get("base"), get("current"), get("incoming"), get("result")
    pid = _id("m", report, page)
    display = (res or cur or inc or base).display_name  # type: ignore[union-attr]
    marks = {n: "invalid" for n in unresolved}

    def pane(cls: str, sign: str, title: str, label: str, p: Page | None, override=None) -> str:
        d = diff_page(page, base, p)
        return (
            f"<div class='pane-c'><div class='pane-head {cls}'><span class='side'>{sign}</span><b>{title}</b>"
            f"<code>{escape(label)}</code></div>{canvas(p, d.visuals, 'after', pid, override)}</div>"
        )

    warn = "".join(f"<div class='flash warn'>{escape(w)}</div>" for w in (_overlaps(res, cur, inc) if res else []))
    canvases = (
        "<div class='panes three'>"
        + pane("cur", "●", "Current", labels[0], cur)
        + pane("res", "◆", "Result", "preview", res, marks)
        + pane("inc", "●", "Incoming", labels[1], inc)
        + "</div>"
    )
    every = diff_page(page, base, res).visuals + diff_page(page, base, cur).visuals + diff_page(page, base, inc).visuals
    return f"""<section class="file pfile" id="{pid}">
<header class="file-head"><span class="chevbtn"></span>{PAGE}<span class="fname">{escape(display)}</span>
<span class="fpath">{escape(report)}/definition/pages/{escape(page)}</span><span class="grow"></span></header>
<div class="file-body"><div class="file-inner">{warn}{canvases}<div class="vfiles">{''.join(items)}</div></div></div>
</section>{hover_css(pid, every)}"""


# ---------------------------------------------------------------- whole page


def render_merge(
    session: MergeSession, decisions: dict[str, Decision], labels: tuple[str, str], *,
    token: str, errors: list[str] = (), done: list[str] | None = None, can_apply: bool = True, note: str = "",
    theme: str | None = None,
) -> str:
    result = session.result_source(decisions)
    sides = {"base": session.base, "current": session.current, "incoming": session.incoming, "result": result}
    file_lists = {k: s.files() for k, s in sides.items()}
    folders = set().union(*(find_reports(fl) for fl in file_lists.values()))
    reports = {k: {r: load_report(s, file_lists[k], r) for r in folders} for k, s in sides.items()}

    page_items: dict[tuple[str, str], list[str]] = {}
    unresolved: dict[tuple[str, str], set[str]] = {}
    others: list[str] = []
    tree_pages: dict[tuple[str, str], list[str]] = {}
    tree_other: list[str] = []
    open_conflicts = 0

    for n, f in enumerate(session.files):
        d = decisions[f.path]
        state = file_state(f, d)
        if d.strategy == MERGE:
            open_conflicts += sum(1 for c in f.conflict_ids if c not in d.choices)
        elif d.strategy is None:
            open_conflicts += max(1, len(f.conflicts))
        m = PAGE_FILE.match(f.path)
        icon = CHECK if state == "ok" else ALERT
        if m:
            key = (m["report"], m["page"])
            pid = _id("m", *key)
            vname = m["visual"]
            if vname:
                v = None
                for side in ("result", "current", "incoming", "base"):
                    rep = reports[side].get(m["report"])
                    pg = rep.pages.get(m["page"]) if rep else None
                    if pg and vname in pg.visuals:
                        v = pg.visuals[vname]
                        break
                label = v.label if v else vname
                fid = _id(pid, vname)
                title = f"{escape(label)}"
                if state != "ok":
                    unresolved.setdefault(key, set()).add(vname)
            else:
                label, fid, title = "Page settings", _id(pid, "page.json"), "Page settings"
            page_items.setdefault(key, []).append(merge_block(n, f, d, fid, title, labels, vname or ""))
            tree_pages.setdefault(key, []).append(
                f"<a class='row l2' href='#{fid}'>{FILE}<span class='nm'>{escape(label)}</span>{icon}</a>"
            )
        else:
            fid = f"of{n}"
            name = f.path.rsplit("/", 1)[-1]
            others.append(merge_block(n, f, d, fid, f"{FILE} {escape(name)}", labels))
            tree_other.append(f"<a class='row l1' href='#{fid}' title='{escape(f.path)}'>{FILE}<span class='nm'>{escape(name)}</span>{icon}</a>")

    blocks = [
        page_section(r, p, reports, items, unresolved.get((r, p), set()), labels)
        for (r, p), items in page_items.items()
    ]
    if others:
        blocks.append(f"<h2 class='group-title'>{FOLDER}Other files</h2>" + "".join(others))

    tree = []
    for (r, p), rows in tree_pages.items():
        rep = reports["result"].get(r) or reports["current"].get(r)
        pg = rep.pages.get(p) if rep else None
        name = pg.display_name if pg else p
        tree.append(
            f"<details open><summary class='row l1'>{CHEVRON}<a href='#{_id('m', r, p)}' class='row-link'>{PAGE}"
            f"<span class='nm'>{escape(name)}</span></a></summary>{''.join(rows)}</details>"
        )
    if tree_other:
        tree.append(f"<details open><summary class='row l1'>{CHEVRON}{FOLDER}<span class='nm'>Other files</span></summary>{''.join(tree_other)}</details>")

    flashes = []
    if note:
        flashes.append(f"<div class='flash'>{note}</div>")
    if session.incoming_only:
        k = len(session.incoming_only)
        flashes.append(f"<div class='flash'>{k} file{'s' * (k != 1)} changed only in incoming {'are' if k != 1 else 'is'} taken as-is.</div>")
    if errors:
        flashes.append("<div class='flash err'><b>Not applied yet:</b><ul>" + "".join(f"<li>{escape(e)}</li>" for e in errors) + "</ul></div>")
    if done is not None:
        flashes.append(
            f"<div class='flash ok'>{CHECK}<div><b>Resolution applied to {len(done)} file{'s' * (len(done) != 1)}.</b> "
            + ("Review with <code>git status</code> / <code>git diff --cached</code>, then <code>git commit</code> to finish the merge."
               if session.mode == "git" else "The merged project was written.")
            + " You can close this tab.</div></div>"
        )
    if not session.files:
        flashes.append("<div class='blankslate big'>Nothing to resolve: no file was changed on both sides.</div>")

    title = "Merge — " + (", ".join(sorted(f.removesuffix(".Report") for f in folders)) or "PBIP")
    total = len(session.files)
    resolved = sum(1 for f in session.files if file_state(f, decisions[f.path]) == "ok")
    disabled = "" if can_apply and done is None else " disabled"
    buttons = (
        f"<button class='btn' type='submit' form='mf' formaction='/preview'>Preview result</button>"
        f"<button class='btn primary' type='submit' form='mf' formaction='/apply'{disabled}>Apply resolution</button>"
    )
    progress = f"<span class='cnt s-{'ok' if resolved == total else 'conflict'}'>{resolved} / {total} files resolved</span>"
    return f"""<!doctype html>
<html lang="en"{_theme_attr(theme)}><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>{CSS}{MERGE_CSS}</style></head>
<body>
<header class="appbar">
  <div class="brand">{LOGO}<b>{escape(title)}</b></div>
  <div class="refs"><span class="ref" title="current">{BRANCH}<span>{escape(labels[0])}</span></span>
  <span class="muted">←</span><span class="ref inc" title="incoming">{BRANCH}<span>{escape(labels[1])}</span></span></div>
  <span class="grow"></span>{progress}{buttons}
</header>
<div class="layout">
  <aside class="sidebar">
    <div class="side-title"><span>Merge conflicts</span><span class="counter">{total}</span></div>
    <nav class="tree"><details open><summary class='row l0'>{CHEVRON}{REPORT}<span class='nm'>Files</span></summary>{''.join(tree)}</details></nav>
    <div class="legend"><span>{ALERT}Needs a decision</span><span>{CHECK}Resolved</span>
    <span><i class='swatch cur'></i>Current = yours (HEAD)</span><span><i class='swatch inc'></i>Incoming = theirs</span></div>
  </aside>
  <main><form id="mf" method="post" action="/preview"><input type="hidden" name="token" value="{escape(token)}">
  {''.join(flashes)}{''.join(blocks)}
  </form></main>
</div>
<footer class="statusbar"><span>{MERGE_IC}{escape(labels[1])} → {escape(labels[0])}</span><span class="grow"></span>
<span>{open_conflicts} open conflict{'s' * (open_conflicts != 1)}</span></footer>
</body></html>"""


MERGE_CSS = """
.s-ok{color:var(--add)}.s-conflict{color:var(--bad)}
.cnt.s-ok{color:var(--add)}.cnt.s-conflict{color:var(--bad)}
.ref.inc{color:var(--bad);background:color-mix(in srgb,var(--bad) 12%,transparent)}
.btn{height:32px;padding:0 14px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--fg);
  font:500 13px inherit;font-family:inherit;cursor:pointer}
.btn:hover{background:var(--bg-subtle)}
.btn.primary{background:#1f883d;border-color:#1f883d;color:#fff}
.btn.primary:hover{background:#1a7f37}
.btn:disabled{opacity:.5;cursor:not-allowed}
.swatch{display:inline-block;width:12px;height:12px;border-radius:3px}
.swatch.cur{background:var(--move)}.swatch.inc{background:var(--bad)}
.panes.three{grid-template-columns:repeat(3,minmax(0,1fr))}
@media (max-width:1300px){.panes.three{grid-template-columns:1fr}}
.pane-c{min-width:0;border:1px solid var(--border);border-radius:6px;overflow:hidden;background:var(--bg-inset)}
.pane-head.cur{background:var(--move-bg)}.pane-head.cur .side{color:var(--move)}
.pane-head.inc{background:var(--bad-bg)}.pane-head.inc .side{color:var(--bad)}
.pane-head.res{background:var(--add-line)}.pane-head.res .side{color:var(--add)}
.mfile .file-head{position:static}
.mfile.st-conflict{border-color:color-mix(in srgb,var(--bad) 55%,var(--border))}
.mfile .pane,.mfile .pane-hint{display:none}
.mfile .file-inner{padding:12px 16px}
.mfile:not(:has(.st:checked)) .pane-hint{display:block}
.st-current:checked~.file-body .pane-current,.st-incoming:checked~.file-body .pane-incoming,
.st-merge:checked~.file-body .pane-merge{display:block}
.seg.text label{width:auto;padding:0 10px;font-size:12px;gap:6px;display:inline-flex;align-items:center;color:var(--muted)}
.st-current:checked~.file-head .l-current{background:var(--move-bg);color:var(--move);font-weight:600}
.st-incoming:checked~.file-head .l-incoming{background:var(--bad-bg);color:var(--bad);font-weight:600}
.st-merge:checked~.file-head .l-merge{background:var(--add-bg);color:var(--add);font-weight:600}
.st-merge:checked~.file-head .l-merge .ic{color:var(--add)}
.conflict-label{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 50%,transparent)}
.ok-label{color:var(--add);border-color:color-mix(in srgb,var(--add) 50%,transparent)}
.subhead{margin:4px 0 8px}
.conflict{border:1px solid var(--border);border-radius:6px;margin-bottom:10px;overflow:hidden}
.c-path{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:6px 10px;background:var(--bg-subtle);border-bottom:1px solid var(--border);font-size:12px}
.c-path code{font-weight:600}
.opts{display:grid;grid-template-columns:1fr 1fr}
.opts.three{grid-template-columns:repeat(3,minmax(0,1fr))}
@media (max-width:900px){.opts,.opts.three{grid-template-columns:1fr}}
.opt{display:grid;grid-template-columns:auto 1fr;gap:4px 8px;align-items:start;padding:10px;cursor:pointer;min-width:0}
.opt+.opt{border-left:1px solid var(--border)}
.opt input{margin:3px 0 0;accent-color:var(--accent)}
.opt-title{font-size:12px;font-weight:600}
.opt-body{grid-column:2;min-width:0;overflow-wrap:anywhere}
.opt pre{margin:0;font:12px/18px var(--mono);white-space:pre-wrap}
.opt-current:has(input:checked){background:var(--move-bg);box-shadow:inset 3px 0 0 var(--move)}
.opt-incoming:has(input:checked){background:var(--bad-bg);box-shadow:inset 3px 0 0 var(--bad)}
.opt-both:has(input:checked){background:var(--add-line);box-shadow:inset 3px 0 0 var(--add)}
.flash.ok{display:flex;gap:8px;align-items:flex-start;border-color:color-mix(in srgb,var(--add) 40%,transparent);background:var(--add-line)}
.flash.err{border-color:color-mix(in srgb,var(--del) 40%,transparent);background:var(--del-line)}
.flash ul{margin:6px 0 0;padding-left:20px}
.pfile>.file-body>.file-inner{padding:16px}
"""
