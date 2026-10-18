# PBIP Visual Diff (VS Code)

See Power BI report changes as before/after canvases inside VS Code, instead of JSON diffs,
and resolve merge conflicts visually. It's a thin wrapper around the `pbip-diff` and
`pbip-merge` commands from this repository.

## What it does

- **Click a changed report file in Source Control.** The JSON diff tab is replaced by the
  visual diff (HEAD ↔ working tree), scrolled to that visual. Turn this off with
  `pbip.replaceTextDiff`.
- **The tab refreshes itself** when report files change, e.g. when Power BI Desktop saves.
  "Viewed" ticks and the scroll position are kept across refreshes.
- **In the diff tab:** click a file path to open the file; Alt+click opens the regular text diff.
- **Merge conflicts:** right-click a conflicted file in *Merge Changes* → **Resolve Merge
  Conflicts Visually**, or accept the prompt shown when you open a conflicted report file.
  The resolver opens in a tab; after **Apply resolution**, commit to finish the merge.
- **Commands** (`PBIP:` in the Command Palette): *Show Report Changes*, *Compare Report with
  Branch or Commit…*, *Resolve Merge Conflicts Visually*, *Preview Merging a Branch…*.
- **Theme:** follows your VS Code colour theme and fonts.

## Setup

1. Install the Python tools so `pbip-diff` and `pbip-merge` are on your PATH:

   ```sh
   uv tool install ~/Developer/pbip-merge      # re-run with --reinstall after updating
   ```

   Or point the settings at a checkout instead:
   `"pbip.diffCommand": "uv run --project /path/to/pbip-merge pbip-diff"` (same for `pbip.mergeCommand`).
   When the pbip-merge repo itself is open in VS Code, the extension finds it automatically.

2. Install the extension:

   ```sh
   cd vscode-extension
   npx @vscode/vsce package --skip-license --allow-missing-repository
   code --install-extension pbip-visual-diff-0.1.0.vsix
   ```

   Or open this folder in VS Code and press F5 to run it in a development window.

## Settings

| Setting | Default | |
|---|---|---|
| `pbip.replaceTextDiff` | `true` | Show the visual diff instead of the JSON diff for report files |
| `pbip.diffCommand` | `pbip-diff` | Command line for pbip-diff |
| `pbip.mergeCommand` | `pbip-merge` | Command line for pbip-merge |
| `pbip.promptOnConflicts` | `true` | Offer the resolver when a conflicted report file is opened |

Logs: *Output* panel → **PBIP Visual Diff**.

## Development

```sh
npm install
npm run check                                   # type-check extension.js
node test/runTest.js <workspace-with-pbip-changes>   # smoke test in a downloaded VS Code
```
