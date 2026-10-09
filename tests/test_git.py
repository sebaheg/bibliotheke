import json
import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from bibliotheke import Graph, GraphError
from bibliotheke.cli import app
from bibliotheke.diff import diff_graphs, summary

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
runner = CliRunner()


def run(root, *args):
    result = runner.invoke(app, [*args, "--root", str(root)])
    if result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    assert result.exit_code == 0, result.output
    return result.output.strip()

T1 = "2026-10-01T10:00:00Z"
T2 = "2026-10-02T10:00:00Z"


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """A repository with the graph in a subdirectory, two commits and one
    uncommitted change."""
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    kg = tmp_path / "kg"
    g = Graph.init(kg)
    g.add_node("person", "Anna Berg")
    g.add_node("organisation", "DeepMind")
    g.add_node("person", "Old Timer")
    field = g.add_fact("anna-berg", "field", "ai", now=T1)
    title = g.add_fact("anna-berg", "title", "researcher", now=T1)
    edge = g.add_edge("anna-berg", "works_at", "deepmind", valid_from=2019, now=T1)
    (kg / "nodes" / "person" / "anna-berg.md").write_text("# Anna\nfirst draft\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "one")

    g.update(field.id, value="ml", now=T2)
    g.retract(title.id, now=T2)
    g.update(edge.id, valid_to=2024, now=T2)
    g.add_node("person", "Bo Ek")
    g.add_edge("bo-ek", "knows", "anna-berg", now=T2)
    (kg / "nodes" / "person" / "anna-berg.md").write_text("# Anna\nsecond draft\n")
    (kg / "nodes" / "person" / "old-timer.yaml").unlink()
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "two")

    g.add_fact("bo-ek", "field", "music", now=T2)
    return kg


def test_graph_at_commit_is_the_old_graph(repo):
    old = Graph.open(repo, at_commit="HEAD~1")
    assert set(old.nodes) == {"anna-berg", "deepmind", "old-timer"}
    assert [f.value for f in old.facts("anna-berg")] == ["ai", "researcher"]
    assert old.out_edges("anna-berg")[0].valid_to is None
    assert old.body("anna-berg") == "# Anna\nfirst draft\n"
    assert old.has_body(old.node("anna-berg")) and not old.has_body(old.node("deepmind"))
    assert len(old.source.commit) == 40

    head = Graph.open(repo, at_commit="HEAD")
    assert set(head.nodes) == {"anna-berg", "deepmind", "bo-ek"}
    assert head.facts("bo-ek") == []
    assert head.body("anna-berg") == "# Anna\nsecond draft\n"
    assert Graph.open(repo).facts("bo-ek")[0].value == "music"


def test_graph_at_commit_is_read_only(repo):
    old = Graph.open(repo, at_commit="HEAD~1")
    with pytest.raises(GraphError, match="read-only"):
        old.add_fact("anna-berg", "x", 1)
    with pytest.raises(GraphError, match="read-only"):
        old.apply({"nodes": [{"type": "person", "name": "X"}]})


def test_bad_revision_or_no_repository(repo, tmp_path):
    with pytest.raises(GraphError, match="no commit 'nope'"):
        Graph.open(repo, at_commit="nope")
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    Graph.init(outside)
    with pytest.raises(GraphError, match="not a git repository"):
        Graph.open(outside, at_commit="HEAD")


def test_cli_reads_at_commit(repo):
    shown = json.loads(run(repo, "show", "anna-berg", "--json", "--at-commit", "HEAD~1"))
    assert [f["value"] for f in shown["facts"]] == ["ai", "researcher"]
    assert shown["body"] == "nodes/person/anna-berg.md"
    assert "old-timer" in run(repo, "list", "--at-commit", "HEAD~1")
    assert "old-timer" not in run(repo, "list")
    table = run(repo, "export", "--columns", "name,field", "--at-commit", "HEAD~1")
    assert "| [Anna Berg](nodes/person/anna-berg.md) | ai |" in table
    assert [r["id"] for r in json.loads(run(repo, "rank", "--json", "--at-commit", "HEAD~1"))] == ["anna-berg", "deepmind", "old-timer"]
    assert "--at-commit" not in runner.invoke(app, ["add", "fact", "--help"]).output


def test_diff_between_revisions(repo):
    found = diff_graphs(Graph.open(repo, at_commit="HEAD~1"), Graph.open(repo, at_commit="HEAD"))
    assert [n["id"] for n in found["nodes"]["added"]] == ["bo-ek"]
    assert [n["id"] for n in found["nodes"]["removed"]] == ["old-timer"]
    assert found["nodes"]["changed"] == []
    assert [(f["key"], f["value"]) for f in found["facts"]["changed"]] == [("field", "ml")]
    assert [f["key"] for f in found["facts"]["retracted"]] == ["title"]
    assert found["facts"]["added"] == []
    assert [e["rel"] for e in found["edges"]["added"]] == ["knows"]
    assert [e["to"] for e in found["edges"]["changed"]] == ["deepmind"]
    assert summary(found) == "1 node added, 1 node removed, 1 fact changed, 1 fact retracted, 1 edge added, 1 edge changed"

    data = json.loads(run(repo, "diff", "HEAD~1", "HEAD", "--json"))
    assert data["from"] != data["to"] and data["facts"]["changed"][0]["node"] == "anna-berg"
    working = json.loads(run(repo, "diff", "HEAD", "--json"))
    assert working["to"] == "working tree"
    assert [f["key"] for f in working["facts"]["added"]] == ["field"] and working["nodes"] == {"added": [], "removed": [], "changed": []}

    text = run(repo, "diff", "HEAD~1", "HEAD")
    assert text.startswith("1 node added") and "retracted" in text and "works_at deepmind" in text
    assert run(repo, "diff", "HEAD~1", "HEAD~1") == "no changes"
