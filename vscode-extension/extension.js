// @ts-check
// PBIP Visual Diff: shows pbip-diff / pbip-merge pages inside VS Code.
//
// - Visual diff: runs `pbip-diff` (HEAD vs working tree, or a chosen ref) and shows
//   the HTML in a webview. Refreshes when report files change (e.g. saved by Power BI).
// - Replaces the plain JSON diff tab for report files with the visual diff.
// - Merge conflicts: runs `pbip-merge` (a local server) and shows it in a webview.

const vscode = require("vscode");
const cp = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const PROJECT_PART = /\.(Report|SemanticModel|Dataset)$/;
const REPORT_JSON = /\.Report[\\/]definition[\\/].*\.json$/;
const PBIP_FILE = /\.(Report|SemanticModel|Dataset)[\\/]/;

/** @type {vscode.OutputChannel} */
let log;
/** Diff tabs we opened on purpose (the "Open Text Diff" button) — don't replace those. */
const allowTextDiff = new Set();

// ------------------------------------------------------------------ helpers

/** Project folder (holding the .pbip) for a file inside it. */
function projectOf(fsPath) {
  if (fsPath.endsWith(".pbip")) return path.dirname(fsPath);
  const parts = fsPath.split(/[\\/]/);
  const i = parts.findIndex((p) => PROJECT_PART.test(p));
  if (i <= 0) return undefined;
  return parts.slice(0, i).join(path.sep) || path.sep;
}

function relPosix(project, fsPath) {
  return path.relative(project, fsPath).split(path.sep).join("/");
}

/** File path of a URI, including git: URIs used by the SCM diff editor. */
function fsPathOf(uri) {
  if (uri.scheme === "file") return uri.fsPath;
  if (uri.scheme === "git") {
    try {
      return JSON.parse(uri.query).path;
    } catch {
      return uri.fsPath;
    }
  }
  return undefined;
}

function cfg(key) {
  return vscode.workspace.getConfiguration("pbip").get(key);
}

function quote(arg) {
  if (process.platform === "win32") return `"${arg.replace(/"/g, '""')}"`;
  return `'${arg.replace(/'/g, `'\\''`)}'`;
}

function onPath(name) {
  const which = process.platform === "win32" ? "where" : "which";
  return cp.spawnSync(which, [name], { env: shellEnv() }).status === 0;
}

/** PATH as a login shell sees it (VS Code started from the Dock misses ~/.local/bin etc.). */
function shellEnv() {
  const env = { ...process.env };
  const extra = [path.join(os.homedir(), ".local", "bin"), path.join(os.homedir(), ".cargo", "bin"), "/opt/homebrew/bin", "/usr/local/bin"];
  env.PATH = [env.PATH || "", ...extra].join(path.delimiter);
  env.PYTHONUNBUFFERED = "1";  // see pbip-merge's "running at" line right away
  return env;
}

/**
 * Command line for pbip-diff / pbip-merge. Uses the setting; when the default
 * name isn't installed, falls back to `uv run` in a workspace folder that
 * contains the pbip-merge project.
 */
function resolveCommand(kind) {
  const setting = String(cfg(kind === "diff" ? "diffCommand" : "mergeCommand") || "").trim();
  const name = kind === "diff" ? "pbip-diff" : "pbip-merge";
  if (setting && setting !== name) return setting;
  if (onPath(name)) return name;
  for (const folder of vscode.workspace.workspaceFolders || []) {
    const pyproject = path.join(folder.uri.fsPath, "pyproject.toml");
    if (fs.existsSync(pyproject) && fs.readFileSync(pyproject, "utf8").includes("pbip_diff")) {
      return `uv run --quiet --project ${quote(folder.uri.fsPath)} ${name}`;
    }
  }
  throw new Error(
    `Can't find \`${name}\`. Install it with \`uv tool install /path/to/pbip-merge\`, ` +
      `or set "pbip.${kind}Command" (e.g. "uv run --project /path/to/pbip-merge ${name}").`
  );
}

/** Run a command line + args; resolves with {code, stdout, stderr}. */
function run(commandLine, args, cwd) {
  const full = [commandLine, ...args.map(quote)].join(" ");
  log.appendLine(`$ ${full}`);
  return new Promise((resolve) => {
    cp.exec(full, { cwd, env: shellEnv(), maxBuffer: 64 * 1024 * 1024 }, (err, stdout, stderr) => {
      if (stderr) log.appendLine(stderr.trim());
      resolve({ code: err ? (typeof err.code === "number" ? err.code : 1) : 0, stdout, stderr });
    });
  });
}

