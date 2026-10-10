"""The data model under git: a pre-commit hook that gates commits on
``bib check``, a CI workflow that reviews model changes on pull requests,
the model's history commit by commit, and its version, tagged in git.

Versions follow the diff classification in ``modeledit``: a destructive
change bumps the major number, an additive one the minor, a neutral one the
patch. The version lives in ``bibliotheke.yaml`` as ``version:`` and the tag
is ``model-v<version>``."""

from __future__ import annotations

import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from bibliotheke import modeledit, store
from bibliotheke.datamodel import DataModel
from bibliotheke.modeledit import ADDITIVE, DESTRUCTIVE, NEUTRAL, Change

HOOK_MARK = "# installed by `bib hook install`"
TAG_PREFIX = "model-v"
VERSION_KEY = "version"
_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

HOOK = f"""#!/bin/sh
{HOOK_MARK}; `bib hook remove` deletes it.
# Checks the graph against its data model before every commit, then shows
# what a model change does. A destructive model change is refused unless
# BIB_ALLOW_DESTRUCTIVE is set; `git commit --no-verify` skips the hook.
cd "$(git rev-parse --show-toplevel)/__PREFIX__" || exit 1
if command -v bib >/dev/null 2>&1; then BIB=bib; else BIB="uv run --quiet bib"; fi
$BIB check || exit 1
git rev-parse -q --verify HEAD >/dev/null 2>&1 || exit 0
if [ -n "$BIB_ALLOW_DESTRUCTIVE" ]; then exec $BIB model diff HEAD; fi
exec $BIB model diff HEAD --no-destructive
"""

WORKFLOW = """# Written by `bib ci`. On every pull request: check the graph against its
# data model, show what the model change does, and post the report on the PR.
name: bibliotheke
on:
  pull_request:
  push:
    branches: [main]
permissions:
  contents: read
  pull-requests: write
jobs:
  check:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: __DIR__
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: __INSTALL__
      - name: Check the graph and the model change
        id: check
        run: |
          since="${{ github.event.pull_request.base.sha || github.event.before }}"
          git cat-file -e "$since" 2>/dev/null || since=""
          set +e
          bib check --markdown > report.md
          status=$?
          if [ -n "$since" ]; then bib model diff "$since" --markdown >> report.md || status=1; fi
          echo "status=$status" >> "$GITHUB_OUTPUT"
          cat report.md >> "$GITHUB_STEP_SUMMARY"
      - name: Comment on the pull request
        if: github.event_name == 'pull_request'
        env:
          GH_TOKEN: ${{ github.token }}
        run: gh pr comment "${{ github.event.pull_request.number }}" --body-file report.md --edit-last || gh pr comment "${{ github.event.pull_request.number }}" --body-file report.md
      - run: exit "${{ steps.check.outputs.status }}"
"""


def git(root: Path, *args: str) -> str | None:
    """A git command's output, or None when it fails."""
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    return done.stdout.strip() if done.returncode == 0 else None


def repo(root: Path) -> tuple[Path, str]:
    """The repository's top level and the graph's path inside it (with a
    trailing slash, or empty)."""
    found = git(root, "rev-parse", "--show-toplevel", "--show-prefix")
    if found is None:
        raise ValueError(f"{root} is not in a git repository")
    lines = found.split("\n")
    return Path(lines[0]), lines[1] if len(lines) > 1 else ""


# -- hook ------------------------------------------------------------------------


def hook_path(root: Path) -> Path:
    repo(root)
    found = git(root, "rev-parse", "--git-path", "hooks/pre-commit")
    assert found is not None
    path = Path(found)
    return (path if path.is_absolute() else root / path).resolve()


def install_hook(root: Path) -> Path:
    """Write the pre-commit hook; refuses to replace one that is not ours."""
    _, prefix = repo(root)
    path = hook_path(root)
    if path.exists() and HOOK_MARK not in path.read_text():
        raise ValueError(f"{path} exists and was not written by bib; merge it by hand")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HOOK.replace("__PREFIX__", prefix))
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def remove_hook(root: Path) -> Path | None:
    path = hook_path(root)
    if not path.exists():
        return None
    if HOOK_MARK not in path.read_text():
        raise ValueError(f"{path} was not written by bib; leaving it")
    path.unlink()
    return path


# -- CI --------------------------------------------------------------------------


