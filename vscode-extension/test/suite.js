// Smoke test inside VS Code. Workspace: a git repo whose PBIP report has
// uncommitted changes, plus env PBIP_MERGE_DEMO = a repo stuck in a conflicted merge.
const assert = require("assert");
const path = require("path");
const http = require("http");
const vscode = require("vscode");

const wait = async (fn, ms = 30000) => {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    const v = await fn();
    if (v) return v;
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error("timed out");
};
const get = (url) => new Promise((res, rej) => http.get(url, (r) => { let b = ""; r.on("data", (d) => (b += d)); r.on("end", () => res(b)); }).on("error", rej));

exports.run = async () => {
  const ext = vscode.extensions.getExtension("local.pbip-visual-diff");
  await ext.activate();
  const { DiffPanel, MergePanel } = ext.exports._test;
  const root = vscode.workspace.workspaceFolders[0].uri.fsPath;

  // 1. Report diff command
  await vscode.commands.executeCommand("pbip.showReportDiff");
  const panel = await wait(() => [...DiffPanel.panels.values()][0]);
  const ready = (p) => /acquireVsCodeApi|failed|not installed/.test(p.panel.webview.html) && p.panel.webview.html;
  const html = await wait(() => ready(panel), 60000);
  assert.ok(!/pbip-diff failed|not installed/.test(html), html.slice(0, 2000));
  assert.ok(html.includes("data-path="), "page has data-path attributes");
  assert.ok(html.includes("acquireVsCodeApi"), "helper script injected");
  console.log("✓ report diff rendered", html.length, "chars");
  panel.panel.dispose();

  // 2. A JSON diff tab of a visual is replaced by the visual diff
  const file = vscode.Uri.file(path.join(root, "Sales Retail.Report/definition/pages/PageOverview/visuals/VisualContainer7/visual.json"));
  const left = file.with({ scheme: "git", query: JSON.stringify({ path: file.fsPath, ref: "HEAD" }) });
  await vscode.commands.executeCommand("vscode.diff", left, file, "visual.json (Working Tree)");
  const replaced = await wait(() => [...DiffPanel.panels.values()][0]);
  await wait(() => !vscode.window.tabGroups.all.some((g) => g.tabs.some((t) => t.input instanceof vscode.TabInputTextDiff)));
  console.log("✓ text diff tab replaced by visual diff");
  console.log("… waiting for replaced panel", replaced.panel.webview.html.slice(0, 200));
  const html2 = await wait(() => ready(replaced), 60000);
  console.log("… replaced panel ready");
  assert.ok(html2.includes('"Sales Retail.Report/definition/pages/PageOverview/visuals/VisualContainer7/visual.json"'), "focus target passed to page");
  replaced.panel.dispose();

  // 3. Merge resolver
  const demo = process.env.PBIP_MERGE_DEMO;
  if (demo) {
    await vscode.commands.executeCommand("pbip.resolveConflicts", vscode.Uri.file(path.join(demo, "Sales Retail.pbip")));
    console.log("… starting merge resolver");
    const mp = await wait(() => MergePanel.current && MergePanel.current.port && MergePanel.current, 60000)
      .catch((e) => { console.log("merge panel html:", MergePanel.current && MergePanel.current.panel.webview.html.slice(0, 1500)); throw e; });
    assert.ok(mp.panel.webview.html.includes("<iframe"), "iframe page");
    const page = await get(`http://127.0.0.1:${mp.port}/?theme=dark`);
    assert.ok(page.includes('data-theme="dark"') && page.includes("Merge —"), "merge page served with theme");
    console.log("✓ merge resolver served on port", mp.port);
    const pid = mp.proc.pid;
    mp.dispose();
    await wait(async () => { try { await get(`http://127.0.0.1:${mp.port}/`); return false; } catch { return true; } }, 10000);
    console.log("✓ resolver process stopped when the panel closed (pid", pid + ")");
  }
};