/** PBIP projects in the workspace. */
async function findProjects() {
  const found = new Set();
  for (const uri of await vscode.workspace.findFiles("**/*.pbip", "**/{node_modules,.git,.venv}/**", 50)) {
    found.add(path.dirname(uri.fsPath));
  }
  for (const uri of await vscode.workspace.findFiles("**/*.Report/definition.pbir", "**/{node_modules,.git,.venv}/**", 50)) {
    const p = projectOf(uri.fsPath);
    if (p) found.add(p);
  }
  return [...found].sort();
}

async function pickProject(uri) {
  const fsPath = uri && fsPathOf(uri);
  const fromUri = fsPath && projectOf(fsPath);
  if (fromUri) return fromUri;
  const active = vscode.window.activeTextEditor && projectOf(vscode.window.activeTextEditor.document.uri.fsPath);
  if (active) return active;
  const projects = await findProjects();
  if (projects.length <= 1) {
    if (!projects.length) vscode.window.showWarningMessage("No PBIP project found in this workspace.");
    return projects[0];
  }
  const pick = await vscode.window.showQuickPick(
    projects.map((p) => ({ label: path.basename(p), description: vscode.workspace.asRelativePath(p), project: p })),
    { placeHolder: "Which Power BI project?" }
  );
  return pick && pick.project;
}

/** Resource URI from a command argument (Uri, SCM resource state, or nothing). */
function uriArg(arg) {
  if (!arg) return vscode.window.activeTextEditor && vscode.window.activeTextEditor.document.uri;
  if (arg instanceof vscode.Uri) return arg;
  if (arg.resourceUri) return arg.resourceUri;
  return undefined;
}

function nonce() {
  return [...Array(24)].map(() => Math.floor(Math.random() * 36).toString(36)).join("");
}

function errorPage(title, detail) {
  const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);
  return `<!doctype html><html><body style="font-family:var(--vscode-font-family);padding:24px;color:var(--vscode-foreground)">
<h3>${esc(title)}</h3><pre style="white-space:pre-wrap;font-family:var(--vscode-editor-font-family)">${esc(detail)}</pre></body></html>`;
}

// ------------------------------------------------------------------ visual diff panel

class DiffPanel {
  /** @type {Map<string, DiffPanel>} key: project + base */
  static panels = new Map();

  /**
   * @param {vscode.ExtensionContext} context
   * @param {string} project
   * @param {{ base?: string, focus?: string }} opts
   */
  static show(context, project, opts = {}) {
    const base = opts.base || "HEAD";
    const key = `${project}::${base}`;
    let p = DiffPanel.panels.get(key);
    if (p) {
      p.panel.reveal(undefined, false);
      if (opts.focus) p.focus(opts.focus);
      return p;
    }
    p = new DiffPanel(context, project, base, opts.focus);
    DiffPanel.panels.set(key, p);
    return p;
  }

  constructor(context, project, base, focus) {
    this.context = context;
    this.project = project;
    this.base = base;
    this.pendingFocus = focus;
    this.running = false;
    this.again = false;
    this.stale = false;
    const name = path.basename(project);
    this.panel = vscode.window.createWebviewPanel(
      "pbipDiff",
      `Δ ${name} (${base} ↔ working tree)`,
      { viewColumn: vscode.ViewColumn.Active, preserveFocus: false },
      { enableScripts: true, retainContextWhenHidden: true, enableFindWidget: true, localResourceRoots: [] }
    );
    this.panel.iconPath = vscode.Uri.joinPath(context.extensionUri, "media", "icon.png");
    this.panel.webview.html = errorPage("Loading report diff…", `${base} ↔ working tree\n${project}`);

    const disposables = [];
    this.panel.webview.onDidReceiveMessage((m) => this.onMessage(m), null, disposables);
    this.panel.onDidChangeViewState(() => {
      if (this.panel.visible && this.stale) this.refresh();
    }, null, disposables);

    // Refresh when report files change (Power BI Desktop saves, git checkout...).
    const watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(project, "**/*"));
    let timer;
    const changed = (uri) => {
      if (!PBIP_FILE.test(uri.fsPath) || /[\\/]\.pbi[\\/]/.test(uri.fsPath)) return;
      clearTimeout(timer);
      timer = setTimeout(() => (this.panel.visible ? this.refresh() : (this.stale = true)), 400);
    };
    watcher.onDidChange(changed, null, disposables);
    watcher.onDidCreate(changed, null, disposables);
    watcher.onDidDelete(changed, null, disposables);
    disposables.push(watcher);

