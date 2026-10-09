import json

import pytest
import yaml
from typer.testing import CliRunner

from bibliotheke import Graph, GraphError
from bibliotheke.cli import app

MODEL = """\
schema_version: 1
description: A multilingual vocabulary.
time: ignored
vocabularies:
  lang: [en, sv]
nodes:
  word:
    description: A single word in one language.
    id: "<name>-<lang>"
    facts:
      lang: {type: enum, values: lang, required: true}
      definition: {type: text}
      example: {type: text, many: true}
      frequency: int
  source:
    facts:
      url: url
edges:
  translates_to:
    from: word
    to: word
    symmetric: true
  defined_in:
    from: word
    to: source
"""


@pytest.fixture
def graph(tmp_path):
    (tmp_path / "bibliotheke.yaml").write_text(MODEL)
    (tmp_path / "nodes").mkdir()
    g = Graph.open(tmp_path)
    g.add_node("word", "adamant", id="adamant-en")
    g.add_fact("adamant-en", "lang", "en")
    g.add_node("word", "envis", id="envis-sv")
    g.add_fact("envis-sv", "lang", "sv")
    return g


def test_model_is_parsed(graph):
    assert graph.model.description == "A multilingual vocabulary."
    assert graph.model.nodes["word"].facts["lang"].values == ("en", "sv")
    assert graph.model.edges["translates_to"].symmetric


def test_no_model_accepts_anything(tmp_path):
    g = Graph.init(tmp_path)
    assert g.model is None
    g.add_node("anything", "X")
    g.add_fact("x", "whatever", "1", valid_from=2020)


def test_writes_are_checked(graph):
    with pytest.raises(GraphError, match="no node type 'person'"):
        graph.add_node("person", "Anna")
    with pytest.raises(GraphError, match="pass it with --id"):
        graph.add_node("word", "bereft")
    with pytest.raises(GraphError, match="no fact 'colour'"):
        graph.add_fact("adamant-en", "colour", "red")
    with pytest.raises(GraphError, match="must be one of en, sv"):
        graph.add_fact("adamant-en", "lang", "xx")
    with pytest.raises(GraphError, match="is an int"):
        graph.add_fact("adamant-en", "frequency", "often")
    with pytest.raises(GraphError, match="time: ignored"):
        graph.add_fact("adamant-en", "definition", "firm", valid_from=2020)
    with pytest.raises(GraphError, match="takes one value"):
        graph.add_fact("adamant-en", "lang", "sv")


def test_values_are_coerced_and_many_allows_several(graph):
    assert graph.add_fact("adamant-en", "frequency", "12").value == 12
    graph.add_fact("adamant-en", "example", "She was adamant.")
    graph.add_fact("adamant-en", "example", "He stayed adamant.")


def test_edges_are_checked(graph):
    graph.add_node("source", "Oxford")
    with pytest.raises(GraphError, match="no edge 'likes'"):
        graph.add_edge("adamant-en", "likes", "envis-sv")
    with pytest.raises(GraphError, match="from word to source, not from source to word"):
        graph.add_edge("oxford", "defined_in", "adamant-en")
    graph.add_edge("adamant-en", "translates_to", "envis-sv")
    with pytest.raises(GraphError, match="symmetric"):
        graph.add_edge("envis-sv", "translates_to", "adamant-en")


def test_check_reports_hand_edits(graph, tmp_path):
    assert graph.check() == []
    path = tmp_path / "nodes" / "word" / "envis-sv.yaml"
    data = yaml.safe_load(path.read_text())
    data["facts"][0]["value"] = "de"
    path.write_text(yaml.safe_dump(data))
    graph.add_node("word", "bereft", id="wrong-id")
    problems = Graph.open(tmp_path).check()
    assert any("envis-sv" in p and "must be one of en, sv" in p for p in problems)
    assert any("wrong-id: missing required fact 'lang'" in p for p in problems)


def test_check_reports_id_template(graph):
    graph.add_node("word", "bereft", id="bereft")
    graph.add_fact("bereft", "lang", "en")
    assert graph.check() == ["bereft: id should be 'bereft-en' (template '<name>-<lang>')"]


