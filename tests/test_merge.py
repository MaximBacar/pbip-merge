import json
import shutil
import subprocess
from pathlib import Path

from pbip_diff.merge import (
    BOTH, CURRENT, INCOMING, MERGE, Decision, build_session, merge_file, merge_json, unmerged_paths,
)
from pbip_diff.mergecli import decisions_from_form, main as merge_main
from pbip_diff.mergeui import render_merge
from pbip_diff.source import FolderSource, GitSource

from test_diff import make_demo

lit = lambda v: {"expr": {"Literal": {"Value": v}}}
proj = lambda ref: {"queryRef": ref, "field": {"Column": {"Property": ref}}}


def merged(b, c, i, choices=None):
    conflicts = []
    return merge_json(b, c, i, choices or {}, conflicts), conflicts


def test_different_properties_combine():
    base = {"position": {"x": 1, "y": 1}, "title": lit("'A'")}
    cur = {"position": {"x": 5, "y": 1}, "title": lit("'A'")}
    inc = {"position": {"x": 1, "y": 9}, "title": lit("'B'")}
    out, conflicts = merged(base, cur, inc)
    assert out == {"position": {"x": 5, "y": 9}, "title": lit("'B'")} and conflicts == []


def test_same_property_conflict_points_at_literal_and_takes_choice():
    base = {"objects": {"labels": [{"properties": {"color": lit("'#111'"), "size": lit("10D")}}]}}
    cur = {"objects": {"labels": [{"properties": {"color": lit("'#222'"), "size": lit("10D")}}]}}
    inc = {"objects": {"labels": [{"properties": {"color": lit("'#333'"), "size": lit("12D")}}]}}
    out, conflicts = merged(base, cur, inc)
    assert [c.path for c in conflicts] == ["objects.labels[0].properties.color"]
    assert out["objects"]["labels"][0]["properties"] == {"color": lit("'#222'"), "size": lit("12D")}
    out, _ = merged(base, cur, inc, {"objects.labels[0].properties.color": INCOMING})
    assert out["objects"]["labels"][0]["properties"]["color"] == lit("'#333'")


def test_fields_added_on_both_sides_are_kept():
    base = [proj("A"), proj("B")]
    out, conflicts = merged(base, base + [proj("C")], [proj("D")] + base)
    assert [p["queryRef"] for p in out] == ["D", "A", "B", "C"] and not conflicts


def test_field_removed_one_side_edited_other_conflicts():
    base = [proj("A"), proj("B")]
    edited = dict(proj("B"), displayName="Bee")
    out, conflicts = merged(base, [proj("A")], [proj("A"), edited])
    assert [c.path for c in conflicts] == ["[B]"]
    assert [p["queryRef"] for p in out] == ["A"]  # current (removed) while unresolved


def test_page_order_merges_as_list():
    base = ["P1", "P2", "P3"]
    out, conflicts = merged(base, ["P1", "P3", "P2"], ["P1", "P2", "P3", "P4"])
    assert out == ["P1", "P3", "P2", "P4"] and not conflicts


def test_active_page_never_conflicts():
    out, conflicts = merged({"activePageName": "a"}, {"activePageName": "b"}, {"activePageName": "c"})
    assert out == {"activePageName": "b"} and not conflicts


def test_text_merge_conflict_choices():
    base = b"one\ntwo\nthree\n"
    fm = merge_file("m.tmdl", base, b"one\nTWO\nthree\n", b"one\n2\nthree\n")
    assert fm.kind == "text" and len(fm.conflicts) == 1
    assert fm.result(MERGE, {"#0": INCOMING}) == b"one\n2\nthree\n"
    assert fm.result(MERGE, {"#0": BOTH}) == b"one\nTWO\n2\nthree\n"
    assert fm.result(CURRENT) == b"one\nTWO\nthree\n"


def test_delete_modify_cannot_merge():
    fm = merge_file("v.json", b'{"a": 1}', None, b'{"a": 2}')
    assert not fm.can_merge and fm.result(INCOMING) == b'{"a": 2}' and fm.result(CURRENT) is None


