"""The data model under git: hook, CI workflow, history, versions."""

import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest
import yaml
from typer.testing import CliRunner

from bibliotheke import Graph, GraphError, gitops
from bibliotheke.cli import app

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
runner = CliRunner()

MODEL = """\
schema_version: 1
nodes:
  word:
    facts:
      lang: {type: enum, values: [en, sv], required: true}
  source: {}
edges:
  defined_in: {from: word, to: source}
"""


def run(root, *args, ok=True):
    result = runner.invoke(app, [*args, "--root", str(root)])
    if isinstance(result.exception, GraphError):
        result = SimpleNamespace(exit_code=1, output=result.output + f"error: {result.exception}")
    elif result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    if ok:
        assert result.exit_code == 0, result.output
    return result


@pytest.fixture
def repo(tmp_path):
    """A repository with the graph in kg/, one commit."""
    git(tmp_path, "init", "-q", "-b", "main", ".")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "T")
    kg = tmp_path / "kg"
    (kg / "nodes").mkdir(parents=True)
    (kg / "bibliotheke.yaml").write_text(MODEL)
    g = Graph.open(kg)
    g.add_node("word", "adamant", id="adamant-en")
    g.add_fact("adamant-en", "lang", "en")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "one")
    return kg


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def test_check_is_about_the_data_and_model_diff_about_the_model(repo):
    run(repo, "model", "add", "fact", "word", "frequency", "int")
    result = run(repo, "check")
    assert "OK: 1 nodes" in result.output and "word.frequency" not in result.output, "check says nothing about history"
    assert "OK: 1 nodes." in run(repo, "check", "--markdown").output
    result = run(repo, "model", "diff", "HEAD")
    assert "word.frequency" in result.output
    report = run(repo, "model", "diff", "HEAD", "--markdown").output
    assert "### Model changes, HEAD → working tree" in report and "| `+` | `word.frequency` | new fact (int) | additive |" in report

    run(repo, "model", "drop", "edge", "defined_in")
    assert run(repo, "check").exit_code == 0
    assert run(repo, "model", "diff", "HEAD").exit_code == 0, "destructive but nothing breaks"
    result = run(repo, "model", "diff", "HEAD", "--no-destructive", ok=False)
    assert result.exit_code == 1 and "destructive model change" in result.output
    report = run(repo, "model", "diff", "HEAD", "--markdown").output
    assert "| `-` | `defined_in` | edge removed | destructive |" in report


def test_diff_markdown(repo):
    run(repo, "model", "add", "node", "phrase")
    report = run(repo, "model", "diff", "--markdown").output
    assert report.startswith("### Model changes, HEAD → working tree") and "| `+` | `phrase` | new node type | additive |" in report


def test_hook_install_and_run(repo):
    result = run(repo, "hook", "install")
    path = repo.parent / ".git" / "hooks" / "pre-commit"
    assert str(path) in result.output and path.is_file() and os.access(path, os.X_OK)
    text = path.read_text()
    assert gitops.HOOK_MARK in text and '/kg/"' in text
    assert "$BIB check || exit 1" in text and "model diff HEAD --no-destructive" in text
    run(repo, "hook", "install"), "reinstalling is fine"
    assert "Removed" in run(repo, "hook", "remove").output and not path.exists()
    assert "No hook" in run(repo, "hook", "remove").output
    path.write_text("#!/bin/sh\nexit 0\n")
    assert "not written by bib" in run(repo, "hook", "install", ok=False).output
    assert "not written by bib" in run(repo, "hook", "remove", ok=False).output


def test_ci_workflow(repo):
    text = run(repo, "ci", "--install", "pip install git+https://example.com/bib.git").output
    data = yaml.safe_load(text)
    assert data["jobs"]["check"]["defaults"]["run"]["working-directory"] == "kg"
    assert {"run": "pip install git+https://example.com/bib.git"} in data["jobs"]["check"]["steps"]
    assert "bib check --markdown > report.md" in text and 'bib model diff "$since" --markdown >> report.md' in text
    assert data["permissions"] == {"contents": "read", "pull-requests": "write"}
    top = run(repo.parent / "kg", "ci").output  # default install line
    assert "pip install bibliotheke" in top


def test_model_log_lists_model_changes_with_their_meaning(repo):
    assert "No commits changed the model" not in run(repo, "model", "log").output  # the first commit added a model
    run(repo, "model", "add", "fact", "word", "frequency", "int")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "frequency")
    (repo / "bibliotheke.yaml").write_text((repo / "bibliotheke.yaml").read_text() + "view: {theme: map}\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "view only")
    run(repo, "model", "drop", "edge", "defined_in")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "drop edge")
    result = run(repo, "model", "log")
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert lines[-1].startswith("+ additive"), "a legend closes the table"
    assert [line.split()[2] for line in lines[1:-1]] == ["drop", "frequency", "one"], "view-only commit is left out"
    assert "-1 defined_in" in lines[1] and "+1 word.frequency" in lines[2] and "a model was added" in run(repo, "model", "log", "--json").output
    entries = yaml.safe_load(run(repo, "model", "log", "--json", "--max", "2").output)
    assert [e["subject"] for e in entries] == ["drop edge"] , "--max counts commits touching the model files, before filtering"


def test_version_bump_follows_the_diff(repo):
    assert "no version" in run(repo, "model", "version").output
    assert "the model has no version" in run(repo, "model", "tag", ok=False).output
    assert "Version 1.0.0" in run(repo, "model", "version", "--bump").output
    assert yaml.safe_load((repo / "bibliotheke.yaml").read_text())["version"] == "1.0.0"
    assert "uncommitted" in run(repo, "model", "tag", ok=False).output
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "v1")
    assert "Tagged HEAD as model-v1.0.0" in run(repo, "model", "tag").output
    assert "tag model-v1.0.0 exists" in run(repo, "model", "tag", ok=False).output
    assert "no model change since model-v1.0.0" in run(repo, "model", "version", "--bump", ok=False).output

    run(repo, "model", "add", "fact", "word", "frequency", "int")
    assert "Version 1.1.0 (was 1.0.0)" in run(repo, "model", "version", "--bump").output
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "v1.1")
    run(repo, "model", "tag")
    run(repo, "model", "drop", "edge", "defined_in")
    assert "Version 2.0.0" in run(repo, "model", "version", "--bump").output
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "v2")
    run(repo, "model", "tag")
    assert git(repo, "tag", "--list", "model-v*").split() == ["model-v1.0.0", "model-v1.1.0", "model-v2.0.0"]
    assert "Version 2.0.0" in run(repo, "model").output
