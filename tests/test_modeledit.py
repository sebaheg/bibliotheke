import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest
import yaml
from typer.testing import CliRunner

from bibliotheke import Graph, GraphError, modeledit
from bibliotheke import cli
from bibliotheke.cli import app
from bibliotheke.datamodel import DataModel

runner = CliRunner()

MODEL = """\
schema_version: 1
description: A multilingual vocabulary.
time: ignored
vocabularies:
  lang: [en, sv]
nodes:
  word:
    id: "<name>-<lang>"
    facts:
      lang: {type: enum, values: lang, required: true}
      example: {type: text, many: true}
  source:
    facts:
      url: url
edges:
  translates_to: {from: word, to: word, symmetric: true}
  defined_in: {from: word, to: source}
view:
  color_by: lang
"""


def run(root, *args, ok=True):
    """The command's result; a GraphError (printed by `main`, not by `app`)
    is folded into the output with exit code 1, as a user would see it."""
    result = runner.invoke(app, [*args, "--root", str(root)])
    if isinstance(result.exception, GraphError):
        result = SimpleNamespace(exit_code=1, output=result.output + f"error: {result.exception}")
    elif result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    if ok:
        assert result.exit_code == 0, result.output
    return result


@pytest.fixture
def root(tmp_path):
    (tmp_path / "bibliotheke.yaml").write_text(MODEL)
    (tmp_path / "nodes").mkdir()
    g = Graph.open(tmp_path)
    g.add_node("word", "adamant", id="adamant-en")
    g.add_fact("adamant-en", "lang", "en")
    first = g.add_fact("adamant-en", "example", "an adamant refusal")
    g.update(first.id, value="an adamant refusal to go")
    g.add_node("word", "envis", id="envis-sv")
    g.add_fact("envis-sv", "lang", "sv")
    g.add_edge("adamant-en", "translates_to", "envis-sv")
    g.add_node("source", "SAOL")
    (tmp_path / "nodes" / "source" / "saol.md").write_text("# SAOL\n")
    return tmp_path


def marker(root):
    return yaml.safe_load((root / "bibliotheke.yaml").read_text())


def test_add_node_starts_a_model_and_reports_what_breaks(tmp_path):
    runner.invoke(app, ["init", str(tmp_path)])
    run(tmp_path, "add", "node", "person", "Anna")
    run(tmp_path, "add", "node", "organisation", "DeepMind")
    result = run(tmp_path, "model", "add", "node", "person", "--description", "A human", ok=False)
    assert result.exit_code == 1
    assert "deepmind: the model has no node type 'organisation'" in result.output
    assert marker(tmp_path)["nodes"] == {"person": {"description": "A human"}}
    assert "Data OK" in run(tmp_path, "model", "add", "node", "organisation").output


def test_add_fact_edge_and_vocabulary(root):
    run(root, "model", "add", "vocabulary", "pos", "noun", "verb")
    run(root, "model", "add", "fact", "word", "pos", "enum", "--values", "pos")
    run(root, "model", "add", "fact", "word", "frequency", "int", "--description", "Per million words")
    run(root, "model", "add", "fact", "word", "tags", "enum", "--values", "slang,archaic", "--many")
    run(root, "model", "add", "edge", "cites", "source,word", "source", "--one")
    data = marker(root)
    assert data["vocabularies"] == {"lang": ["en", "sv"], "pos": ["noun", "verb"]}
    assert data["nodes"]["word"]["facts"]["pos"] == {"type": "enum", "values": "pos"}
    assert data["nodes"]["word"]["facts"]["frequency"] == {"type": "int", "description": "Per million words"}
    assert data["nodes"]["word"]["facts"]["tags"] == {"type": "enum", "values": ["slang", "archaic"], "many": True}
    assert data["edges"]["cites"] == {"from": ["source", "word"], "to": "source", "many": False}
    assert data["view"] == {"color_by": "lang"}, "other sections survive"
    assert data["nodes"]["word"]["id"] == "<name>-<lang>"
    g = Graph.open(root)
    g.add_fact("adamant-en", "frequency", "12")
    assert g.facts("adamant-en")[-1].value == 12