def test_whole_side_keeps_original_bytes():
    cur = b'{ "a": 1,   "b": 2 }\n'
    fm = merge_file("v.json", b'{"a": 1, "b": 1}', cur, b'{"a": 1, "b": 1}')
    assert fm.result(MERGE) == cur


# ---------------------------------------------------------------- end to end


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True).stdout


VIS = "MasterPosts.Report/definition/pages/dd34709e01702e11a610/visuals"


def edit(repo, name, fn):
    path = Path(repo) / VIS / name / "visual.json"
    data = json.loads(path.read_text())
    fn(data)
    path.write_text(json.dumps(data, indent=2))


def conflicted_repo(tmp_path):
    make_demo.main(str(tmp_path / "repo"))
    repo = tmp_path / "repo"
    git(repo, "reset", "-q", "--hard", "HEAD~1")  # back to the simple base report
    git(repo, "checkout", "-qb", "feature")
    edit(repo, "d4bycat", lambda d: d["position"].update(y=215))
    edit(repo, "c3likes", lambda d: d["position"].update(width=310))
    git(repo, "commit", "-qam", "feature")
    git(repo, "checkout", "-q", "main")
    edit(repo, "d4bycat", lambda d: d["position"].update(x=25))
    edit(repo, "c3likes", lambda d: d["position"].update(width=290))
    git(repo, "commit", "-qam", "main")
    git(repo, "merge", "feature")
    return repo


def test_git_merge_resolve(tmp_path):
    repo = conflicted_repo(tmp_path)
    only = set(unmerged_paths(repo))
    assert len(only) == 2
    session = build_session(
        "git", GitSource(repo, git(repo, "merge-base", "HEAD", "MERGE_HEAD").strip(), ""),
        GitSource(repo, "HEAD", ""), GitSource(repo, "MERGE_HEAD", ""), only=only,
        worktree=FolderSource(repo), project=repo, repo=repo,
    )
    by_name = {f.path.split("/")[-2]: f for f in session.files}
    assert by_name["d4bycat"].conflicts == []          # x vs y: auto-merged
    assert [c.path for c in by_name["c3likes"].conflicts] == ["position.width"]

    # Through the form, as the browser would send it
    n = session.files.index(by_name["c3likes"])
    form = {f"s{i}": MERGE for i in range(len(session.files))} | {f"c{n}_0": INCOMING}
    decisions = decisions_from_form(session, form)
    assert session.problems(decisions) == []
    assert "Merge" in render_merge(session, decisions, ("main", "feature"), token="t")
    session.apply_git(decisions)

    assert git(repo, "ls-files", "-u") == ""
    pos = lambda name: json.loads((repo / VIS / name / "visual.json").read_text())["position"]
    assert (pos("d4bycat")["x"], pos("d4bycat")["y"]) == (25, 215)
    assert pos("c3likes")["width"] == 310


def test_cli_accept_merge_fails_on_real_conflict(tmp_path, monkeypatch):
    repo = conflicted_repo(tmp_path)
    monkeypatch.chdir(repo)
    assert merge_main(["--accept", "merge"]) == 1
    assert merge_main(["--accept", "incoming"]) == 0
    assert git(repo, "ls-files", "-u") == ""


def test_folders_merge_writes_output(tmp_path):
    make_demo.main(str(tmp_path / "repo"))
    repo = tmp_path / "repo"
    base, cur, inc = (tmp_path / n for n in ("base", "cur", "inc"))
    git(repo, "reset", "-q", "--hard", "HEAD~1")
    for d in (base, cur, inc):
        shutil.copytree(repo, d, ignore=shutil.ignore_patterns(".git"))
    edit(cur, "d4bycat", lambda d: d["position"].update(x=25))
    edit(inc, "d4bycat", lambda d: d["position"].update(y=215))
    edit(inc, "e5trend", lambda d: d["visual"].update(visualType="areaChart"))  # incoming only
    out = tmp_path / "out"
    assert merge_main(["--folders", str(base), str(cur), str(inc), "-o", str(out), "--accept", "merge"]) == 0
    read = lambda name: json.loads((out / VIS / name / "visual.json").read_text())
    assert read("d4bycat")["position"]["x"] == 25 and read("d4bycat")["position"]["y"] == 215
    assert read("e5trend")["visual"]["visualType"] == "areaChart"