def workflow(root: Path, install: str = "pip install bibliotheke") -> str:
    """A GitHub Actions workflow for the repository that holds the graph."""
    _, prefix = repo(root)
    return WORKFLOW.replace("__DIR__", prefix.rstrip("/") or ".").replace("__INSTALL__", install)


# -- history ---------------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    sha: str
    date: str
    subject: str
    changes: list[Change]

    def to_dict(self) -> dict:
        return {"commit": self.sha, "date": self.date, "subject": self.subject, "changes": [c.to_dict() for c in self.changes]}


def model_at(root: Path, rev: str | None) -> DataModel | None:
    """The model as committed at ``rev``; None for no revision or no model."""
    if rev is None:
        return None
    marker, _ = store.load_model(store.GitTree(root, rev))
    return DataModel.from_marker(marker)


def model_log(root: Path, max_count: int = 20) -> list[Entry]:
    """Commits that changed the model, newest first, each with its diff."""
    repo(root)
    listing = git(root, "log", f"--max-count={max_count}", "--format=%H%x09%ad%x09%s", "--date=short", "--", store.ROOT_MARKER, store.MODEL_DIR)
    entries = []
    for line in (listing or "").splitlines():
        sha, date, subject = line.split("\t", 2)
        parent = git(root, "rev-parse", "--verify", "--quiet", f"{sha}^")
        changes = modeledit.diff_models(model_at(root, parent), model_at(root, sha))
        if changes:
            entries.append(Entry(sha, date, subject, changes))
    return entries


def summary(changes: list[Change]) -> str:
    """One line: counts by kind, then what changed."""
    counts = {kind: sum(1 for c in changes if c.kind == kind) for kind in (DESTRUCTIVE, ADDITIVE, NEUTRAL)}
    marks = {DESTRUCTIVE: "-", ADDITIVE: "+", NEUTRAL: "~"}
    parts = [f"{marks[kind]}{n}" for kind, n in counts.items() if n]
    where = []
    for change in changes:
        if change.where not in where:
            where.append(change.where)
    shown = ", ".join(where[:6]) + (f" and {len(where) - 6} more" if len(where) > 6 else "")
    return f"{' '.join(parts)}: {shown}"


# -- version ---------------------------------------------------------------------


def last_tag(root: Path) -> str | None:
    """The newest ``model-v*`` tag reachable from HEAD."""
    return git(root, "describe", "--tags", "--match", f"{TAG_PREFIX}*", "--abbrev=0")


def next_version(root: Path, marker: dict) -> tuple[str, list[Change], str | None]:
    """The version the working-tree model should get, the changes since the
    last tag that justify it, and that tag. Without a tag, the version is
    the one in the marker, or 1.0.0."""
    tag = last_tag(root)
    if tag is None:
        return str(marker.get(VERSION_KEY) or "1.0.0"), [], None
    changes = modeledit.diff_models(model_at(root, tag), DataModel.from_marker(marker))
    if not changes:
        raise ValueError(f"no model change since {tag}")
    match = _VERSION.match(tag.removeprefix(TAG_PREFIX))
    if match is None:
        raise ValueError(f"tag {tag} is not {TAG_PREFIX}<major>.<minor>.<patch>")
    major, minor, patch = (int(n) for n in match.groups())
    kinds = {c.kind for c in changes}
    if DESTRUCTIVE in kinds:
        version = f"{major + 1}.0.0"
    elif ADDITIVE in kinds:
        version = f"{major}.{minor + 1}.0"
    else:
        version = f"{major}.{minor}.{patch + 1}"
    return version, changes, tag


def tag_version(root: Path, marker: dict) -> str:
    """Tag HEAD with the marker's version. The model must be committed."""
    repo(root)
    version = marker.get(VERSION_KEY)
    if not version:
        raise ValueError("the model has no version; `bib model version --bump` sets one")
    if git(root, "status", "--porcelain", "--", store.ROOT_MARKER, store.MODEL_DIR):
        raise ValueError("the model has uncommitted changes; commit them first")
    name = f"{TAG_PREFIX}{version}"
    if git(root, "rev-parse", "--verify", "--quiet", f"refs/tags/{name}") is not None:
        raise ValueError(f"tag {name} exists")
    if git(root, "tag", "-a", name, "-m", f"Data model {version}") is None:
        raise ValueError(f"could not create tag {name}")
    return name