    this.panel.onDidDispose(() => {
      DiffPanel.panels.delete(`${project}::${base}`);
      disposables.forEach((d) => d.dispose());
    });
    this.refresh();
  }

  focus(relPath) {
    this.panel.webview.postMessage({ type: "focus", path: relPath });
  }

  async refresh() {
    if (this.running) {
      this.again = true;
      return;
    }
    this.running = true;
    this.stale = false;
    try {
      const out = path.join(os.tmpdir(), `pbip-diff-${process.pid}-${Math.abs(hash(this.project + this.base))}.html`);
      let command;
      try {
        command = resolveCommand("diff");
      } catch (e) {
        this.panel.webview.html = errorPage("pbip-diff is not installed", e.message);
        return;
      }
      const res = await run(command, [this.base, "-p", this.project, "-o", out, "--no-open"], this.project);
      if (res.code !== 0 || !fs.existsSync(out)) {
        this.panel.webview.html = errorPage("pbip-diff failed", (res.stderr || res.stdout || "").trim());
        return;
      }
      const html = fs.readFileSync(out, "utf8");
      this.panel.webview.html = this.wrap(html, this.pendingFocus);
      this.pendingFocus = undefined;
    } finally {
      this.running = false;
      if (this.again) {
        this.again = false;
        this.refresh();
      }
    }
  }

  /** Add a CSP and a small script: focus a file, keep "Viewed" + scroll across refreshes, open files. */
  wrap(html, focus) {
    const n = nonce();
    const csp = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; script-src 'nonce-${n}';">`;
    const script = `<script nonce="${n}">
(() => {
  const vscode = acquireVsCodeApi();
  const state = vscode.getState() || { viewed: [], scroll: 0 };
  const save = () => vscode.setState(state);

  for (const box of document.querySelectorAll("input.vw")) {
    if (state.viewed.includes(box.id)) box.checked = true;
    box.addEventListener("change", () => {
      state.viewed = state.viewed.filter((id) => id !== box.id);
      if (box.checked) state.viewed.push(box.id);
      save();
    });
  }

  function highlight(el) {
    el.classList.add("flash-focus");
    setTimeout(() => el.classList.remove("flash-focus"), 1600);
  }
  function focus(p) {
    let el = document.querySelector('[data-path="' + CSS.escape(p) + '"]');
    if (!el) {  // page.json and other files of a page: fall back to the page block
      el = [...document.querySelectorAll("[data-path]")].filter((e) => p.startsWith(e.dataset.path + "/")).pop();
    }
    if (!el) return false;
    for (let a = el; a; a = a.parentElement) {  // unfold collapsed parents
      const fold = a.querySelector(":scope > input.fold");
      if (fold) fold.checked = false;
    }
    el.scrollIntoView({ block: "start" });
    highlight(el);
    return true;
  }

  // In-page links (sidebar, canvas boxes): scroll + highlight (webviews don't apply :target).
  document.addEventListener("click", (e) => {
    const a = e.target.closest('a[href^="#"]');
    if (a) {
      const el = document.getElementById(decodeURIComponent(a.hash.slice(1)));
      if (el) { e.preventDefault(); el.scrollIntoView({ block: "start" }); highlight(el); }
      return;
    }
    const p = e.target.closest(".fpath");
    if (p) vscode.postMessage({ type: e.altKey ? "textDiff" : "open", path: p.title });
  }, true);

  window.addEventListener("message", (e) => { if (e.data.type === "focus") focus(e.data.path); });
  let t;
  window.addEventListener("scroll", () => { clearTimeout(t); t = setTimeout(() => { state.scroll = scrollY; save(); }, 150); });

  const initial = ${JSON.stringify(focus || "")};
  if (!(initial && focus(initial))) window.scrollTo(0, state.scroll || 0);
  for (const p of document.querySelectorAll(".fpath")) p.title += "\\nClick: open file · Alt+click: text diff";
})();
</script>`;
    return html.replace("<head>", `<head>${csp}`).replace("</body>", `${script}</body>`);
  }

  async onMessage(m) {
    if (!m || typeof m.path !== "string") return;
    const file = vscode.Uri.file(path.join(this.project, ...m.path.split("/")));
    if (m.type === "open") {
      if (fs.existsSync(file.fsPath)) {
        await vscode.window.showTextDocument(file, { preview: true, viewColumn: vscode.ViewColumn.Beside });
      }
    } else if (m.type === "textDiff") {
      await openTextDiff(file, this.base);
    }
  }
}

