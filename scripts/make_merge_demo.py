"""Create a git repo with a PBIP report stuck in a conflicted merge, to try pbip-merge.

    uv run python scripts/make_merge_demo.py ExampleReport /tmp/pbip-merge-demo
    cd /tmp/pbip-merge-demo && uv run --project <this repo> pbip-merge

`main` and `feature` both edit the "Sales Retail" example report:
  - same property changed differently        -> real conflict (pick a side)
  - different properties on adjacent lines   -> git conflict, auto-merged by pbip-merge
  - both add a field to the same table       -> auto-merged (both fields kept)
  - visual deleted on one side, edited on the other
  - page order changed on both sides         -> merged as a list
  - same DAX measure changed on both sides   -> line conflict in the .tmdl
  - a new visual lands on top of a moved one -> overlap warning
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

PAGES = "Sales Retail.Report/definition/pages"
MODEL = "Sales Retail.SemanticModel/definition/tables/FactSales.tmdl"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=False, capture_output=True, text=True).stdout


def load(repo: Path, rel: str) -> dict:
    return json.loads((repo / rel).read_text(encoding="utf-8-sig"))


def save(repo: Path, rel: str, data: dict) -> None:
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def visual(page: str, name: str) -> str:
    return f"{PAGES}/{page}/visuals/{name}/visual.json"


def lit(v: str) -> dict:
    return {"expr": {"Literal": {"Value": v}}}


def measure(entity: str, prop: str) -> dict:
    return {
        "field": {"Measure": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}},
        "queryRef": f"{entity}.{prop}",
        "nativeQueryRef": prop,
    }


def set_title(data: dict, text: str) -> None:
    data["visual"]["visualContainerObjects"]["title"][0]["properties"]["text"] = lit(f"'{text}'")


def card_colour(data: dict, colour: str) -> None:
    data["visual"]["objects"]["labels"][0]["properties"]["color"]["solid"]["color"] = lit(f"'{colour}'")


def edit_measure(repo: Path, old: str, new: str) -> None:
    path = repo / MODEL
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new), encoding="utf-8")


def main(source: str, target: str) -> None:
    src, repo = Path(source).resolve(), Path(target).resolve()
    if repo.exists():
        shutil.rmtree(repo)
    shutil.copytree(src, repo, ignore=shutil.ignore_patterns(".pbi", ".DS_Store", "cache.abf"))
    # Same JSON layout everywhere, like files saved by Power BI Desktop.
    for f in (repo / PAGES).rglob("*.json"):
        save(repo, f.relative_to(repo).as_posix(), load(repo, f.relative_to(repo).as_posix()))

    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "demo@example.com")
    git(repo, "config", "user.name", "demo")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "Base report")
    total_discount = re.search(r"measure 'Total Discount' = .*", (repo / MODEL).read_text()).group(0)

    # ---------------- feature branch
    git(repo, "checkout", "-qb", "feature")
    v = load(repo, visual("PageOverview", "VisualContainer6"))
    v["position"]["y"] = 230                                   # adjacent to main's x change
    save(repo, visual("PageOverview", "VisualContainer6"), v)
    v = load(repo, visual("PageStores", "VisualContainer2"))
    card_colour(v, "#00B050")                                  # main picks another colour
    save(repo, visual("PageStores", "VisualContainer2"), v)
    v = load(repo, visual("PageStores", "VisualContainer6"))
    v["position"].update(x=650, y=500, height=205)             # moved into main's new card
    save(repo, visual("PageStores", "VisualContainer6"), v)
    v = load(repo, visual("PageCustomers", "VisualContainer8"))
    v["visual"]["query"]["queryState"]["Values"]["projections"].append(measure("FactSales", "Total Discount"))
    save(repo, visual("PageCustomers", "VisualContainer8"), v)
    v = load(repo, visual("PageProducts", "VisualContainer6"))
    set_title(v, "Best sellers")                               # main deletes this visual
    save(repo, visual("PageProducts", "VisualContainer6"), v)
    # new page
    page = load(repo, f"{PAGES}/PageOverview/page.json")
    page.update(name="PageTrends", displayName="Trends")
    save(repo, f"{PAGES}/PageTrends/page.json", page)
    save(repo, visual("PageTrends", "VisualContainer6"), load(repo, visual("PageOverview", "VisualContainer6")))
    meta = load(repo, f"{PAGES}/pages.json")
    meta["pageOrder"].append("PageTrends")
    meta["activePageName"] = "PageTrends"
    save(repo, f"{PAGES}/pages.json", meta)
    edit_measure(repo, total_discount, "measure 'Total Discount' = ABS(SUM(FactSales[DiscountAmount]))")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "Feature: new Trends page, layout and field tweaks")

    # ---------------- main branch
    git(repo, "checkout", "-q", "main")
    v = load(repo, visual("PageOverview", "VisualContainer6"))
    v["position"]["x"] = 30
    set_title(v, "Revenue trend")
    save(repo, visual("PageOverview", "VisualContainer6"), v)
    v = load(repo, visual("PageStores", "VisualContainer2"))
    card_colour(v, "#C00000")
    save(repo, visual("PageStores", "VisualContainer2"), v)
    v = load(repo, visual("PageStores", "VisualContainer7"))
    v["position"].update(width=620)                            # make room for a new card
    save(repo, visual("PageStores", "VisualContainer7"), v)
    card = load(repo, visual("PageStores", "VisualContainer3"))
    card["name"] = "NewRegionCard"
    card["position"].update(x=650, y=500, width=610, height=205, z=9000)
    set_title(card, "Revenue (all regions)")
    save(repo, visual("PageStores", "NewRegionCard"), card)
    v = load(repo, visual("PageCustomers", "VisualContainer8"))
    v["visual"]["query"]["queryState"]["Values"]["projections"].append(measure("FactSales", "Gross Profit"))
    save(repo, visual("PageCustomers", "VisualContainer8"), v)
    shutil.rmtree(repo / PAGES / "PageProducts/visuals/VisualContainer6")
    meta = load(repo, f"{PAGES}/pages.json")
    order = meta["pageOrder"]
    order.remove("PageCustomers")
    order.insert(1, "PageCustomers")
    save(repo, f"{PAGES}/pages.json", meta)
    edit_measure(repo, total_discount, "measure 'Total Discount' = -SUM(FactSales[DiscountAmount])")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "Main: layout and formatting fixes")

    out = subprocess.run(["git", "-C", str(repo), "merge", "feature"], capture_output=True, text=True)
    print(out.stdout.strip())
    print(f"\nDemo repo with a conflicted merge: {repo}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "ExampleReport", sys.argv[2] if len(sys.argv) > 2 else "pbip-merge-demo")
