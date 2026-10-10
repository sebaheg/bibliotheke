"""``bib fetch``: a URL becomes a node, the files behind it, and its neighbours.

A *handler* knows one kind of source: YouTube, arXiv, GitHub, or any web
page. It claims a URL by returning the source's canonical key, and fetches
that key into a ``Fetched``: one batch node, the files an agent will read
later under ``sources/<kind>/``, and the people or organisations the source
names. The writer here is the same for every kind: it turns a ``Fetched``
into the batch structure ``Graph.apply`` takes, so the model checks it like
any import, then writes the files. A refetch finds the node again through
its identity fact and appends new versions of the facts that changed.

Bibliotheke itself never learns what a video or a repository is. Like
``bibliotheke.repo``, this package only uses the public API.
"""

from __future__ import annotations

import copy
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from bibliotheke import modeledit
from bibliotheke.graph import Graph, GraphError, slugify
from bibliotheke.model import Fact

SOURCES_DIR = "sources"


class FetchError(GraphError):
    """Something the source, the network or a missing dependency refused."""


@dataclass
class Options:
    """What a handler may be told; each one reads what concerns it."""

    lang: str = "en"  # caption language, for videos
    transcribe: str = "auto"  # captions, whisper, or auto (captions, then whisper)
    cache: Path | None = None  # where downloads that are not part of the graph go


@dataclass
class Fetched:
    """What one fetch found, before it is written to any graph."""

    kind: str
    key: str  # the source's canonical key: a video id, an arXiv id, owner/repo, a URL
    node: dict[str, Any]  # a batch node: type, name, facts, edges, body, valid_from
    files: dict[str, str | bytes] = field(default_factory=dict)  # path under the root -> content
    neighbours: list[dict[str, Any]] = field(default_factory=list)  # batch nodes the node's edges point to
    identity: tuple[str, str] | None = None  # the fact (key, value) that finds the node again
    links: list[str] = field(default_factory=list)  # URLs the source points at that a handler could fetch


class Handler(Protocol):
    """One kind of source."""

    kind: str
    type: str  # the node type a fetch produces unless told otherwise
    model: dict[str, Any]  # the node types and edges the kind needs, in model form

    def key(self, url: str) -> str | None:
        """The source's canonical key when the URL is one of its, else None."""
        ...

    def fetch(self, key: str, options: Options) -> Fetched: ...


def handlers() -> list[Handler]:
    """Every handler, in the order they get to claim a URL; the web one last."""
    from bibliotheke.fetch import arxiv, github, web, youtube

    return [youtube, arxiv, github, web]  # type: ignore[list-item]


def handler_for(url: str) -> tuple[Handler, str]:
    for handler in handlers():
        key = handler.key(url)
        if key:
            return handler, key
    raise FetchError(f"no handler takes {url!r}")


def by_kind(kind: str) -> Handler:
    for handler in handlers():
        if handler.kind == kind:
            return handler
    raise FetchError(f"no source kind {kind!r}; one of {', '.join(h.kind for h in handlers())}")


def claimed(urls: list[str], own: tuple[str, str] | None = None) -> list[str]:
    """The URLs among ``urls`` that a handler other than the web one takes,
    once each, in order: what a source points at that could be fetched next.
    ``own`` is this source's (kind, key), so its links to itself are left out."""
    found: list[str] = []
    for url in urls:
        for handler in handlers():
            key = handler.key(url)
            if handler.kind != "web" and key and url not in found and (handler.kind, key) != own:
                found.append(url)
    return found


_SUBTITLE = re.compile(r"\s*(?::|\||[-–—]\s)\s*")


def name_id(title: str, words: int = 8) -> str:
    """A node id from a title: the part before a subtitle separator (a colon,
    a bar, a dash) when it is a name on its own, at most ``words`` words."""
    head = _SUBTITLE.split(title, maxsplit=1)[0]
    if len(head.split()) < 2 or len(head) < 12:
        head = title
    return "-".join(slugify(head).split("-")[:words])


def fetch(url: str, options: Options | None = None) -> Fetched:
    handler, key = handler_for(url)
    return handler.fetch(key, options or Options())