function hash(s) {
  let h = 0;
  for (const c of s) h = (h * 31 + c.charCodeAt(0)) | 0;
  return h;
}

/** The regular VS Code diff (ref ↔ working tree), without being replaced by the visual diff. */
async function openTextDiff(file, ref = "HEAD") {
  const left = file.with({ scheme: "git", path: file.path, query: JSON.stringify({ path: file.fsPath, ref }) });
  allowTextDiff.add(file.fsPath);
  setTimeout(() => allowTextDiff.delete(file.fsPath), 3000);
  await vscode.commands.executeCommand("vscode.diff", left, file, `${path.basename(file.fsPath)} (${ref} ↔ Working Tree)`);
}

// ------------------------------------------------------------------ merge panel

class MergePanel {
  /** @type {MergePanel | undefined} */
  static current;

  static async show(context, project, incoming) {
    if (MergePanel.current) MergePanel.current.dispose();
    let command;
    try {
      command = resolveCommand("merge");
    } catch (e) {
      vscode.window.showErrorMessage(e.message);
      return;
    }
    MergePanel.current = new MergePanel(context, project, command, incoming);
  }

  constructor(context, project, command, incoming) {
    this.project = project;
    this.applied = false;
    const title = incoming ? `Merge preview: ${incoming}` : `Resolve conflicts: ${path.basename(project)}`;
    this.panel = vscode.window.createWebviewPanel("pbipMerge", title, vscode.ViewColumn.Active, {
      enableScripts: true,
      retainContextWhenHidden: true,
    });
    this.panel.iconPath = vscode.Uri.joinPath(context.extensionUri, "media", "icon.png");
    this.panel.webview.html = errorPage("Starting merge resolver…", project);

    const args = [...(incoming ? [incoming] : []), "--no-open", "--port", "0", "-p", project];
    const full = [command, ...args.map(quote)].join(" ");
    log.appendLine(`$ ${full}`);
    // Own process group, so closing the panel stops the whole tree (shell → uv → python).
    this.proc = cp.spawn(full, { cwd: project, env: shellEnv(), shell: true, detached: process.platform !== "win32" });
    let output = "";
    const onData = (buf) => {
      const text = buf.toString();
      output += text;
      log.append(text);
      const m = /http:\/\/127\.0\.0\.1:(\d+)\//.exec(output);
      if (m && !this.port) this.load(Number(m[1]));
      if (text.includes("Applied resolution")) this.applied = true;
    };
    this.proc.stdout.on("data", onData);
    this.proc.stderr.on("data", onData);
    this.proc.on("exit", (code) => {
      if (this.applied) {
        vscode.window.showInformationMessage("Power BI merge conflicts resolved and staged. Review, then commit to finish the merge.");
        vscode.commands.executeCommand("git.refresh").then(undefined, () => {});
      } else if (!this.port) {
        this.panel.webview.html = errorPage("pbip-merge stopped", output.trim() || `exit code ${code}`);
      }
      this.proc = undefined;
    });
    this.panel.onDidDispose(() => this.dispose());
  }

  load(port) {
    this.port = port;
    const kind = vscode.window.activeColorTheme.kind;
    const dark = kind === vscode.ColorThemeKind.Dark || kind === vscode.ColorThemeKind.HighContrast;
    const src = `http://127.0.0.1:${port}/?theme=${dark ? "dark" : "light"}`;
    this.panel.webview.options = {
      enableScripts: true,
      portMapping: [{ webviewPort: port, extensionHostPort: port }],
    };
    this.panel.webview.html = `<!doctype html><html><head>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; frame-src http://127.0.0.1:${port}/ http://127.0.0.1:${port}; style-src 'unsafe-inline';">
<style>html,body,iframe{margin:0;padding:0;border:0;width:100%;height:100%;overflow:hidden;background:var(--vscode-editor-background)}</style>
</head><body><iframe src="${src}" title="PBIP merge"></iframe></body></html>`;
  }

