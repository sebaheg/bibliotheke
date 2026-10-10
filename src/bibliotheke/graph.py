"""The graph API: writes that never delete, and reads filtered on three time axes."""

from __future__ import annotations

import re
import secrets
import unicodedata
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

import networkx

from bibliotheke import store, timeutil
from bibliotheke.datamodel import DataModel, ModelError
from bibliotheke.model import Edge, Fact, Node, Record


class GraphError(Exception):
    pass


@dataclass(frozen=True)
class At:
    """A point of view on the graph. Each axis is optional.

    - ``valid_at``: only what was true in the world at that time.
    - ``known_at``: only what had been learned by then.
    - ``changed_at``: the graph as it was recorded at that time; without it,
      only the current version of each record.
    """

    valid_at: str | None = None
    known_at: str | None = None
    changed_at: str | None = None

    def __post_init__(self) -> None:
        for name in ("valid_at", "known_at", "changed_at"):
            object.__setattr__(self, name, timeutil.canonical(getattr(self, name)))

    @property
    def filtered(self) -> bool:
        return bool(self.valid_at or self.known_at or self.changed_at)

    def sees(self, record: Record) -> bool:
        if self.changed_at is None:
            if record.superseded_at is not None:
                return False
        else:
            cutoff = timeutil.end(self.changed_at)
            if timeutil.start(record.changed_at) >= cutoff:
                return False
            if record.superseded_at and timeutil.start(record.superseded_at) < cutoff:
                return False
        if self.known_at and timeutil.start(record.known_at) >= timeutil.end(self.known_at):
            return False
        if self.valid_at:
            when = timeutil.start(self.valid_at)
            if record.valid_from and timeutil.start(record.valid_from) > when:
                return False
            # valid_to is inclusive of the period it names: "2023" lasts through 2023.
            if record.valid_to and timeutil.end(record.valid_to) <= when:
                return False
        return True


NOW = At()


