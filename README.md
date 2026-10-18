# pbip-diff

See what changed in a Power BI report between two git versions, without opening Power BI.

`pbip-diff` reads a PBIP project (PBIR format) at two revisions and writes one HTML page with:

The page is laid out like GitHub's "Files changed" view: a changes tree on the left,
one collapsible block per page and per visual, a **rich / source** toggle (property table or
side-by-side JSON diff), a **Viewed** checkbox per visual with a progress counter in the status bar,
light and dark themes.

- **Before / After canvases** for every page, drawn as wireframes at the right position and size.
  Colours mark visuals as added, removed, changed, or moved/resized. A dashed outline shows where a moved visual used to be.
- **A card per changed visual**: field changes (`− Total Posts  + Avg Likes`) and a property table (before → after).
- **Other files** (semantic model `.tmdl`, `report.json`, themes…) as a side-by-side line diff.
- Merge-conflict markers in a visual file are flagged; the visual is drawn from the "ours" side.

Hover a visual to highlight it on both canvases. Click it to jump to its details.

No data is shown: the report's data lives in the semantic model, so charts are placeholders.

## Usage

```sh
uv sync
uv run pbip-diff                      # last commit vs. uncommitted changes
uv run pbip-diff main                 # main vs. working tree
uv run pbip-diff main feature         # two branches / commits
uv run pbip-diff main...feature       # what `feature` changed since it branched (PR view)
uv run pbip-diff --folders old/ new/  # two folders, no git
```

Options: `-p/--project` (if several PBIP projects are in the repo), `-o out.html`,
`--no-open`, `--exit-code` (exit 1 when something changed, for CI).

The project is found automatically from the current folder.
Only the PBIR format is drawn (`definition/pages/.../visual.json`). For older projects, turn on
*Preview features → Store reports using enhanced metadata format (PBIR)* in Power BI Desktop and save.

## Try it without Power BI

```sh
uv run python scripts/make_demo.py /tmp/pbip-demo   # demo repo with 2 commits
cd /tmp/pbip-demo && uv run --project ~/Developer/pbip-merge pbip-diff HEAD~1 HEAD
```

## Code

| File | Role |
|---|---|
| `src/pbip_diff/source.py` | Read project files from a folder or a git revision (`git cat-file --batch`) |
| `src/pbip_diff/model.py`  | Parse pages and visuals (position, type, title, fields) |
| `src/pbip_diff/diff.py`   | JSON diff, classify changes (moved, fields, formatting, filters…) |
| `src/pbip_diff/textdiff.py` | Side-by-side line diff with word highlights |
| `src/pbip_diff/render.py` | Self-contained HTML output, no JavaScript |
| `src/pbip_diff/cli.py`    | Command line |

Tests: `uv run pytest`.