# -- writing -----------------------------------------------------------------


@dataclass
class Prepared:
    """A fetch turned into what the graph takes, with what had to be left out."""

    node_id: str
    type: str
    existing: bool
    batch: dict[str, Any]
    dropped: list[str]


@dataclass
class Result:
    node_id: str
    type: str
    existing: bool
    counts: Counter[str]
    files: list[str]
    dropped: list[str]


def prepare(graph: Graph, fetched: Fetched, *, type: str | None = None, id: str | None = None) -> Prepared:
    """The batch a fetch becomes in this graph. The type is the handler's
    unless given; the id is given, found through the identity fact, or the
    node's own. With a model, facts, neighbours and edges the model does not
    allow are dropped and reported rather than refused, so a fetch fits a
    graph whose model names fewer things than the source knows."""
    model = graph.model
    type = slugify(type or fetched.node["type"])
    if model and type not in model.nodes:
        raise FetchError(
            f"the model has no node type {type!r}; `bib model add source {fetched.kind}` adds what a {fetched.kind} "
            f"fetch needs, or pass --type"
        )
    node_id = slugify(id) if id else _find(graph, type, fetched.identity) or slugify(fetched.node.get("id") or fetched.node["name"])
    existing = node_id in graph.nodes
    if existing and graph.nodes[node_id].type != type:
        raise FetchError(f"node {node_id!r} is a {graph.nodes[node_id].type}, not a {type}")
    dropped: list[str] = []

    main = copy.deepcopy(fetched.node)
    main["type"] = type
    main["id"] = node_id
    main["facts"] = _allowed_facts(model, type, main.get("facts") or {}, node_id, dropped)
    nodes = [main]
    edges = []
    for raw in fetched.neighbours:
        neighbour = copy.deepcopy(raw)
        neighbour["type"] = slugify(neighbour["type"])
        neighbour.setdefault("id", slugify(neighbour["name"]))
        neighbour["id"] = slugify(neighbour["id"])
        if model and neighbour["type"] not in model.nodes:
            dropped.append(f"{neighbour['type']} {neighbour['name']!r}: the model has no {neighbour['type']} type")
            continue
        if neighbour["id"] in graph.nodes and graph.nodes[neighbour["id"]].type != neighbour["type"]:
            dropped.append(f"{neighbour['type']} {neighbour['name']!r}: {neighbour['id']} is a {graph.nodes[neighbour['id']].type}")
            continue
        neighbour["facts"] = _allowed_facts(model, neighbour["type"], neighbour.get("facts") or {}, neighbour["id"], dropped)
        nodes.append(neighbour)
    kept = {n["id"]: n["type"] for n in nodes}
    for raw in main.pop("edges", None) or []:
        dst = slugify(str(raw["to"]))
        if dst not in kept and dst not in graph.nodes:
            continue  # its neighbour was dropped
        dst_type = kept.get(dst) or graph.nodes[dst].type
        if model and (raw["rel"] not in model.edges or not model.edges[raw["rel"]].allows(type, dst_type)):
            dropped.append(f"edge {raw['rel']} to {dst}: the model has no {raw['rel']} from {type} to {dst_type}")
            continue
        edges.append({"from": node_id, **raw, "to": dst})
    batch: dict[str, Any] = {"nodes": nodes, "edges": edges}
    return Prepared(node_id, type, existing, batch, dropped)


def write(graph: Graph, fetched: Fetched, *, type: str | None = None, id: str | None = None, now: Any = None) -> Result:
    """Merge a fetch into the graph and write its files. Facts that changed
    since the last fetch get a new version; everything else goes through
    ``Graph.apply``, so a refetch adds nothing twice."""
    prepared = prepare(graph, fetched, type=type, id=id)
    counts: Counter[str] = Counter()
    main = prepared.batch["nodes"][0]
    if prepared.existing:
        main["facts"] = _update_changed(graph, graph.node(prepared.node_id), main["facts"], counts, now)
    counts.update(graph.apply(prepared.batch, now=now))
    written = []
    for rel, content in fetched.files.items():
        path = graph.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)
        written.append(rel)
    counts["files written"] = len(written)
    return Result(prepared.node_id, prepared.type, prepared.existing, counts, written, prepared.dropped)