def test_import_uses_id_template_and_checks_first(graph):
    counts = graph.apply({"nodes": [{"type": "word", "name": "bereft", "facts": {"lang": "en"}}]})
    assert counts["nodes added"] == 1
    assert "bereft-en" in graph.nodes
    with pytest.raises(GraphError, match="missing required fact 'lang'"):
        graph.apply({"nodes": [{"type": "word", "name": "sorg", "id": "sorg-sv"}]})
    with pytest.raises(GraphError, match="no fact 'colour'"):
        graph.apply({"nodes": [{"type": "word", "name": "x", "facts": {"lang": "en", "colour": "red"}}]})
    assert "sorg-sv" not in graph.nodes and "x-en" not in graph.nodes


def test_import_skips_reverse_of_symmetric_edge(graph):
    graph.add_edge("adamant-en", "translates_to", "envis-sv")
    counts = graph.apply({"edges": [{"from": "envis-sv", "rel": "translates_to", "to": "adamant-en"}]})
    assert counts["edges skipped"] == 1


@pytest.mark.parametrize(
    "bad, message",
    [
        ("nodes: {word: {facts: {lang: {type: colour}}}}", "type must be one of"),
        ("nodes: {word: {facts: {lang: {type: enum, values: langs}}}}", "no vocabulary 'langs'"),
        ("nodes: {word: {}}\nedges: {x: {from: word, to: person}}", "no node type 'person'"),
        ("nodes: {word: {}}\ntime: sometimes", "time must be one of"),
        ("nodes: {part_of_speech: {}}", "lowercase with hyphens, e.g. 'part-of-speech'"),
    ],
)
def test_bad_model_is_reported(tmp_path, bad, message):
    (tmp_path / "bibliotheke.yaml").write_text(bad)
    with pytest.raises(GraphError, match=message):
        Graph.open(tmp_path)


def test_cli_model(graph, tmp_path):
    runner = CliRunner()
    result = runner.invoke(app, ["model", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "translates_to" in result.output and "word ↔ word" in result.output
    result = runner.invoke(app, ["model", "--json", "--root", str(tmp_path)])
    data = json.loads(result.output)
    assert data["nodes"]["word"]["facts"]["lang"] == {"type": "enum", "values": "lang", "required": True}


POS_MODEL = """\
schema_version: 1
nodes:
  word:
    id: "<name>-<pos>-<lang>"
    facts:
      lang: {type: enum, values: [en, sv], required: true}
  part-of-speech: {}
edges:
  pos: {from: word, to: part-of-speech, required: true, many: false}
"""


@pytest.fixture
def pos_graph(tmp_path):
    (tmp_path / "bibliotheke.yaml").write_text(POS_MODEL)
    (tmp_path / "nodes").mkdir()
    g = Graph.open(tmp_path)
    g.add_node("part-of-speech", "verb")
    g.add_node("part-of-speech", "noun")
    return g


def test_id_template_fills_in_from_an_edge(pos_graph):
    pos_graph.apply({"nodes": [{"type": "word", "name": "lot", "facts": {"lang": "en"}, "edges": [{"rel": "pos", "to": "verb"}]}]})
    assert "lot-verb-en" in pos_graph.nodes
    assert pos_graph.check() == []


def test_required_and_single_target_edges(pos_graph):
    with pytest.raises(GraphError, match="missing required edge 'pos'"):
        pos_graph.apply({"nodes": [{"type": "word", "name": "lot", "id": "lot-verb-en", "facts": {"lang": "en"}}]})
    pos_graph.add_node("word", "lot", id="lot-verb-en")
    pos_graph.add_fact("lot-verb-en", "lang", "en")
    assert pos_graph.check() == ["lot-verb-en: missing required edge 'pos'"]
    edge = pos_graph.add_edge("lot-verb-en", "pos", "verb")
    with pytest.raises(GraphError, match="takes one target"):
        pos_graph.add_edge("lot-verb-en", "pos", "noun")
    with pytest.raises(GraphError, match="takes one target"):
        pos_graph.apply({"edges": [{"from": "lot-verb-en", "rel": "pos", "to": "noun"}]})
    pos_graph.retract(edge.id)
    pos_graph.add_edge("lot-verb-en", "pos", "noun")
    assert pos_graph.check() == ["lot-verb-en: id should be 'lot-noun-en' (template '<name>-<pos>-<lang>')"]


def test_id_placeholder_must_exist(tmp_path):
    (tmp_path / "bibliotheke.yaml").write_text('nodes: {word: {id: "<name>-<pos>"}}')
    with pytest.raises(GraphError, match="<pos> is not name, a fact or an edge of word"):
        Graph.open(tmp_path)