  dispose() {
    if (this.proc && this.proc.pid) {
      try {
        if (process.platform === "win32") cp.spawn("taskkill", ["/pid", String(this.proc.pid), "/t", "/f"]);
        else process.kill(-this.proc.pid, "SIGTERM");
      } catch {
        /* already gone */
      }
    }
    this.proc = undefined;
    if (MergePanel.current === this) MergePanel.current = undefined;
    try {
      this.panel.dispose();
    } catch {
      /* already disposed */
    }
  }
}

// ------------------------------------------------------------------ activation

/** @param {vscode.ExtensionContext} context */
function activate(context) {
  log = vscode.window.createOutputChannel("PBIP Visual Diff");
  const reg = (id, fn) => context.subscriptions.push(vscode.commands.registerCommand(id, fn));

  reg("pbip.openVisualDiff", async (arg) => {
    const uri = uriArg(arg);
    const file = uri && fsPathOf(uri);
    const project = await pickProject(uri);
    if (!project) return;
    DiffPanel.show(context, project, { focus: file ? relPosix(project, file) : undefined });
  });

  reg("pbip.showReportDiff", async (arg) => {
    const project = await pickProject(uriArg(arg));
    if (project) DiffPanel.show(context, project);
  });

  reg("pbip.compareWithRef", async (arg) => {
    const project = await pickProject(uriArg(arg));
    if (!project) return;
    const ref = await vscode.window.showInputBox({
      prompt: "Compare the working tree with which branch, tag or commit?",
      placeHolder: "main, origin/main, HEAD~3, a1b2c3d…",
      value: "main",
    });
    if (ref) DiffPanel.show(context, project, { base: ref.trim() });
  });

  reg("pbip.resolveConflicts", async (arg) => {
    const project = await pickProject(uriArg(arg));
    if (project) MergePanel.show(context, project);
  });

  reg("pbip.previewMerge", async (arg) => {
    const project = await pickProject(uriArg(arg));
    if (!project) return;
    const ref = await vscode.window.showInputBox({ prompt: "Preview merging which branch into HEAD?", placeHolder: "feature/my-branch" });
    if (ref) MergePanel.show(context, project, ref.trim());
  });

  reg("pbip.openTextDiff", async (arg) => {
    const uri = uriArg(arg);
    if (uri) await openTextDiff(uri);
  });

  // Replace the JSON diff tab of report files with the visual diff.
  context.subscriptions.push(
    vscode.window.tabGroups.onDidChangeTabs((e) => {
      if (!cfg("replaceTextDiff")) return;
      for (const tab of e.opened) {
        if (!(tab.input instanceof vscode.TabInputTextDiff)) continue;
        const file = fsPathOf(tab.input.modified) || fsPathOf(tab.input.original);
        if (!file || !REPORT_JSON.test(file)) continue;
        if (allowTextDiff.has(file)) continue;
        const project = projectOf(file);
        if (!project) continue;
        const column = tab.group.viewColumn;
        vscode.window.tabGroups.close(tab).then(() => {
          const p = DiffPanel.show(context, project, { focus: relPosix(project, file) });
          if (p.panel.viewColumn !== column) p.panel.reveal(column);
        });
      }
    })
  );

  // Offer the resolver when a conflicted report file is opened.
  const prompted = new Set();
  const checkConflicts = (doc) => {
    if (!cfg("promptOnConflicts") || doc.uri.scheme !== "file" || !PBIP_FILE.test(doc.uri.fsPath)) return;
    const project = projectOf(doc.uri.fsPath);
    if (!project || prompted.has(project) || !/^<{7} /m.test(doc.getText())) return;
    prompted.add(project);
    vscode.window
      .showInformationMessage(`${path.basename(doc.uri.fsPath)} has merge conflicts.`, "Resolve visually", "Not now")
      .then((choice) => {
        if (choice === "Resolve visually") MergePanel.show(context, project);
      });
  };
  context.subscriptions.push(vscode.workspace.onDidOpenTextDocument(checkConflicts));
  vscode.workspace.textDocuments.forEach(checkConflicts);

  context.subscriptions.push({ dispose: () => MergePanel.current && MergePanel.current.dispose() });
  return { _test: { projectOf, relPosix, fsPathOf, quote, DiffPanel, MergePanel } };  // for test/suite.js
}

function deactivate() {
  if (MergePanel.current) MergePanel.current.dispose();
}

module.exports = { activate, deactivate };