def _find(graph: Graph, type: str, identity: tuple[str, str] | None) -> str | None:
    if identity is None:
        return None
    key, value = identity
    for node in graph.nodes.values():
        if node.type == type and any(f.key == key and str(f.value) == value and f.superseded_at is None for f in node.facts):
            return node.id
    return None


def _allowed_facts(model, type: str, facts: dict[str, Any], node_id: str, dropped: list[str]) -> dict[str, Any]:
    kept = {}
    for key, given in facts.items():
        if given is None or given == [] or (isinstance(given, dict) and given.get("value") is None):
            continue
        if model and key not in (model.nodes[type].facts or {}):
            dropped.append(f"{node_id}: the model has no {key!r} fact on a {type}")
            continue
        kept[key] = given
    return kept


def _update_changed(graph: Graph, node, facts: dict[str, Any], counts: Counter[str], now: Any) -> dict[str, Any]:
    """A single value for a key the node already has one current fact for
    becomes a new version of that fact when it differs. Returns the facts the
    batch still has to add."""
    rest = {}
    for key, given in facts.items():
        current = [f for f in node.facts if f.key == key and f.superseded_at is None]
        value = given.get("value") if isinstance(given, dict) else given
        if isinstance(given, list) or len(current) != 1:
            rest[key] = given
            continue
        old: Fact = current[0]
        new_value = graph.model.fact_value(node.type, key, value) if graph.model else value
        if old.value == new_value:
            counts["facts skipped"] += 1
            continue
        graph.update(old.id, value=value, valid_from=given.get("valid_from") if isinstance(given, dict) else None, now=now)
        counts["facts updated"] += 1
    return rest


# -- the model a kind needs ----------------------------------------------------


def install_model(marker: dict[str, Any], kind: str) -> tuple[dict[str, Any], list[str]]:
    """Add the node types, facts and edges a kind's fetches use to the model,
    leaving what is already there alone. Returns the new marker and a line
    per thing added."""
    handler = by_kind(kind)
    added: list[str] = []
    have_nodes = marker.get("nodes") or {}
    for type, spec in handler.model.get("nodes", {}).items():
        if type not in have_nodes:
            marker = modeledit.add_node_type(
                marker, type, description=spec.get("description", ""), name=spec.get("name", ""), id=spec.get("id"), body=spec.get("body", "")
            )
            added.append(f"node type {type}")
        for key, fact in (spec.get("facts") or {}).items():
            fact = {"type": fact} if isinstance(fact, str) else fact
            if key in ((marker["nodes"].get(type) or {}).get("facts") or {}):
                continue
            marker = modeledit.add_fact(
                marker, type, key, kind=fact.get("type", "text"), required=bool(fact.get("required")),
                many=bool(fact.get("many")), values=fact.get("values"), description=fact.get("description", ""),
            )
            added.append(f"fact {type}.{key}")
    for rel, spec in handler.model.get("edges", {}).items():
        if rel in (marker.get("edges") or {}):
            continue
        sources, targets = _types(spec["from"]), _types(spec["to"])
        for type in sources + targets:
            if type not in marker["nodes"]:
                marker = modeledit.add_node_type(marker, type)
                added.append(f"node type {type}")
        marker = modeledit.add_edge(marker, rel, sources, targets, many=spec.get("many", True), description=spec.get("description", ""))
        added.append(f"edge {rel}")
    return marker, added


def _types(given: Any) -> list[str]:
    return list(given) if isinstance(given, list) else [given]


def source_path(kind: str, *parts: str) -> str:
    """Where a kind keeps a file: ``sources/<kind>/<parts...>``."""
    return "/".join((SOURCES_DIR, kind, *parts))


__all__ = [
    "Fetched",
    "FetchError",
    "Handler",
    "Options",
    "Prepared",
    "Result",
    "by_kind",
    "claimed",
    "fetch",
    "handler_for",
    "handlers",
    "install_model",
    "name_id",
    "prepare",
    "source_path",
    "write",
]
