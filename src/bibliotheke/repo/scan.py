"""Read a repository's history into nodes and edges.

Nodes are ``dir``, ``file`` and ``author``. A file's facts and edges are
valid from the commit that created it to the one that deleted it, so under
``--valid-at`` the graph holds exactly the files that existed then; a rename
appends a new ``path`` version. Edges are one per commit: ``touched`` from
author to file, and ``co_changed`` between files changed together, so the
count of parallel edges is the weight and ``--valid-at`` shows the coupling
and ownership accumulated up to that time. ``imports`` edges come from the
files' contents at each commit and run from the commit that introduced the
import to the one that dropped it. A commit's author date is valid time and
its committer date is knowledge time: a rebase moves the second but not the
first.
"""

from __future__ import annotations

import subprocess
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from itertools import combinations
from pathlib import Path
from typing import Any

from bibliotheke import timeutil
from bibliotheke.graph import Graph, GraphError, slugify
from bibliotheke.model import Edge, Fact, Node, Record
from bibliotheke.repo import deps

RS, US = "\x1e", "\x1f"  # record and field separators in the log format
FORMAT = f"{RS}%H{US}%aN{US}%aE{US}%aI{US}%cI{US}%s"
MAX_BLOB = 1_000_000  # bytes; bigger source files are generated or vendored

# What the in-memory graph's bibliotheke.yaml says: the shape of the data and
# how to draw it. ``co_changed`` is symmetric so that Graph.nx() mirrors it.
MARKER: dict[str, Any] = {
    "schema_version": 1,
    "description": "A git repository: its files, directories and authors, one edge per commit.",
    "nodes": {
        "dir": {"description": "A directory."},
        "file": {
            "description": "A file, from the commit that created it to the one that deleted it.",
            "facts": {"path": "text", "dir": "text", "ext": "text", "commits": "int"},
        },
        "author": {"description": "A commit author.", "facts": {"email": "text", "commits": "int"}},
    },
    "edges": {
        "contains": {"from": "dir", "to": ["dir", "file"], "description": "Directory tree."},
        "touched": {"from": "author", "to": "file", "description": "One per commit by the author to the file."},
        "co_changed": {
            "from": "file",
            "to": "file",
            "symmetric": True,
            "description": "One per commit that changed both files.",
        },
        "imports": {"from": "file", "to": "file", "description": "The file imports, includes or declares the other."},
    },
    # How it is drawn: directories and the `contains` edges are the
    # structure, so they are quiet greys; files are the content, coloured by
    # top-level directory (presets.py picks the hues) and sized by how many
    # files import them, which is also what earns a label. Five lenses, each
    # answering a different question; the first is the default.
    "view": {
        "theme": "plotui",
        "color_by": "dir",
        "size_by": "in:imports",
        "labels": {"count": 6, "types": ["file"]},
        "valid_at": "now",  # deleted files gone, a moved file under its current path only
        "edges": {"contains": "#4a4d56", "imports": "#8e92a2", "touched": "#6e608c", "co_changed": "#5a5c68"},
        "lenses": [
            {
                "name": "Structure",
                "description": "the directory tree, and what imports what across it",
                "rels": ["contains", "imports"],
                "tree": "contains",
            },
            {
                "name": "Map",
                "description": "the same, as a force layout: clusters find their own place",
                "rels": ["contains", "imports"],
            },
            {
                "name": "Dependencies",
                "description": "files alone, held together only by what imports what",
                "rels": ["imports"],
            },
            {
                "name": "Coupling",
                "description": "files that keep changing in the same commits, however far apart they live",
                "rels": ["co_changed"],
                "min_parallel": 3,  # commits two files must have changed in together to count
            },
            {"name": "People", "description": "who has touched which files", "rels": ["touched"]},
        ],
    },
}


@dataclass
class Scan:
    """The result: the graph plus what was read to build it."""

    graph: Graph
    commits: int
    wide_commits: int = 0  # too many files for co-change edges
    parsed: int = 0  # source files read for imports
    first: str | None = None
    last: str | None = None