class Graph:
    """The graph in memory. ``nodes`` is the public structure, one ``Node``
    per file. Behind it a NetworkX multigraph indexes every edge version
    (superseded ones included) so that reads against any point in time are
    one adjacency lookup rather than a scan of every node."""

    def __init__(self, root: Path, source: store.Source | None = None) -> None:
        source = source or store.WorkingTree(root)
        marker, layout = store.load_model(source)
        self._setup(root, source, marker, store.load_nodes(source), layout)

    @classmethod
    def from_nodes(
        cls,
        nodes: Iterable[Node],
        *,
        marker: dict[str, Any] | None = None,
        texts: dict[str, str] | None = None,
        root: Path | str = ".",
    ) -> Graph:
        """A read-only graph that lives in memory, e.g. one built from a git
        log. ``marker`` is what ``bibliotheke.yaml`` would hold (a data model,
        view settings); ``texts`` holds bodies at ``nodes/<type>/<id>.md``;
        ``root`` names the graph (its last part is shown as the title)."""
        graph = cls.__new__(cls)
        source = store.Memory(root=Path(root), texts=dict(texts or {}))
        marker = marker or {}
        graph._setup(source.root, source, marker, {node.id: node for node in nodes}, store.FILE if marker.get("nodes") is not None else None)
        return graph

    def _setup(self, root: Path, source: store.Source, marker: dict[str, Any], nodes: dict[str, Node], layout: str | None) -> None:
        self.root = root
        self.source = source
        self.marker = marker  # bibliotheke.yaml with the model folded in: model, view and globe settings
        self.layout = layout  # where the model lives: store.FOLDER (model/), store.FILE (bibliotheke.yaml) or None
        self.model: DataModel | None = DataModel.from_marker(marker)
        self.nodes: dict[str, Node] = {}
        self._g = networkx.MultiDiGraph()
        self._versions: dict[str, list[tuple[Node, Record]]] = {}
        self._pos: dict[str, int] = {}  # node id -> load order, so in_edges keep file order
        self._seq = 0  # keeps the keys of two versions of one edge apart
        for node in nodes.values():
            self._put_node(node)
        for node in nodes.values():
            for fact in node.facts:
                self._index_fact(node, fact)
            for edge in node.edges:
                self._index_edge(node, edge)

    def _put_node(self, node: Node) -> None:
        self.nodes[node.id] = node
        self._g.add_node(node.id, node=node)
        self._pos[node.id] = len(self._pos)

    def _put_fact(self, node: Node, fact: Fact) -> None:
        node.facts.append(fact)
        self._index_fact(node, fact)

    def _put_edge(self, node: Node, edge: Edge) -> None:
        node.edges.append(edge)
        self._index_edge(node, edge)

    def _index_fact(self, node: Node, fact: Fact) -> None:
        self._versions.setdefault(fact.id, []).append((node, fact))

    def _index_edge(self, node: Node, edge: Edge) -> None:
        # The attribute is the very Edge object in node.edges: when a write
        # stamps superseded_at on it, the index sees the change at once.
        self._seq += 1
        self._g.add_edge(node.id, edge.to, key=(edge.id, self._seq), edge=edge)
        self._versions.setdefault(edge.id, []).append((node, edge))

    @classmethod
    def open(cls, root: Path | str | None = None, *, at_commit: str | None = None) -> Graph:
        """The graph at ``root`` (default: the nearest graph at or above the
        working directory). With ``at_commit``, the graph as committed at that
        git revision, read-only, without checking it out."""
        if root is not None:
            path = Path(root)
            if not (path / store.ROOT_MARKER).is_file():
                raise GraphError(f"no graph at {path} (missing {store.ROOT_MARKER}); run `bib init`")
        else:
            found = store.find_root()
            if found is None:
                raise GraphError(f"no {store.ROOT_MARKER} here or in any parent; run `bib init`")
            path = found
        try:
            return cls(path, store.GitTree(path, at_commit) if at_commit else None)
        except ValueError as err:
            raise GraphError(str(err)) from err

    def save_as(self, root: Path | str) -> Graph:
        """Write this graph as files at ``root`` (a new or empty directory)
        and return the file-backed graph. For graphs that live in memory."""
        root = Path(root)
        if root.exists() and any(root.iterdir()):
            raise GraphError(f"{root} is not empty")
        store.init_root(root)
        store.save_model(root, self.marker, self.layout or store.FILE)
        for node in self.nodes.values():
            store.save_node(root, node)
            if self.has_body(node):
                path = store.body_path(root, node)
                path.write_text(self.body(node.id))
        return Graph(root)

    def _mutable(self) -> None:
        if not self.source.writable:
            raise GraphError(f"the graph at {self.source.rev!r} is read-only; drop --at-commit to write")

    @classmethod
    def init(cls, root: Path | str) -> Graph:
        return cls(store.init_root(Path(root)))

    # -- writes ------------------------------------------------------------

    def add_node(self, type: str, name: str, id: str | None = None, notes: str = "") -> Node:
        self._mutable()
        type = slugify(type)
        if self.model and type:
            with _modelled():
                spec = self.model.node_spec(type)
            if id is None and spec.id:
                id = self.model.render_id(type, name, lambda key: None)
                if id is None:
                    raise GraphError(f"a {type} id follows {spec.id!r}; pass it with --id")
        node_id = slugify(id or name)
        if not node_id or not type:
            raise GraphError("a node needs a non-empty id and type")
        if node_id in self.nodes:
            raise GraphError(f"node {node_id!r} already exists")
        node = Node(id=node_id, type=type, name=name, notes=notes)
        self._put_node(node)
        store.save_node(self.root, node)
        return node

    def add_fact(
        self,
        node_id: str,
        key: str,
        value: Any,
        *,
        valid_from: Any = None,
        valid_to: Any = None,
        known_at: Any = None,
        now: Any = None,
    ) -> Fact:
        self._mutable()
        node = self.node(node_id)
        fact = Fact(id=self._new_id("f"), key=key, value=value, **_stamps(valid_from, valid_to, known_at, now))
        if self.model:
            with _modelled():
                self.model.times(valid_from, valid_to)
                fact.value = self.model.fact_value(node.type, key, value)
            self._no_clash(node, fact, [f for f in node.facts if f.superseded_at is None])
        self._put_fact(node, fact)
        store.save_node(self.root, node)
        return fact

    def add_edge(
        self,
        src: str,
        rel: str,
        dst: str,
        *,
        valid_from: Any = None,
        valid_to: Any = None,
        known_at: Any = None,
        now: Any = None,
    ) -> Edge:
        self._mutable()
        node = self.node(src)
        target = self.node(dst)
        if self.model:
            with _modelled():
                self.model.times(valid_from, valid_to)
                spec = self.model.edge_spec(rel, node.type, target.type)
            if spec.symmetric and any(e.rel == rel and e.to == node.id and e.superseded_at is None for e in target.edges):
                raise GraphError(f"{target.id} {rel} {node.id} exists already, and {rel} is symmetric")
        edge = Edge(id=self._new_id("e"), rel=rel, to=target.id, **_stamps(valid_from, valid_to, known_at, now))
        if self.model:
            self._no_edge_clash(node, edge, [e for e in node.edges if e.superseded_at is None])
        self._put_edge(node, edge)
        store.save_node(self.root, node)
        return edge

    def update(
        self,
        record_id: str,
        *,
        value: Any = None,
        valid_from: Any = None,
        valid_to: Any = None,
        known_at: Any = None,
        now: Any = None,
    ) -> Record:
        """Append a new version of a record; the old one is kept, superseded."""
        self._mutable()
        node, old = self.record(record_id)
        if value is not None and not isinstance(old, Fact):
            raise GraphError(f"{record_id} is an edge; only facts have a value")
        stamp = _time(now) or timeutil.now()
        changes: dict[str, Any] = {"changed_at": stamp}
        if value is not None:
            changes["value"] = value
        for name, given in (("valid_from", valid_from), ("valid_to", valid_to), ("known_at", known_at)):
            if given is not None:
                changes[name] = _time(given)
        new = replace(old, **changes)
        if self.model:
            with _modelled():
                self.model.times(new.valid_from, new.valid_to)
                if isinstance(new, Fact):
                    new.value = self.model.fact_value(node.type, new.key, new.value)
            if isinstance(new, Fact):
                self._no_clash(node, new, [f for f in node.facts if f.superseded_at is None and f is not old])
            else:
                self._no_edge_clash(node, new, [e for e in node.edges if e.superseded_at is None and e is not old])
        old.superseded_at = stamp
        if isinstance(new, Fact):
            self._put_fact(node, new)
        else:
            self._put_edge(node, new)
        store.save_node(self.root, node)
        return new

    def retract(self, record_id: str, *, now: Any = None) -> Record:
        """Mark a record as no longer part of the graph, without deleting it."""
        self._mutable()
        node, record = self.record(record_id)
        record.superseded_at = _time(now) or timeutil.now()
        store.save_node(self.root, node)
        return record

    def apply(self, batch: dict[str, Any], *, now: Any = None) -> Counter[str]:
        """Merge a batch of nodes, facts, edges and bodies into the graph.

        Safe to re-run: existing nodes are reused, and a fact or edge that is
        already current (same key and value, or same relation and target) is
        skipped. An existing body is never overwritten. Everything is checked
        before anything is written. Returns counts of what was added or skipped.
        """
        self._mutable()
        stamp = _time(now) or timeutil.now()
        default_known = batch.get("known_at")
        specs = [_node_spec(raw, self.model) for raw in batch.get("nodes") or []]
        edges = [(spec["id"], e) for spec in specs for e in spec["edges"]]
        edges += [(slugify(str(_need(e, "from"))), e) for e in batch.get("edges") or []]

        types = {node.id: node.type for node in self.nodes.values()}
        for spec in specs:
            if types.setdefault(spec["id"], spec["type"]) != spec["type"]:
                raise GraphError(f"node {spec['id']!r} is a {types[spec['id']]}, not a {spec['type']}")
        for src, edge in edges:
            for end in (src, slugify(str(_need(edge, "to")))):
                if end not in types:
                    raise GraphError(f"edge {src} {edge.get('rel')} {edge.get('to')}: no node {end!r}")
        if self.model:
            self._check_batch(specs, edges, types)

        counts: Counter[str] = Counter()
        touched: dict[str, Node] = {}

        def times(raw: dict[str, Any], spec: dict[str, Any] | None) -> dict[str, Any]:
            inherited = spec or {}
            return _stamps(
                raw.get("valid_from", inherited.get("valid_from")),
                raw.get("valid_to", inherited.get("valid_to")),
                raw.get("known_at", inherited.get("known_at", default_known)),
                stamp,
            )

        for spec in specs:
            node = self.nodes.get(spec["id"])
            if node is None:
                node = Node(id=spec["id"], type=spec["type"], name=spec["name"], notes=spec["notes"])
                self._put_node(node)
                counts["nodes added"] += 1
            elif spec["notes"] and not node.notes:
                node.notes = spec["notes"]
            touched[node.id] = node
            for key, raw in spec["facts"]:
                if self.model:
                    raw = {**raw, "value": self.model.fact_value(node.type, key, raw["value"])}
                if any(f.key == key and f.value == raw["value"] and f.superseded_at is None for f in node.facts):
                    counts["facts skipped"] += 1
                    continue
                self._put_fact(node, Fact(id=self._new_id("f"), key=key, value=raw["value"], **times(raw, spec)))
                counts["facts added"] += 1
            if spec["body"]:
                path = store.body_path(self.root, node)
                if path.exists():
                    counts["bodies kept"] += 1
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(spec["body"])
                    counts["bodies added"] += 1

        by_id = {spec["id"]: spec for spec in specs}
        for src, raw in edges:
            node = self.nodes[src]
            rel, dst = str(_need(raw, "rel")), slugify(str(raw["to"]))
            if any(e.rel == rel and e.to == dst and e.superseded_at is None for e in node.edges):
                counts["edges skipped"] += 1
                continue
            if self.model and self.model.edges[rel].symmetric and any(
                e.rel == rel and e.to == src and e.superseded_at is None for e in self.nodes[dst].edges
            ):
                counts["edges skipped"] += 1
                continue
            inherit = by_id.get(src) if "from" not in raw else None
            self._put_edge(node, Edge(id=self._new_id("e"), rel=rel, to=dst, **times(raw, inherit)))
            touched[src] = node
            counts["edges added"] += 1

        for node in touched.values():
            store.save_node(self.root, node)
        return counts

    # -- reads -------------------------------------------------------------

    def node(self, node_id: str) -> Node:
        try:
            return self.nodes[node_id]
        except KeyError:
            raise GraphError(f"no node {node_id!r}") from None

    def record(self, record_id: str) -> tuple[Node, Record]:
        """The current version of a record and the node that holds it."""
        for node, record in self._versions.get(record_id, ()):
            if record.superseded_at is None:
                return node, record
        raise GraphError(f"no current record {record_id!r}")

    def body(self, node_id: str) -> str:
        """The node's long-form Markdown, or '' when it has none."""
        node = self.node(node_id)
        return self.source.read_text(store.body_rel(node)) if self.has_body(node) else ""

    def has_body(self, node: Node) -> bool:
        return self.source.exists(store.body_rel(node))

    def facts(self, node_id: str, at: At | None = NOW) -> list[Fact]:
        """Facts in view; ``at=None`` gives every version."""
        return [f for f in self.node(node_id).facts if at is None or at.sees(f)]

    def span(self, node_id: str, at: At | None = NOW) -> tuple[str | None, str | None]:
        """When the node is in the world, from its records in view: the
        earliest ``valid_from`` and the latest ``valid_to`` across its facts
        and the edges at either end, ``None`` for open. A paper's span starts
        at its publication date; a file's runs from creation to deletion."""
        records = [*self.facts(node_id, at), *self.out_edges(node_id, at), *(e for _, e in self.in_edges(node_id, at))]
        starts = [r.valid_from for r in records if r.valid_from]
        if not starts and not any(r.valid_to for r in records):
            return None, None
        start = min(starts, key=timeutil.start) if starts else None
        if any(r.valid_to is None for r in records):
            return start, None
        ends = [r.valid_to for r in records if r.valid_to]
        return start, (max(ends, key=timeutil.end) if ends else None)

    def out_edges(self, node_id: str, at: At | None = NOW) -> list[Edge]:
        """Edges out of a node in view; ``at=None`` gives every version."""
        return [e for e in self.node(node_id).edges if at is None or at.sees(e)]

    def in_edges(self, node_id: str, at: At | None = NOW) -> list[tuple[Node, Edge]]:
        """Edges into a node with the nodes they come from, in file order.
        ``at=None`` gives every version, superseded ones included."""
        found = [
            (self.nodes[src], attrs["edge"])
            for src, keyed in self._g.pred.get(node_id, {}).items()
            if src in self.nodes
            for attrs in keyed.values()
            if at is None or at.sees(attrs["edge"])
        ]
        found.sort(key=lambda pair: self._pos[pair[0].id])
        return found

    def list_nodes(self, at: At = NOW, type: str | None = None) -> list[Node]:
        """Nodes in view. Under a time filter, a node is hidden when none of
        its facts or edges (in either direction) are in view; a node that
        never had any is always shown."""
        result = []
        for node in self.nodes.values():
            if type and node.type != type:
                continue
            if at.filtered and (node.facts or node.edges or self._g.in_degree(node.id) > 0):
                incoming = (attrs["edge"] for _, _, attrs in self._g.in_edges(node.id, data=True))
                if not (
                    any(at.sees(f) for f in node.facts)
                    or any(at.sees(e) for e in node.edges)
                    or any(at.sees(e) for e in incoming)
                ):
                    continue
            result.append(node)
        return result

    def search(self, text: str, at: At = NOW) -> list[Node]:
        needle = text.casefold()
        hits = []
        for node in self.list_nodes(at):
            haystack = [node.id, node.name, node.type, node.notes, self.body(node.id)]
            haystack += [f"{f.key} {f.value}" for f in self.facts(node.id, at)]
            haystack += [f"{e.rel} {e.to}" for e in self.out_edges(node.id, at)]
            if any(needle in part.casefold() for part in haystack):
                hits.append(node)
        return hits

    def nx(
        self,
        at: At = NOW,
        rels: Iterable[str] | None = None,
        types: Iterable[str] | None = None,
    ) -> networkx.MultiDiGraph:
        """The graph in view as a frozen NetworkX multigraph, for algorithms
        and export. One edge per visible edge, keyed by record id, with
        ``rel``, ``id`` and the time fields as attributes; a symmetric edge is
        added in both directions, the copy marked ``mirror=True``, so the
        undirected view (``to_undirected(as_view=True)``) folds it into one
        edge. A node carries ``type``, ``name`` and one attribute per fact key
        in view (several values joined with ``; ``), so ``nx.write_graphml``
        works on the result. ``rels`` and ``types`` narrow it."""
        rels = set(rels) if rels else None
        types = set(types) if types else None
        nodes = [n for n in self.list_nodes(at) if not types or n.type in types]
        H = networkx.MultiDiGraph(
            valid_at=at.valid_at or "", known_at=at.known_at or "", changed_at=at.changed_at or ""
        )
        for node in nodes:
            H.add_node(node.id, type=node.type, name=node.name, **_flat_facts(self.facts(node.id, at)))
        for node in nodes:
            for edge in self.out_edges(node.id, at):
                if (rels and edge.rel not in rels) or edge.to not in H:
                    continue
                attrs = {"rel": edge.rel, "id": edge.id, **{k: v for k, v in edge._times().items() if v is not None}}
                H.add_edge(node.id, edge.to, key=edge.id, **attrs)
                if edge.to != node.id and self.model and edge.rel in self.model.edges and self.model.edges[edge.rel].symmetric:
                    H.add_edge(edge.to, node.id, key=edge.id, mirror=True, **attrs)
        return networkx.freeze(H)

    def check(self) -> list[str]:
        """Problems that hand edits can introduce."""
        problems = []
        current: dict[str, int] = {}
        on_disk = self.source.writable  # file placement only means something in a working tree
        for node in self.nodes.values():
            if on_disk and not store.node_path(self.root, node).is_file():
                problems.append(f"{node.id}: file is not at nodes/{node.type}/{node.id}.yaml")
            for record in (*node.facts, *node.edges):
                if isinstance(record, Edge) and record.to not in self.nodes:
                    problems.append(f"{node.id}: edge {record.id} points to missing node {record.to!r}")
                if isinstance(record, Fact) and record.key == "file" and record.superseded_at is None:
                    if not (self.root / str(record.value)).exists():
                        problems.append(f"{node.id}: fact {record.id} names missing file {record.value!r}")
                if record.superseded_at is None:
                    current[record.id] = current.get(record.id, 0) + 1
        problems += [f"record id {rid} has {n} current versions" for rid, n in current.items() if n > 1]
        for path in (self.root / store.NODES_DIR).glob("**/*.md") if on_disk else ():
            if path.stem not in self.nodes or path != store.body_path(self.root, self.nodes[path.stem]):
                problems.append(f"{path.relative_to(self.root)}: body has no node")
        if self.model:
            problems += self.model.problems(self.nodes, self.root)
        return sorted(set(problems))

    def _no_clash(self, node: Node, fact: Fact, current: list[Fact]) -> None:
        with _modelled():
            other = self.model.clash(node.type, fact.key, fact, fact.value, current)
        if other:
            raise GraphError(
                f"{node.id} already has {fact.key} = {other.value!r} ({other.id}) and {fact.key} takes one value; "
                f"use `bib update {other.id} --value ...`"
            )

    def _no_edge_clash(self, node: Node, edge: Edge, current: list[Edge]) -> None:
        if other := self.model.edge_clash(edge.rel, edge, edge.to, current):
            raise GraphError(
                f"{node.id} already has {edge.rel} {other.to} ({other.id}) and {edge.rel} takes one target; "
                f"use `bib retract {other.id}` first"
            )

    def _check_batch(self, specs: list[dict[str, Any]], edges: list[tuple[str, Any]], types: dict[str, str]) -> None:
        """Model checks for a batch, all before anything is written."""
        model = self.model
        with _modelled():
            for spec in specs:
                model.node_spec(spec["type"])
                existing = self.nodes.get(spec["id"])
                current = [f for f in existing.facts if f.superseded_at is None] if existing else []
                keys = {key for key, _ in spec["facts"]} | {f.key for f in current}
                for key, raw in spec["facts"]:
                    model.times(raw.get("valid_from", spec.get("valid_from")), raw.get("valid_to", spec.get("valid_to")))
                    value = model.fact_value(spec["type"], key, raw["value"])
                    probe = Fact(
                        id="",
                        key=key,
                        value=value,
                        valid_from=_time(raw.get("valid_from", spec.get("valid_from"))),
                        valid_to=_time(raw.get("valid_to", spec.get("valid_to"))),
                        known_at="",
                        changed_at="",
                    )
                    if other := model.clash(spec["type"], key, probe, value, current):
                        raise ModelError(f"{spec['id']}: {key} takes one value, but has {other.value!r} and {value!r}")
                    current.append(probe)
                if existing is None:
                    for key, fact_spec in (model.nodes[spec["type"]].facts or {}).items():
                        if fact_spec.required and key not in keys:
                            raise ModelError(f"{spec['id']}: missing required fact {key!r}")
            by_id = {spec["id"]: spec for spec in specs}
            current_edges = {
                node.id: [e for e in node.edges if e.superseded_at is None] for node in self.nodes.values()
            }
            for src, raw in edges:
                rel, dst = str(_need(raw, "rel")), slugify(str(raw["to"]))
                inherit = by_id.get(src, {}) if "from" not in raw else {}
                valid_from = raw.get("valid_from", inherit.get("valid_from"))
                valid_to = raw.get("valid_to", inherit.get("valid_to"))
                model.times(valid_from, valid_to)
                model.edge_spec(rel, types[src], types[dst])
                probe = Edge(id="", rel=rel, to=dst, valid_from=_time(valid_from), valid_to=_time(valid_to), known_at="", changed_at="")
                current = current_edges.setdefault(src, [])
                if other := model.edge_clash(rel, probe, dst, current):
                    raise ModelError(f"{src}: {rel} takes one target, but has {other.to!r} and {dst!r}")
                current.append(probe)
            for spec in specs:
                if spec["id"] in self.nodes:
                    continue
                rels = {e.rel for e in current_edges.get(spec["id"], [])}
                for rel, edge_spec in model.edges.items():
                    if edge_spec.required and spec["type"] in edge_spec.sources and rel not in rels:
                        raise ModelError(f"{spec['id']}: missing required edge {rel!r}")

    def _new_id(self, prefix: str) -> str:
        while (candidate := prefix + secrets.token_hex(3)) in self._versions:
            pass
        return candidate


