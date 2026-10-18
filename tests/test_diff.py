import importlib.util
import subprocess
from pathlib import Path

from pbip_diff.cli import main
from pbip_diff.diff import Change, diff_project, json_diff
from pbip_diff.model import literal, parse_json
from pbip_diff.render import render
from pbip_diff.source import FolderSource, GitSource

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("make_demo", ROOT / "scripts/make_demo.py")
make_demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_demo)


def demo(tmp_path):
    make_demo.main(str(tmp_path / "demo"))
    return tmp_path / "demo"


def statuses(diff):
    return {
        (p.current.display_name, v.name): (v.status, v.kinds)
        for r in diff.reports for p in r.pages for v in p.visuals
    }


def test_literal_unwrap():
    assert literal({"expr": {"Literal": {"Value": "'It''s'"}}}) == "It's"
    assert literal({"expr": {"Literal": {"Value": "12D"}}}) == "12"
    assert literal({"expr": {"Literal": {"Value": "true"}}}) == "true"


def test_keyed_list_diff_reports_insert_not_shift():
    a = [{"queryRef": "A"}, {"queryRef": "B"}]
    b = [{"queryRef": "C"}, {"queryRef": "A"}, {"queryRef": "B"}]
    changes = json_diff(a, b)
    assert [c.path for c in changes] == ["[C]"]


def test_reorder_detected():
    a = [{"name": "x"}, {"name": "y"}]
    changes = json_diff(a, list(reversed(a)))
    assert changes == [Change(("(order)",), ["x", "y"], ["y", "x"])]


def test_conflict_markers_use_ours_side():
    raw, err = parse_json(b'{\n<<<<<<< HEAD\n"a": 1\n=======\n"a": 2\n>>>>>>> x\n}')
    assert raw == {"a": 1} and "conflict" in err


def test_git_demo(tmp_path):
    repo = demo(tmp_path)
    base, head = GitSource(repo, "HEAD~1", ""), GitSource(repo, "HEAD", "")
    diff = diff_project(base, head)
    s = statuses(diff)
    assert s[("Overview", "c3likes")][0] == "removed"
    assert s[("Overview", "h8donut")][0] == "added"
    assert s[("Overview", "g7slicer")] == ("layout", ["moved"])
    assert s[("Overview", "d4bycat")] == ("changed", ["resized", "fields", "format"])
    assert s[("Overview", "e5trend")] == ("changed", ["type"])
    assert s[("Overview", "f6table")] == ("changed", ["filters"])
    assert s[("Overview", "a1title")][0] == "unchanged"
    assert s[("Authors", "t1")][0] == "invalid"
    assert [f.path for f in diff.files] == ["MasterPosts.Report/definition/pages/pages.json"]

    d4 = next(v for p in diff.reports[0].pages for v in p.visuals if v.name == "d4bycat")
    assert [(f.role, f.added, f.removed) for f in d4.fields] == [("Y", ["Avg Likes"], ["Total Posts"])]

    html = render(diff)
    assert "Avg likes by category" in html and "Unresolved merge conflict" in html


def test_worktree_vs_head_is_clean(tmp_path):
    repo = demo(tmp_path)
    diff = diff_project(GitSource(repo, "HEAD", ""), FolderSource(repo))
    assert diff.files == []
    assert all(r.status == "unchanged" for r in diff.reports)


def test_cli_merge_base_and_exit_code(tmp_path, monkeypatch):
    repo = demo(tmp_path)
    subprocess.run(["git", "-C", repo, "branch", "old", "HEAD~1"], check=True)
    monkeypatch.chdir(repo)
    out = tmp_path / "out.html"
    assert main(["old...main", "-o", str(out), "--no-open", "--exit-code"]) == 1
    assert "merge-base of old and main" in out.read_text()
    assert main(["HEAD", "-o", str(out), "--no-open", "--exit-code"]) == 0
