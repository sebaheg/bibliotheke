import json

import pytest
from typer.testing import CliRunner

from bibliotheke.cli import app
from bibliotheke.graph import GraphError

runner = CliRunner()


def run(root, *args):
    result = runner.invoke(app, [*args, "--root", str(root)])
    if result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    assert result.exit_code == 0, result.output
    return result.output.strip()


@pytest.fixture
def root(tmp_path):
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    run(tmp_path, "add", "node", "person", "Anna Berg")
    run(tmp_path, "add", "node", "organisation", "DeepMind")
    return tmp_path


def test_add_show_update_history(root):
    edge_id = run(root, "add", "edge", "anna-berg", "works_at", "deepmind", "--valid-from", "2019")
    fact_id = run(root, "add", "fact", "anna-berg", "field", "ai")

    shown = json.loads(run(root, "show", "anna-berg", "--json"))
    assert [f["id"] for f in shown["facts"]] == [fact_id]
    assert shown["out_edges"][0]["to"] == "deepmind"
    assert json.loads(run(root, "show", "deepmind", "--json"))["in_edges"][0]["from"] == "anna-berg"

    assert json.loads(run(root, "show", "anna-berg", "--json", "--valid-at", "2018"))["out_edges"] == []

    run(root, "update", edge_id, "--valid-to", "2022")
    run(root, "retract", fact_id)
    current = json.loads(run(root, "show", "anna-berg", "--json"))
    assert current["facts"] == []
    assert current["out_edges"][0]["valid_to"] == "2022"
    past = json.loads(run(root, "history", "anna-berg", "--json"))
    assert len(past["out_edges"]) == 2 and len(past["facts"]) == 1

    assert "Anna Berg" in run(root, "show", "anna-berg")
    assert "superseded" in run(root, "history", "anna-berg")


def test_list_search_check(root):
    listed = json.loads(run(root, "list", "--json", "--type", "person"))
    assert listed == [{"id": "anna-berg", "type": "person", "name": "Anna Berg"}]
    assert "deepmind" in run(root, "search", "deep")
    assert run(root, "check") == "OK: 2 nodes"


def test_unknown_node_is_a_graph_error(root):
    with pytest.raises(GraphError):
        run(root, "show", "nobody")


def test_import_and_export(root, tmp_path):
    batch = tmp_path / "batch.yaml"
    batch.write_text(
        """
nodes:
  - type: paper
    name: "Paper A | one"
    facts: {year: 2024}
    edges: [{rel: authored_by, to: anna-berg}]
    body: "notes"
  - type: paper
    name: Paper B
    facts: {year: 2025}
    edges: [{rel: cites, to: paper-a-one}]
  - type: paper
    name: Paper C
"""
    )
    assert run(root, "import", str(batch)) == "1 bodies added, 2 edges added, 2 facts added, 3 nodes added"
    assert json.loads(run(root, "import", str(batch), "--json")) == {"facts skipped": 2, "edges skipped": 2, "bodies kept": 1}

    table = run(root, "export", "--type", "paper", "--columns", "name,year,>authored_by,<cites", "--sort=-year")
    lines = table.splitlines()
    assert lines[0] == "| name | year | authored_by | ← cites |"
    assert lines[2] == "| Paper B | 2025 |  |  |"
    assert lines[3] == "| [Paper A \\| one](nodes/paper/paper-a-one.md) | 2024 | Anna Berg | Paper B |"
    assert lines[4].startswith("| Paper C |")
    assert json.loads(run(root, "show", "paper-a-one", "--json"))["body"] == "nodes/paper/paper-a-one.md"


def test_path_rank_communities(root):
    run(root, "add", "node", "person", "Bo Ek")
    run(root, "add", "edge", "anna-berg", "works_at", "deepmind", "--valid-from", "2019")
    run(root, "add", "edge", "bo-ek", "knows", "anna-berg")

    found = json.loads(run(root, "path", "bo-ek", "deepmind", "--json"))
    assert found["length"] == 2 and found["nodes"] == ["bo-ek", "anna-berg", "deepmind"]
    assert [e["rel"] for e in found["edges"]] == ["knows", "works_at"]
    table = run(root, "path", "deepmind", "bo-ek")
    assert table.startswith("2 hops") and "← works_at" in table and "← knows" in table
    with pytest.raises(GraphError, match="no path"):
        run(root, "path", "deepmind", "bo-ek", "--directed")
    with pytest.raises(GraphError, match="not in view"):
        run(root, "path", "bo-ek", "deepmind", "--valid-at", "2018")

    ranked = json.loads(run(root, "rank", "--json"))
    assert ranked[0] == {"id": "anna-berg", "type": "person", "name": "Anna Berg", "score": 2.0}
    assert [r["id"] for r in json.loads(run(root, "rank", "--json", "--type", "person", "--rel", "knows"))] == ["anna-berg", "bo-ek"]
    assert "betweenness" in run(root, "rank", "--by", "betweenness", "--top", "1")
    with pytest.raises(GraphError, match="--by"):
        run(root, "rank", "--by", "fame")

    groups = json.loads(run(root, "communities", "--json"))
    assert groups[0]["size"] == 3 and sorted(groups[0]["members"]) == ["anna-berg", "bo-ek", "deepmind"]
    assert run(root, "communities").splitlines()[1].split()[:2] == ["0", "3"]