def scan(
    path: Path | str = ".",
    *,
    rev: str = "HEAD",
    since: str | None = None,
    max_commits: int | None = None,
    max_coupled: int = 50,
    imports: bool = True,
    now: Any = None,
) -> Scan:
    """The repository at ``path`` as a graph. ``since`` is anything git
    accepts (``2.years``, ``2024-01-01``); ``max_coupled`` caps the files a
    commit may touch and still add ``co_changed`` edges, which keeps mass
    reformats from coupling everything to everything; ``imports=False``
    skips reading file contents."""
    path = Path(path)
    stamp = timeutil.canonical(now) or timeutil.now()
    commits = _log(path, rev, since, max_commits)
    with _Blobs(path) as blobs:
        builder = _Builder(stamp, max_coupled, blobs if imports else None)
        for commit in commits:
            builder.add(commit)
    return builder.finish(path.resolve())


@dataclass
class Change:
    status: str  # A, M, D, R, C, T
    path: str
    new_path: str | None  # for R and C
    blob: str | None  # the content after the change; None when deleted


@dataclass
class Commit:
    sha: str
    author: str
    email: str
    authored: str  # valid time
    committed: str  # knowledge time
    subject: str
    changes: list[Change] = field(default_factory=list)


def _log(path: Path, rev: str, since: str | None, max_commits: int | None) -> list[Commit]:
    if not path.is_dir():
        raise GraphError(f"{path} is not a directory")
    args = ["git", "log", rev, f"--format={FORMAT}", "--raw", "--no-abbrev", "-M", "--no-merges", "--reverse", "--date-order"]
    if since:
        args.append(f"--since={since}")
    if max_commits:
        args.append(f"--max-count={max_commits}")
    done = subprocess.run(args, cwd=path, capture_output=True, check=False)
    if done.returncode:
        raise GraphError(done.stderr.decode(errors="replace").strip() or f"git log failed in {path}")
    commits = []
    for chunk in done.stdout.decode(errors="replace").split(RS)[1:]:
        header, _, body = chunk.partition("\n")
        sha, author, email, authored, committed, subject = header.split(US, 5)
        commit = Commit(sha, author, email, timeutil.canonical(authored), timeutil.canonical(committed), subject)
        for line in body.splitlines():
            if not line.startswith(":"):
                continue
            meta, *paths = line.split("\t")
            fields = meta[1:].split()  # old mode, new mode, old blob, new blob, status
            if len(fields) < 5 or not paths:
                continue
            status, blob = fields[4][0], fields[3]
            blob = None if status == "D" or set(blob) == {"0"} else blob
            if status in ("R", "C") and len(paths) == 2:
                commit.changes.append(Change(status, paths[0], paths[1], blob))
            else:
                commit.changes.append(Change(status, paths[0], None, blob))
        commits.append(commit)
    return commits


