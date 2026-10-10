import json

from typer.testing import CliRunner
from textual.widgets import Static, Tree

from bibliotheke import At, Graph
from bibliotheke.browse import Browser, node_text, type_counts, type_text
from bibliotheke.cli import app

MODEL = """\
schema_version: 1
description: A small library.
nodes:
  paper:
    description: A paper.
    facts:
      year: {type: int, required: true}
  person:
    description: An author.
  venue: {}
edges:
  authored_by: {from: paper, to: person}
"""


def library(tmp_path):
    (tmp_path / "bibliotheke.yaml").write_text(MODEL)
    (tmp_path / "nodes").mkdir()
    g = Graph.open(tmp_path)
    g.add_node("person", "Anna Berg")
    g.add_node("paper", "On Graphs", id="on-graphs")
    g.add_node("paper", "On Time", id="on-time")
    g.add_fact("on-graphs", "year", 2024)
    g.add_fact("on-time", "year", 2025, valid_from=2025)
    g.add_edge("on-graphs", "authored_by", "anna-berg")
    (tmp_path / "nodes" / "paper" / "on-graphs.md").write_text("# Notes\nline two\n")
    return g


def test_type_counts_and_texts(tmp_path):
    g = library(tmp_path)
    assert type_counts(g, At()) == [("paper", 2), ("person", 1), ("venue", 0)]  # venue from the model, unused
    assert type_counts(g, At(valid_at=2024)) == [("paper", 1), ("person", 1), ("venue", 0)]
    text = type_text(g, "paper", 2)
    assert "A paper." in text and "year: int  required" in text and "→ authored_by person" in text
    assert "← paper authored_by" in type_text(g, "person", 1)
    text = node_text(g, g.node("on-graphs"), At())
    assert "year: 2024" in text and "→ authored_by Anna Berg" in text and "# Notes" in text
    assert "← On Graphs authored_by" in node_text(g, g.node("anna-berg"), At())


def test_types_command(tmp_path):
    runner = CliRunner()
    library(tmp_path)
    result = runner.invoke(app, ["types", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "A small library." in result.output and "paper" in result.output and "3 nodes in 3 types" in result.output
    data = json.loads(runner.invoke(app, ["types", "--json", "--root", str(tmp_path)]).output)
    assert data[0] == {"type": "paper", "nodes": 2, "description": "A paper."}


async def test_browser_walks_types_to_nodes(tmp_path):
    g = library(tmp_path)
    appl = Browser(g, At())
    async with appl.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.2)
        tree = appl.query_one("#tree", Tree)
        labels = [str(child.label) for child in tree.root.children]
        assert labels[0].startswith("paper") and "2" in labels[0]
        papers = tree.root.children[0]
        papers.expand()
        await pilot.pause(0.1)
        assert [str(leaf.label) for leaf in papers.children] == ["On Graphs", "On Time"]
        tree.select_node(papers.children[0])
        await pilot.pause(0.1)
        shown = str(appl.query_one("#text", Static).render())
        assert "On Graphs" in shown and "year: 2024" in shown
        # Filtering keeps only matching nodes, and opens their types.
        await pilot.press("slash")
        await pilot.pause(0.1)
        for ch in "time":
            await pilot.press(ch)
        await pilot.pause(0.2)
        labels = [str(child.label) for child in tree.root.children]
        assert len(labels) == 1 and labels[0].startswith("paper")
        assert [str(leaf.label) for leaf in tree.root.children[0].children] == ["On Time"]
        await pilot.press("escape")
        await pilot.pause(0.2)
        assert len(tree.root.children) == 3
