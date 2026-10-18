"""pbip-merge: resolve merge conflicts in a PBIP report, in the browser.

Examples
    git merge feature               # stops with conflicts...
    pbip-merge                      # ...resolve them visually, then `git commit`
    pbip-merge feature              # preview merging `feature` into HEAD (writes nothing)
    pbip-merge --folders base/ current/ incoming/ -o merged/
    pbip-merge --accept merge       # no browser: auto-merge, fail if a conflict remains
"""

from __future__ import annotations

import argparse
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .cli import find_project
from .merge import (
    CURRENT, INCOMING, MERGE, Decision, MergeSession, build_session, merge_in_progress, ref_label,
    unmerged_paths,
)
from .mergeui import render_merge
from .source import FolderSource, GitError, GitSource, merge_base, repo_root


def decisions_from_form(session: MergeSession, form: dict[str, str]) -> dict[str, Decision]:
    out = {}
    for n, f in enumerate(session.files):
        strategy = form.get(f"s{n}")
        if strategy not in (CURRENT, INCOMING, MERGE):
            strategy = None
        choices = {}
        for k, cid in enumerate(f.conflict_ids):
            v = form.get(f"c{n}_{k}")
            if v:
                choices[cid] = v
        out[f.path] = Decision(strategy, choices)
    return out


def apply(session: MergeSession, decisions: dict[str, Decision], output: Path | None) -> list[str]:
    if session.mode == "git":
        return session.apply_git(decisions)
    assert output is not None
    return session.apply_folders(decisions, output)


def serve(session: MergeSession, labels, *, can_apply: bool, output: Path | None, note: str, port: int, open_browser: bool) -> None:
    token = secrets.token_urlsafe(16)
    state = {"decisions": session.default_decisions(), "theme": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the terminal quiet
            pass

        def _send(self, html: str, status: int = 200) -> None:
            body = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _page(self, **kw) -> str:
            return render_merge(
                session, state["decisions"], labels, token=token, can_apply=can_apply, note=note,
                theme=state["theme"], **kw,
            )

        def do_GET(self):
            url = urlparse(self.path)
            if url.path != "/":
                return self._send("Not found", 404)
            theme = parse_qs(url.query).get("theme", [None])[-1]
            if theme in ("light", "dark"):  # e.g. from the VS Code extension
                state["theme"] = theme
            self._send(self._page())

        def do_POST(self):
            size = int(self.headers.get("Content-Length") or 0)
            form = {k: v[-1] for k, v in parse_qs(self.rfile.read(size).decode("utf-8")).items()}
            if form.get("token") != token:
                return self._send("Invalid token", 403)
            state["decisions"] = decisions_from_form(session, form)
            if self.path == "/apply" and can_apply:
                problems = session.problems(state["decisions"])
                if problems:
                    return self._send(self._page(errors=problems))
                try:
                    written = apply(session, state["decisions"], output)
                except Exception as exc:  # show it in the page rather than crash the server
                    return self._send(self._page(errors=[str(exc)]))
                print(f"Applied resolution to {len(written)} file(s).", flush=True)
                self._send(self._page(done=written))
                threading.Timer(0.5, server.shutdown).start()
                return
            self._send(self._page())

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Merge resolver running at {url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped without applying.")
    finally:
        server.server_close()


def build(args) -> tuple[MergeSession, tuple[str, str], bool, str]:
    if args.folders:
        b, c, i = (Path(p) for p in args.folders)
        srcs = [FolderSource(find_project(p), p.resolve().name) for p in (b, c, i)]
        session = build_session("folders", *srcs)
        note = ""
        if not args.output:
            note = "Preview only: pass <code>-o OUTPUT</code> to write the merged project."
        return session, (srcs[1].label, srcs[2].label), bool(args.output), note

    project = find_project(Path(args.project or "."))
    try:
        repo = repo_root(project)
    except GitError:
        raise SystemExit(f"{project} is not inside a git repository (use --folders).")
    subdir = project.relative_to(repo).as_posix()
    subdir = "" if subdir == "." else subdir

    if args.incoming:
        try:
            mb = merge_base(repo, "HEAD", args.incoming)
            srcs = [GitSource(repo, r, subdir) for r in (mb, "HEAD", args.incoming)]
        except GitError as exc:
            raise SystemExit(str(exc))
        session = build_session("preview", *srcs)
        labels = (ref_label(repo, "HEAD"), ref_label(repo, args.incoming))
        note = (f"Preview of merging <code>{args.incoming}</code> into HEAD — nothing is written. "
                f"Run <code>git merge {args.incoming}</code>, then <code>pbip-merge</code> to resolve.")
        return session, labels, False, note

    if not merge_in_progress(repo):
        raise SystemExit(
            "No merge in progress. Start one with `git merge <branch>`, "
            "or preview with `pbip-merge <branch>`."
        )
    prefix = f"{subdir}/" if subdir else ""
    only = {p[len(prefix):] for p in unmerged_paths(repo) if p.startswith(prefix)}
    if not only:
        raise SystemExit("No conflicted files in this PBIP project. Finish with `git commit`.")
    mb = merge_base(repo, "HEAD", "MERGE_HEAD")
    srcs = [GitSource(repo, r, subdir) for r in (mb, "HEAD", "MERGE_HEAD")]
    session = build_session(
        "git", *srcs, only=only, worktree=FolderSource(project, "working tree"), project=project, repo=repo,
    )
    return session, (ref_label(repo, "HEAD"), ref_label(repo, "MERGE_HEAD")), True, ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pbip-merge",
        description=__doc__.split("\n\n")[0],
        epilog=__doc__.split("\n\n", 1)[1],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("incoming", nargs="?", help="Branch to preview merging into HEAD (read-only).")
    parser.add_argument("-p", "--project", help="PBIP project folder (default: search from cwd).")
    parser.add_argument("--folders", nargs=3, metavar=("BASE", "CURRENT", "INCOMING"), help="Merge three folders instead of git.")
    parser.add_argument("-o", "--output", help="With --folders: where to write the merged project.")
    parser.add_argument("--accept", choices=[CURRENT, INCOMING, MERGE], help="Resolve every file this way without the browser.")
    parser.add_argument("--port", type=int, default=0, help="Port for the local page (default: random).")
    parser.add_argument("--no-open", action="store_true", help="Don't open the browser.")
    args = parser.parse_args(argv)

    session, labels, can_apply, note = build(args)
    output = Path(args.output) if args.output else None
    print(f"{len(session.files)} file(s) to resolve, "
          f"{sum(len(f.conflicts) for f in session.files)} property/line conflict(s).")

    if args.accept:
        if not can_apply:
            raise SystemExit("Nothing to write in preview mode.")
        decisions = {}
        for f in session.files:
            strategy = args.accept if (args.accept != MERGE or f.can_merge) else None
            decisions[f.path] = Decision(strategy)
        problems = session.problems(decisions)
        if problems:
            print("Can't resolve automatically:", *problems, sep="\n  ", file=sys.stderr)
            return 1
        written = apply(session, decisions, output)
        print(f"Resolved {len(written)} file(s).")
        return 0

    serve(session, labels, can_apply=can_apply, output=output, note=note, port=args.port, open_browser=not args.no_open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