class _Blobs:
    """File contents by blob hash from one long-lived ``git cat-file --batch``."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> _Blobs:
        self.proc = subprocess.Popen(
            ["git", "cat-file", "--batch"], cwd=self.path, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        return self

    def __exit__(self, *exc: Any) -> None:
        if self.proc is not None:
            self.proc.stdin.close()  # type: ignore[union-attr]
            self.proc.wait()

    def text(self, sha: str) -> str | None:
        """The blob as text, or None when missing or too big to be source."""
        assert self.proc and self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(f"{sha}\n".encode())
        self.proc.stdin.flush()
        header = self.proc.stdout.readline().split()
        if len(header) < 3:
            return None
        size = int(header[2])
        data = self.proc.stdout.read(size + 1)[:-1]
        return data.decode(errors="replace") if size <= MAX_BLOB else None


def _before(stamp: str) -> str:
    """The second before ``stamp``: a record that ends here is gone at ``stamp``."""
    return timeutil.canonical(timeutil.start(stamp) - timedelta(seconds=1))


class _Builder:
    def __init__(self, stamp: str, max_coupled: int, blobs: _Blobs | None) -> None:
        self.stamp = stamp
        self.max_coupled = max_coupled
        self.blobs = blobs
        self.nodes: dict[str, Node] = {}
        self.files: dict[str, Node] = {}  # current path -> node
        self.dirs: dict[str, str] = {}  # directory path -> node id
        self.authors: dict[str, str] = {}  # email -> node id
        self.incoming: dict[str, list[Edge]] = {}  # node id -> edges that point to it
        self.index = deps.Index()  # the paths that exist now, for resolving imports
        self.importing: dict[str, dict[str, Edge]] = {}  # importer id -> target id -> open edge
        self.ids: Counter[str] = Counter()
        self.seq = 0
        self.touches: Counter[str] = Counter()  # node id -> commits
        self.live: Counter[str] = Counter()  # directory path -> files and directories in it right now
        self.born: dict[str, str] = {}  # node id -> creation time
        self.gone: dict[str, str] = {}  # file node id -> last second it existed
        self.result = Scan(graph=None, commits=0)  # type: ignore[arg-type]

    # -- records -------------------------------------------------------------

    def _id(self, text: str) -> str:
        base = slugify(text) or "x"
        self.ids[base] += 1
        return base if self.ids[base] == 1 else f"{base}-{self.ids[base]}"

    def _rid(self, prefix: str) -> str:
        self.seq += 1
        return f"{prefix}{self.seq:x}"

    def _fact(self, node: Node, key: str, value: Any, commit: Commit) -> Fact:
        fact = Fact(id=self._rid("f"), key=key, value=value, valid_from=commit.authored, known_at=commit.committed, changed_at=self.stamp)
        node.facts.append(fact)
        return fact

    def _edge(self, src: Node, rel: str, dst: Node, commit: Commit) -> Edge:
        edge = Edge(id=self._rid("e"), rel=rel, to=dst.id, valid_from=commit.authored, known_at=commit.committed, changed_at=self.stamp)
        src.edges.append(edge)
        self.incoming.setdefault(dst.id, []).append(edge)
        return edge

    @staticmethod
    def _close(records: list[Record], end: str, rel: str | None = None) -> None:
        for record in records:
            if record.valid_to is None and (rel is None or getattr(record, "rel", None) == rel):
                record.valid_to = end

    # -- nodes ---------------------------------------------------------------

    def _author(self, commit: Commit) -> Node:
        if commit.email in self.authors:
            return self.nodes[self.authors[commit.email]]
        node = Node(id=self._id(commit.author), type="author", name=commit.author)
        self.nodes[node.id] = node
        self.authors[commit.email] = node.id
        self.born[node.id] = commit.authored  # an author exists from their first commit
        self._fact(node, "email", commit.email, commit)
        return node

    def _dir(self, path: str, commit: Commit) -> Node | None:
        """The directory node for ``path`` ('' is the root), created with its
        parents on first sight."""
        if path == "":
            return None
        if path in self.dirs:
            return self.nodes[self.dirs[path]]
        node = Node(id=self._id(path), type="dir", name=path + "/")
        self.nodes[node.id] = node
        self.dirs[path] = node.id
        folder = path.rpartition("/")[0]
        parent = self._dir(folder, commit)
        if parent is not None:
            self._edge(parent, "contains", node, commit)
            self._gain(folder, commit)
        return node

    def _gain(self, folder: str, commit: Commit) -> None:
        """A directory has one more thing in it. An emptied directory is
        linked to its parent again, and so on up."""
        if folder == "":
            return
        self.live[folder] += 1
        if self.live[folder] == 1:
            node = self.nodes[self.dirs[folder]]
            if not any(e.rel == "contains" and e.valid_to is None for e in self.incoming.get(node.id, [])):
                above = folder.rpartition("/")[0]
                parent = self._dir(above, commit)
                if parent is not None:
                    self._edge(parent, "contains", node, commit)
                    self._gain(above, commit)

    def _lose(self, folder: str, commit: Commit) -> None:
        """A directory has one thing fewer. An empty directory is no longer
        part of its parent (the edge ends), and so on up."""
        if folder == "":
            return
        self.live[folder] -= 1
        if self.live[folder] <= 0:
            self.live[folder] = 0
            node = self.nodes[self.dirs[folder]]
            self._close(self.incoming.get(node.id, []), _before(commit.authored), rel="contains")
            self._lose(folder.rpartition("/")[0], commit)

    def _create(self, path: str, commit: Commit) -> Node:
        node = Node(id=self._id(path), type="file", name=path)
        self.nodes[node.id] = node
        self.born[node.id] = commit.authored
        self._place(node, path, commit)
        return node

    def _place(self, node: Node, path: str, commit: Commit) -> None:
        """A new ``path`` version (creation or rename) with its ``dir`` and
        ``ext`` facts and the containing edge; the previous ones end here."""
        end = _before(commit.authored)
        self._close(node.facts, end)
        self._close(self.incoming.get(node.id, []), end, rel="contains")
        if node.name in self.files and self.files[node.name] is node:
            del self.files[node.name]
            self.index.remove(node.name)
            self._lose(node.name.rpartition("/")[0], commit)
        node.name = path
        self.files[path] = node
        self.index.add(path)
        folder, _, filename = path.rpartition("/")
        self._fact(node, "path", path, commit)
        self._fact(node, "dir", path.split("/", 1)[0] if "/" in path else "/", commit)
        if ext := deps.extension(path):
            self._fact(node, "ext", ext, commit)
        parent = self._dir(folder, commit)
        if parent is not None:
            self._edge(parent, "contains", node, commit)
            self._gain(folder, commit)

    def _delete(self, path: str, commit: Commit) -> None:
        """The file and everything about it end the second before the commit."""
        node = self.files.pop(path, None)
        if node is None:
            return
        self.index.remove(path)
        self.importing.pop(node.id, None)
        end = _before(commit.authored)
        self.gone[node.id] = end
        self._close(node.facts, end)
        self._close(node.edges, end)
        self._close(self.incoming.get(node.id, []), end)
        self._lose(path.rpartition("/")[0], commit)

    # -- imports -------------------------------------------------------------

    def _imports(self, node: Node, blob: str, commit: Commit) -> None:
        """Open edges for imports the file gained, close those it lost."""
        if self.blobs is None or deps.extension(node.name) not in deps.LANGUAGES:
            return
        text = self.blobs.text(blob)
        if text is None:
            return
        self.result.parsed += 1
        targets: set[str] = set()
        for candidates in deps.extract(node.name, text):
            if (path := self.index.resolve(node.name, candidates)) and path in self.files:
                targets.add(self.files[path].id)
        open_edges = self.importing.setdefault(node.id, {})
        end = _before(commit.authored)
        for target_id in list(open_edges):
            if target_id not in targets or open_edges[target_id].valid_to is not None:
                edge = open_edges.pop(target_id)
                if edge.valid_to is None:
                    edge.valid_to = end
        for target_id in sorted(targets - set(open_edges)):
            open_edges[target_id] = self._edge(node, "imports", self.nodes[target_id], commit)

    # -- commits -------------------------------------------------------------

    def add(self, commit: Commit) -> None:
        self.result.commits += 1
        self.result.first = self.result.first or commit.authored
        self.result.last = commit.authored
        author = self._author(commit)
        touched: list[tuple[Node, str | None]] = []
        # First the tree: creations, renames and deletions, so that imports
        # in this commit resolve against the paths as of this commit.
        for change in commit.changes:
            if change.status == "D":
                node = self.files.get(change.path)
                if node is not None:
                    self.touches[node.id] += 1
                self._delete(change.path, commit)
                continue
            if change.status == "R" and change.new_path:
                node = self.files.get(change.path)
                if node is None:
                    node = self.files.get(change.new_path) or self._create(change.new_path, commit)
                elif node.name != change.new_path:
                    self._place(node, change.new_path, commit)
            else:
                target = change.new_path or change.path  # a copy (C) is a new file at its new path
                node = self.files.get(target) or self._create(target, commit)
            touched.append((node, change.blob))
        self.touches[author.id] += 1
        for node, blob in touched:
            self.touches[node.id] += 1
            self._edge(author, "touched", node, commit)
            if blob:
                self._imports(node, blob, commit)
        if len(touched) > self.max_coupled:
            self.result.wide_commits += 1
            return
        for (a, _), (b, _) in combinations(sorted(touched, key=lambda pair: pair[0].id), 2):
            self._edge(a, "co_changed", b, commit)

    def finish(self, root: Path) -> Scan:
        for node in self.nodes.values():
            if node.type in ("file", "author"):
                node.facts.append(
                    Fact(
                        id=self._rid("f"),
                        key="commits",
                        value=self.touches[node.id],
                        valid_from=self.born.get(node.id),
                        valid_to=self.gone.get(node.id),
                        known_at=self.stamp,
                        changed_at=self.stamp,
                    )
                )
        self.result.graph = Graph.from_nodes(self.nodes.values(), marker=MARKER, root=root)
        return self.result
