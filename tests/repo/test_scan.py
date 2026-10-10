import json
import os
import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from bibliotheke import At, Graph, GraphError
from bibliotheke.cli import app
from bibliotheke.repo import MARKER, scan

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

ANNA = {"GIT_AUTHOR_NAME": "Anna Berg", "GIT_AUTHOR_EMAIL": "anna@example.com"}
BO = {"GIT_AUTHOR_NAME": "Bo Ek", "GIT_AUTHOR_EMAIL": "bo@example.com"}
runner = CliRunner()


def commit(repo, message, date, who=ANNA, committed=None):
    env = {
        **os.environ,
        **who,
        "GIT_COMMITTER_NAME": "CI",
        "GIT_COMMITTER_EMAIL": "ci@example.com",
        "GIT_AUTHOR_DATE": f"{date}T12:00:00+02:00",
        "GIT_COMMITTER_DATE": f"{committed or date}T12:00:00+02:00",
    }
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True, capture_output=True, env=env)


def run(*args):
    result = runner.invoke(app, ["repo", *args])
    if result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    assert result.exit_code == 0, result.output
    return result.output.strip()


def t(date, clock="10:00:00"):
    return f"{date}T{clock}Z"  # 12:00 +02:00 is 10:00 UTC


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("a")
    (tmp_path / "README.md").write_text("hi")
    commit(tmp_path, "start", "2026-01-01")
    (tmp_path / "src" / "a.py").write_text("aa")
    (tmp_path / "src" / "b.py").write_text("b")
    commit(tmp_path, "a and b together", "2026-01-15", who=BO)
    (tmp_path / "src" / "b.py").rename(tmp_path / "src" / "c.py")
    commit(tmp_path, "rename b to c", "2026-02-01")
    (tmp_path / "README.md").unlink()
    commit(tmp_path, "drop readme", "2026-02-15", who=BO, committed="2026-03-01")
    return tmp_path


def test_scan_builds_files_dirs_and_authors(repo):
    found = scan(repo, now="2026-10-01T00:00:00Z")
    g = found.graph
    assert found.commits == 4 and found.first == t("2026-01-01") and found.last == t("2026-02-15")
    assert g.root == repo.resolve() and not g.source.writable
    assert {n.id: n.type for n in g.nodes.values()} == {
        "src": "dir", "src-a-py": "file", "src-b-py": "file", "readme-md": "file", "anna-berg": "author", "bo-ek": "author",
    }
    assert g.node("src").name == "src/" and g.node("src-b-py").name == "src/c.py"
    assert [(f.key, f.value) for f in g.facts("anna-berg")] == [("email", "anna@example.com"), ("commits", 2)]
    assert [(f.key, f.value) for f in g.facts("src-a-py")] == [("path", "src/a.py"), ("dir", "src"), ("ext", "py"), ("commits", 2)]
    assert [(f.key, f.value) for f in g.facts("readme-md", At(valid_at="2026-01-05"))] == [("path", "README.md"), ("dir", "/"), ("ext", "md"), ("commits", 2)]
    assert [e.rel for e in g.out_edges("src")] == ["contains"] * 3  # a, b (ended at the rename), c
    assert [e.to for e in g.out_edges("src", At(valid_at="2026-03"))] == ["src-a-py", "src-b-py"]
    assert [(o.id, e.rel) for o, e in g.in_edges("src-a-py")] == [
        ("anna-berg", "touched"), ("readme-md", "co_changed"), ("src", "contains"), ("bo-ek", "touched"),
    ]
    assert g.node("bo-ek") not in g.list_nodes(At(valid_at="2026-01-05"))  # before Bo's first commit
    # The deletion was authored on 2026-02-15 but committed on 2026-03-01.
    gone = [f for f in g.node("readme-md").facts if f.key == "path"][0]
    assert (gone.valid_from, gone.valid_to, gone.known_at) == (t("2026-01-01"), t("2026-02-15", "09:59:59"), t("2026-01-01"))


def test_scan_renames_and_deletions_are_time_bound(repo):
    g = scan(repo).graph
    # b was renamed to c: one node, two path versions that hand over cleanly.
    paths = [f for f in g.node("src-b-py").facts if f.key == "path"]
    assert [(f.value, f.valid_from, f.valid_to) for f in paths] == [
        ("src/b.py", t("2026-01-15"), t("2026-02-01", "09:59:59")),
        ("src/c.py", t("2026-02-01"), None),
    ]
    assert [f.value for f in g.facts("src-b-py", At(valid_at="2026-01-20")) if f.key == "path"] == ["src/b.py"]
    assert [f.value for f in g.facts("src-b-py", At(valid_at=t("2026-02-01"))) if f.key == "path"] == ["src/c.py"]
    assert [e.valid_to for e in g.out_edges("src", at=None) if e.to == "src-b-py"] == [t("2026-02-01", "09:59:59"), None]
    # README existed from the first commit until the second before its deletion.
    readme = g.node("readme-md")
    assert readme in g.list_nodes(At(valid_at="2026-02-10"))
    assert readme not in g.list_nodes(At(valid_at=t("2026-02-15")))
    assert readme not in g.list_nodes(At(valid_at="2026-09"))
    assert {n.id for n in g.list_nodes(At(valid_at="2026-01-05"))} == {"src", "src-a-py", "readme-md", "anna-berg"}
    assert all(e.valid_to for o, e in g.in_edges("readme-md", at=None))
    assert g.check() == []