def _need(raw: dict[str, Any], key: str) -> Any:
    if not isinstance(raw, dict) or raw.get(key) in (None, ""):
        raise GraphError(f"missing {key!r} in {raw!r}")
    return raw[key]


def _node_spec(raw: dict[str, Any], model: DataModel | None = None) -> dict[str, Any]:
    """A batch node in normal form. ``facts`` maps a key to a value, to a list
    of values (one fact each), or to ``{value, valid_from, ...}``. Without an
    ``id``, the node type's id template in the model is used, then the name."""
    name = str(_need(raw, "name"))
    type = slugify(str(_need(raw, "type")))
    node_id = raw.get("id")
    if node_id is None and model is not None:
        facts = raw.get("facts") or {}
        targets = {str(e.get("rel")): e.get("to") for e in raw.get("edges") or [] if isinstance(e, dict)}
        node_id = model.render_id(
            type, name, lambda key: _first_value(facts[key]) if key in facts else targets.get(key)
        )
    spec = {
        "id": slugify(str(node_id or name)),
        "type": type,
        "name": name,
        "notes": str(raw.get("notes") or ""),
        "body": str(raw.get("body") or ""),
        "edges": list(raw.get("edges") or []),
        "facts": [],
        **{k: raw[k] for k in ("valid_from", "valid_to", "known_at") if k in raw},
    }
    if not spec["id"] or not spec["type"]:
        raise GraphError(f"a node needs a non-empty id and type: {raw!r}")
    for key, given in (raw.get("facts") or {}).items():
        for item in given if isinstance(given, list) else [given]:
            fact = item if isinstance(item, dict) else {"value": item}
            if "value" not in fact:
                raise GraphError(f"node {spec['id']}: fact {key!r} needs a value")
            spec["facts"].append((str(key), fact))
    return spec


