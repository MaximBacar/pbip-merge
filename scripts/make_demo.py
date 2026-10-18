"""Build a demo git repo with two commits of a PBIP report, to try pbip-diff.

    uv run python scripts/make_demo.py /tmp/pbip-demo
    cd /tmp/pbip-demo && uv run --project <this repo> pbip-diff HEAD~1 HEAD
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.4.0/schema.json"


def lit(value: str) -> dict:
    return {"expr": {"Literal": {"Value": value}}}


def col(entity: str, prop: str) -> dict:
    return {
        "field": {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}},
        "queryRef": f"{entity}.{prop}",
        "nativeQueryRef": prop,
    }


def measure(entity: str, prop: str) -> dict:
    return {
        "field": {"Measure": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}},
        "queryRef": f"{entity}.{prop}",
        "nativeQueryRef": prop,
    }


def visual(name, vtype, x, y, w, h, z, roles=None, title=None, objects=None, **extra) -> dict:
    v: dict = {"visualType": vtype}
    if roles:
        v["query"] = {"queryState": {r: {"projections": p} for r, p in roles.items()}}
    if objects:
        v["objects"] = objects
    if title:
        v["visualContainerObjects"] = {
            "title": [{"properties": {"show": lit("true"), "text": lit(f"'{title}'")}}]
        }
    return {
        "$schema": SCHEMA,
        "name": name,
        "position": {"x": x, "y": y, "z": z, "height": h, "width": w, "tabOrder": z},
        "visual": v,
        **extra,
    }


def textbox(name, text, x, y, w, h, z) -> dict:
    return visual(
        name, "textbox", x, y, w, h, z,
        objects={"general": [{"properties": {"paragraphs": [{"textRuns": [{"value": text}]}]}}]},
    )


def base_visuals() -> dict[str, dict]:
    return {
        "a1title": textbox("a1title", "Posts overview", 20, 10, 600, 50, 0),
        "b2cards": visual("b2cards", "cardVisual", 20, 70, 300, 120, 1000,
                          roles={"Data": [measure("Posts", "Total Posts")]}),
        "c3likes": visual("c3likes", "card", 340, 70, 300, 120, 2000,
                          roles={"Values": [measure("Posts", "Avg Likes")]}),
        "d4bycat": visual("d4bycat", "clusteredColumnChart", 20, 210, 620, 490, 3000,
                          roles={"Category": [col("Posts", "Category")],
                                 "Y": [measure("Posts", "Total Posts")]},
                          title="Posts by category",
                          objects={"legend": [{"properties": {"show": lit("false")}}]}),
        "e5trend": visual("e5trend", "lineChart", 660, 70, 600, 300, 4000,
                          roles={"Category": [col("Date", "Month")],
                                 "Y": [measure("Posts", "Total Posts")]},
                          title="Monthly trend"),
        "f6table": visual("f6table", "tableEx", 660, 390, 600, 310, 5000,
                          roles={"Values": [col("Posts", "Title"), col("Posts", "Author"),
                                            measure("Posts", "Avg Likes")]},
                          title="Top posts"),
        "g7slicer": visual("g7slicer", "slicer", 1100, 10, 160, 50, 6000,
                           roles={"Values": [col("Date", "Year")]}),
    }


def write_page(report: Path, page: str, display: str, visuals: dict[str, dict]) -> None:
    pdir = report / "definition/pages" / page
    vdir = pdir / "visuals"
    if vdir.exists():
        shutil.rmtree(vdir)
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "page.json").write_text(json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.1.0/schema.json",
        "name": page, "displayName": display, "displayOption": "FitToPage",
        "height": 720, "width": 1280,
    }, indent=2))
    for name, v in visuals.items():
        (vdir / name).mkdir(parents=True)
        (vdir / name / "visual.json").write_text(json.dumps(v, indent=2))


def write_skeleton(repo: Path, report: Path) -> None:
    """Minimal PBIP project: .pbip file, report definition, empty semantic model."""
    files = {
        "MasterPosts.pbip": {"version": "1.0", "artifacts": [{"report": {"path": "MasterPosts.Report"}}]},
        "MasterPosts.Report/definition.pbir": {
            "version": "4.0", "datasetReference": {"byPath": {"path": "../MasterPosts.SemanticModel"}}},
        "MasterPosts.Report/definition/version.json": {"version": "2.0.0"},
        "MasterPosts.Report/definition/report.json": {"settings": {"useStylableVisualContainerHeader": True}},
        "MasterPosts.Report/definition/pages/pages.json": {
            "pageOrder": ["dd34709e01702e11a610"], "activePageName": "dd34709e01702e11a610"},
        "MasterPosts.SemanticModel/definition.pbism": {"version": "4.2"},
    }
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content, indent=2))
    (repo / "MasterPosts.SemanticModel/definition").mkdir(parents=True, exist_ok=True)
    (repo / "MasterPosts.SemanticModel/definition/model.tmdl").write_text("model Model\n\tculture: en-US\n")


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def main(target: str) -> None:
    repo = Path(target).resolve()
    if repo.exists():
        shutil.rmtree(repo)
    report = repo / "MasterPosts.Report"
    write_skeleton(repo, report)
    page = "dd34709e01702e11a610"

    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "demo@example.com")
    git(repo, "config", "user.name", "demo")
    write_page(report, page, "Overview", base_visuals())
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "Initial report")

    # ---- second commit: the kind of edits a PR would make
    v = base_visuals()
    v["d4bycat"]["position"].update(width=420)                         # resized
    v["d4bycat"]["visual"]["query"]["queryState"]["Y"]["projections"] = [
        measure("Posts", "Avg Likes")]                                 # field swapped
    v["d4bycat"]["visual"]["objects"]["legend"][0]["properties"]["show"] = lit("true")
    v["d4bycat"]["visual"]["visualContainerObjects"]["title"][0]["properties"]["text"] = lit("'Avg likes by category'")
    v["e5trend"]["visual"]["visualType"] = "areaChart"                 # type change
    v["g7slicer"]["position"].update(x=980, y=12)                      # moved
    del v["c3likes"]                                                   # removed
    v["h8donut"] = visual("h8donut", "donutChart", 450, 210, 190, 490, 7000,
                          roles={"Category": [col("Posts", "Platform")],
                                 "Y": [measure("Posts", "Total Posts")]},
                          title="By platform")                         # added
    v["f6table"]["filterConfig"] = {"filters": [{
        "name": "flt1", "type": "TopN",
        "field": col("Posts", "Title")["field"],
    }]}
    write_page(report, page, "Overview", v)

    # a brand new page
    write_page(report, "zz99newpage", "Authors", {
        "t1": textbox("t1", "Authors", 20, 10, 400, 50, 0),
        "t2": visual("t2", "clusteredBarChart", 20, 80, 1240, 620, 1000,
                     roles={"Category": [col("Posts", "Author")],
                            "Y": [measure("Posts", "Total Posts")]},
                     title="Posts per author"),
    })
    pages_json = report / "definition/pages/pages.json"
    meta = json.loads(pages_json.read_text())
    meta["pageOrder"].append("zz99newpage")
    pages_json.write_text(json.dumps(meta, indent=2))

    # an unresolved merge conflict, to show how broken files are reported
    vf = report / f"definition/pages/zz99newpage/visuals/t1/visual.json"
    text = vf.read_text()
    vf.write_text("<<<<<<< HEAD\n" + text + "\n=======\n" + text + "\n>>>>>>> feature\n")

    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "Rework overview, add Authors page")
    print(f"Demo repo ready: {repo}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "pbip-demo")
