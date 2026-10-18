"""Side-by-side line diff (GitHub "split" view) with word-level highlights."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from html import escape


@dataclass
class Row:
    kind: str  # "hunk" | "ctx" | "del" | "add" | "chg"
    a_no: int | None = None
    a_html: str = ""
    b_no: int | None = None
    b_html: str = ""


def line_stats(a: list[str], b: list[str]) -> tuple[int, int]:
    """(added, removed) line counts."""
    added = removed = 0
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ("delete", "replace"):
            removed += i2 - i1
        if tag in ("insert", "replace"):
            added += j2 - j1
    return added, removed


def _inline(a: str, b: str) -> tuple[str, str]:
    sm = SequenceMatcher(None, a, b, autojunk=False)
    if sm.ratio() < 0.35:  # lines too different: no word highlight
        return escape(a), escape(b)
    out_a, out_b = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        sa, sb = escape(a[i1:i2]), escape(b[j1:j2])
        if tag == "equal":
            out_a.append(sa)
            out_b.append(sb)
        else:
            if sa:
                out_a.append(f"<span class='x'>{sa}</span>")
            if sb:
                out_b.append(f"<span class='x'>{sb}</span>")
    return "".join(out_a), "".join(out_b)


def split_rows(a: list[str], b: list[str], context: int = 3, max_rows: int = 1500) -> tuple[list[Row], bool]:
    rows: list[Row] = []
    sm = SequenceMatcher(None, a, b, autojunk=False)
    for group in sm.get_grouped_opcodes(context):
        i1, j1 = group[0][1], group[0][3]
        i2, j2 = group[-1][2], group[-1][4]
        rows.append(Row("hunk", a_html=escape(f"@@ -{i1 + 1},{i2 - i1} +{j1 + 1},{j2 - j1} @@")))
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                for k in range(i2 - i1):
                    t = escape(a[i1 + k])
                    rows.append(Row("ctx", i1 + k + 1, t, j1 + k + 1, t))
            elif tag == "delete":
                rows += [Row("del", i + 1, escape(a[i])) for i in range(i1, i2)]
            elif tag == "insert":
                rows += [Row("add", b_no=j + 1, b_html=escape(b[j])) for j in range(j1, j2)]
            else:  # replace: pair lines up, pad the shorter side
                for k in range(max(i2 - i1, j2 - j1)):
                    ia, jb = i1 + k, j1 + k
                    has_a, has_b = ia < i2, jb < j2
                    if has_a and has_b:
                        ha, hb = _inline(a[ia], b[jb])
                        rows.append(Row("chg", ia + 1, ha, jb + 1, hb))
                    elif has_a:
                        rows.append(Row("del", ia + 1, escape(a[ia])))
                    else:
                        rows.append(Row("add", b_no=jb + 1, b_html=escape(b[jb])))
        if len(rows) > max_rows:
            return rows[:max_rows], True
    return rows, False


def split_table(a: list[str], b: list[str]) -> str:
    rows, truncated = split_rows(a, b)
    if not rows:
        return "<div class='blankslate'>No textual changes.</div>"
    out = []
    for r in rows:
        if r.kind == "hunk":
            out.append(f"<tr class='hunk'><td class='n'></td><td colspan='3'>{r.a_html}</td></tr>")
            continue
        left = "ctx" if r.kind == "ctx" else ("del" if r.a_no else "empty")
        right = "ctx" if r.kind == "ctx" else ("add" if r.b_no else "empty")
        out.append(
            f"<tr><td class='n {left}'>{r.a_no or ''}</td><td class='c {left}'>{r.a_html}</td>"
            f"<td class='n {right}'>{r.b_no or ''}</td><td class='c {right}'>{r.b_html}</td></tr>"
        )
    if truncated:
        out.append("<tr class='hunk'><td class='n'></td><td colspan='3'>… diff truncated</td></tr>")
    return (
        "<div class='splitwrap'><table class='split'><colgroup><col class='cn'><col><col class='cn'><col></colgroup>"
        f"{''.join(out)}</table></div>"
    )