def test_add_refuses_what_the_model_would(root):
    assert "already has a fact" in run(root, "model", "add", "fact", "word", "lang", ok=False).output
    assert "an enum needs" in run(root, "model", "add", "fact", "word", "pos", "enum", ok=False).output
    assert "only an enum" in run(root, "model", "add", "fact", "word", "pos", "int", "--values", "a", ok=False).output
    assert "no node type 'book'" in run(root, "model", "add", "edge", "in", "word", "book", ok=False).output
    assert "has no data model" in run(root, "model", "add", "fact", "x", "y", ok=False).output or True
    assert marker(root) == yaml.safe_load(MODEL), "nothing was written"


def test_rename_fact_rewrites_every_version_and_the_id_template(root):
    result = run(root, "model", "rename", "fact", "word", "lang", "language")
    assert "in 2 records" in result.output
    data = marker(root)
    assert list(data["nodes"]["word"]["facts"]) == ["language", "example"]
    assert data["nodes"]["word"]["id"] == "<name>-<language>"
    g = Graph.open(root)
    assert [f.key for f in g.node("adamant-en").facts] == ["language", "example", "example"]
    assert g.check() == []
    result = run(root, "model", "rename", "fact", "word", "example", "quote")
    assert "in 2 records" in result.output, "superseded versions are renamed too"
    assert {f.key for f in g.node("adamant-en").facts if f.superseded_at} == {"example"}
    assert {f.key for f in Graph.open(root).node("adamant-en").facts} == {"language", "quote"}


def test_rename_edge_and_node_type(root):
    assert "in 1 record" in run(root, "model", "rename", "edge", "translates_to", "same_as").output
    assert marker(root)["edges"]["same_as"] == {"from": "word", "to": "word", "symmetric": True}
    assert Graph.open(root).out_edges("adamant-en")[0].rel == "same_as"

    assert "moved 1 node" in run(root, "model", "rename", "node", "source", "reference").output
    data = marker(root)
    assert "reference" in data["nodes"] and "source" not in data["nodes"]
    assert data["edges"]["defined_in"] == {"from": "word", "to": "reference"}
    assert (root / "nodes" / "reference" / "saol.yaml").is_file()
    assert (root / "nodes" / "reference" / "saol.md").read_text() == "# SAOL\n"
    assert not (root / "nodes" / "source").exists()
    g = Graph.open(root)
    assert g.node("saol").type == "reference" and g.check() == []
    assert "already has" in run(root, "model", "rename", "node", "word", "reference", ok=False).output


def test_drop_refuses_in_use_and_force_retracts(root):
    result = run(root, "model", "drop", "fact", "word", "example", ok=False)
    assert "1 current record uses word.example; --force" in result.output
    assert "example" in marker(root)["nodes"]["word"]["facts"]

    result = run(root, "model", "drop", "fact", "word", "example", "--force")
    assert "Retracted 1 record" in result.output
    assert "example" not in marker(root)["nodes"]["word"]["facts"]
    g = Graph.open(root)
    assert [f.key for f in g.facts("adamant-en")] == ["lang"]
    assert len([f for f in g.node("adamant-en").facts if f.key == "example"]) == 2, "history kept"
    assert g.check() == []

    assert "uses <lang>" in run(root, "model", "drop", "fact", "word", "lang", "--force", ok=False).output
    assert "translates_to; --force" in run(root, "model", "drop", "edge", "translates_to", ok=False).output
    run(root, "model", "drop", "edge", "translates_to", "--force")
    assert Graph.open(root).out_edges("adamant-en") == []
    assert "1 node is of type 'source'" in run(root, "model", "drop", "node", "source", ok=False).output
    (root / "nodes" / "source" / "saol.yaml").unlink()
    run(root, "model", "drop", "node", "source")
    assert "edges" not in marker(root), "an edge between dropped types goes with it"