def test_scan_edges_are_one_per_commit(repo):
    g = scan(repo).graph
    touched = [e for e in g.out_edges("bo-ek") if e.rel == "touched"]
    assert sorted(e.to for e in touched) == ["src-a-py", "src-b-py"]  # a deletion is counted, not drawn
    assert [f.value for f in g.facts("bo-ek") if f.key == "commits"] == [2]
    coupled = [(n.id, e.valid_from, e.to, e.valid_to) for n in g.nodes.values() for e in n.edges if e.rel == "co_changed"]
    assert sorted(coupled) == [
        ("readme-md", t("2026-01-01"), "src-a-py", t("2026-02-15", "09:59:59")),  # ends when README goes
        ("src-a-py", t("2026-01-15"), "src-b-py", None),
    ]
    H = g.nx(rels=["co_changed"])
    assert H.has_edge("src-a-py", "src-b-py") and H.has_edge("src-b-py", "src-a-py")  # symmetric, mirrored
    assert g.nx(At(valid_at="2026-01-10"), rels=["co_changed"]).number_of_edges() == 2  # README–a and its mirror
    assert g.nx(At(valid_at="2026-03"), rels=["co_changed"]).number_of_edges() == 2  # a–c and its mirror
    assert g.model is not None and g.marker == MARKER


def test_deleted_file_leaves_its_directory_edge_bounded(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "x.py").write_text("x")
    commit(tmp_path, "add", "2026-01-01")
    (tmp_path / "lib" / "x.py").unlink()
    commit(tmp_path, "remove", "2026-02-01")
    g = scan(tmp_path).graph
    assert [e.valid_to for e in g.out_edges("lib", at=None) if e.rel == "contains"] == [t("2026-02-01", "09:59:59")]
    assert g.out_edges("lib", At(valid_at="2026-03")) == [] and g.out_edges("lib", At(valid_at="2026-01-15")) != []


def test_wide_commits_do_not_couple(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for i in range(4):
        (tmp_path / f"f{i}.txt").write_text(str(i))
    commit(tmp_path, "many files", "2026-01-01")
    found = scan(tmp_path, max_coupled=3)
    assert found.wide_commits == 1
    assert not any(e.rel == "co_changed" for n in found.graph.nodes.values() for e in n.edges)
    assert len(found.graph.out_edges("anna-berg")) == 4


def test_scan_errors(tmp_path):
    with pytest.raises(GraphError, match="not a directory"):
        scan(tmp_path / "nowhere")
    with pytest.raises(GraphError, match="not a git repository"):
        scan(tmp_path)


def test_cli_json_and_save(repo, tmp_path):
    assert json.loads(run(str(repo), "--json"))["nodes"] == {"dir": 1, "file": 3, "author": 2}
    out = tmp_path / "graph"
    assert run(str(repo), "--save", str(out)) == f"Wrote 6 nodes to {out}"
    saved = Graph.open(out)
    fresh = scan(repo).graph
    assert {n.id: n.name for n in saved.nodes.values()} == {n.id: n.name for n in fresh.nodes.values()}
    assert sum(len(n.edges) for n in saved.nodes.values()) == sum(len(n.edges) for n in fresh.nodes.values())
    assert saved.model is not None and saved.check() == []
    with pytest.raises(GraphError, match="not empty"):
        run(str(repo), "--save", str(out))


def test_imports_follow_the_history(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "store.py").write_text("import json\n")
    (pkg / "graph.py").write_text("from pkg import store\nfrom . import util\n")  # util does not exist yet
    commit(tmp_path, "start", "2026-01-01")
    (pkg / "util.py").write_text("")
    (pkg / "graph.py").write_text("from pkg import store\nfrom . import util\n# touched so imports are re-read\n")
    commit(tmp_path, "add util", "2026-02-01")
    (pkg / "store.py").rename(pkg / "files.py")
    (pkg / "graph.py").write_text("from pkg import files\nfrom . import util\n")
    commit(tmp_path, "rename store to files", "2026-03-01")
    (pkg / "graph.py").write_text("from pkg import files\n")
    commit(tmp_path, "drop util import", "2026-04-01")
    (pkg / "files.py").unlink()
    commit(tmp_path, "delete files", "2026-05-01")

    found = scan(tmp_path)
    g = found.graph
    assert found.parsed == 8  # every added, modified or renamed .py file, once per commit
    edges = sorted((e.to, e.valid_from, e.valid_to) for e in g.node("pkg-graph-py").edges if e.rel == "imports")
    assert edges == [
        # `from . import util` with no util.py yet means a name in the package, until util.py appears
        ("pkg-init-py", t("2026-01-01"), t("2026-02-01", "09:59:59")),
        ("pkg-store-py", t("2026-01-01"), t("2026-05-01", "09:59:59")),  # survives the rename, ends with the file
        ("pkg-util-py", t("2026-02-01"), t("2026-04-01", "09:59:59")),
    ]
    assert [e.to for e in g.out_edges("pkg-graph-py", At(valid_at="2026-03-15")) if e.rel == "imports"] == ["pkg-store-py", "pkg-util-py"]
    assert [e.to for e in g.out_edges("pkg-graph-py", At(valid_at="2026-06")) if e.rel == "imports"] == []
    assert [e.to for e in g.out_edges("pkg-store-py", at=None) if e.rel == "imports"] == []  # json is not in the repo
    H = g.nx(At(valid_at="2026-02-15"), rels=["imports"])
    assert sorted(H.edges()) == [("pkg-graph-py", "pkg-store-py"), ("pkg-graph-py", "pkg-util-py")]
    assert g.check() == []


def test_imports_can_be_skipped(repo):
    found = scan(repo, imports=False)
    assert found.parsed == 0
    assert not any(e.rel == "imports" for n in found.graph.nodes.values() for e in n.edges)