def _flat_facts(facts: list[Fact]) -> dict[str, Any]:
    """Facts as scalar node attributes: a lone value as is, several joined.
    ``type`` and ``name`` are the node's own and are skipped."""
    by_key: dict[str, list[Any]] = {}
    for fact in facts:
        if fact.key not in ("type", "name"):
            by_key.setdefault(fact.key, []).append(fact.value)
    flat = {}
    for key, values in by_key.items():
        if len(values) == 1 and isinstance(values[0], (str, int, float, bool)):
            flat[key] = values[0]
        else:
            flat[key] = "; ".join(str(v) for v in values)
    return flat


def _first_value(given: Any) -> Any:
    if isinstance(given, list):
        given = given[0] if given else None
    return given.get("value") if isinstance(given, dict) else given


@contextmanager
def _modelled():
    """Model violations surface as graph errors."""
    try:
        yield
    except ModelError as err:
        raise GraphError(str(err)) from None


def slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def _time(value: Any) -> str | None:
    try:
        return timeutil.canonical(value)
    except ValueError as err:
        raise GraphError(str(err)) from err


def _stamps(valid_from: Any, valid_to: Any, known_at: Any, now: Any) -> dict[str, str | None]:
    stamp = _time(now) or timeutil.now()
    return {
        "valid_from": _time(valid_from),
        "valid_to": _time(valid_to),
        "known_at": _time(known_at) or stamp,
        "changed_at": stamp,
    }