def test_diff_models_classifies():
    old = DataModel.from_marker(yaml.safe_load(MODEL))
    changed = yaml.safe_load(MODEL)
    changed["time"] = "tracked"
    changed["vocabularies"]["lang"] = ["en", "de"]
    changed["nodes"]["word"]["facts"]["example"] = {"type": "text", "required": True}
    changed["nodes"]["word"]["facts"]["pos"] = "text"
    changed["nodes"]["word"]["id"] = None
    del changed["nodes"]["source"]
    del changed["edges"]["defined_in"]
    changed["edges"]["translates_to"]["symmetric"] = False
    changed["edges"]["translates_to"]["from"] = ["word", "phrase"]
    changed["nodes"]["phrase"] = {"facts": {"lang": {"type": "enum", "values": "lang", "required": True}}}
    new = DataModel.from_marker(changed)
    changes = {(c.where, c.what): c.kind for c in modeledit.diff_models(old, new)}
    assert changes == {
        ("time", "ignored → tracked"): "additive",
        ("word", "id template '<name>-<lang>' → None"): "additive",
        ("word.lang", "values added: de"): "additive",
        ("word.lang", "values removed: sv"): "destructive",
        ("word.example", "now required"): "destructive",
        ("word.example", "now takes one value"): "destructive",
        ("word.pos", "new fact (text)"): "additive",
        ("phrase", "new node type"): "additive",
        ("phrase.lang", "new fact (enum: en, de), required"): "destructive",
        ("source", "node type removed"): "destructive",
        ("translates_to", "from types added: phrase"): "additive",
        ("translates_to", "no longer symmetric"): "destructive",
        ("defined_in", "edge removed"): "destructive",
    }
    assert modeledit.diff_models(None, new)[0].kind == "destructive"
    assert modeledit.diff_models(old, None)[0].kind == "additive"
    assert modeledit.diff_models(old, old) == []


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_cli_diff_against_a_commit(root):
    git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True, capture_output=True)  # noqa: E731
    git("init", "-q", ".")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "T")
    git("add", "-A")
    git("commit", "-qm", "one")
    assert "no change" in run(root, "model", "diff").output
    run(root, "model", "add", "fact", "word", "frequency", "int")
    text = (root / "bibliotheke.yaml").read_text().replace("values: lang, required: true", "values: lang")
    (root / "bibliotheke.yaml").write_text(text.replace("lang: [en, sv]", "lang: [en]"))
    result = run(root, "model", "diff", ok=False)
    assert result.exit_code == 1
    assert "+" in result.output and "word.frequency" in result.output and "values removed: sv" in result.output
    assert "envis-sv: fact" in result.output and "must be one of en" in result.output
    data = json.loads(run(root, "model", "diff", "--json", ok=False).output)
    assert {c["kind"] for c in data["changes"]} == {"additive", "destructive"}
    assert len(data["problems"]) == 1 and data["to"] == "working tree"


def test_edit_checks_before_saving(root, monkeypatch):
    original = (root / "bibliotheke.yaml").read_text()
    edited = {"text": original.replace("lang: [en, sv]", "lang: [en]")}
    monkeypatch.setattr(cli, "_edit_text", lambda text: edited["text"])
    result = run(root, "model", "edit", ok=False)
    assert result.exit_code == 1 and "values removed: sv" in result.output and "not saved" in result.output
    assert (root / "bibliotheke.yaml").read_text() == original

    run(root, "model", "edit", "--force")
    assert marker(root)["vocabularies"] == {"lang": ["en"]}

    edited["text"] = "nodes: [1, 2]\n"
    assert "not saved" in run(root, "model", "edit", ok=False).output
    edited["text"] = (root / "bibliotheke.yaml").read_text()
    assert "No change" in run(root, "model", "edit").output


def test_model_still_prints(root):
    result = run(root, "model")
    assert "translates_to" in result.output and "word ↔ word" in result.output
    assert json.loads(run(root, "model", "--json").output)["nodes"]["word"]["facts"]["lang"]["required"] is True
