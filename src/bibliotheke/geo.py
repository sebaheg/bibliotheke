"""Where things are: nodes with coordinates, and nodes placed through them.

A *place* is any node with `lat` and `lon` facts (a city, a lab, a site).
Any other node is placed by following its edges: the first relation in `via`
that leads to places decides, so an explicit `based_in` wins over a place
inherited through `affiliated_with`. Two places are *linked* through a node
without a place of its own that touches located nodes in both, e.g. a paper
whose authors work in different cities (``arcs="shared"``), or by the edges
of given relations between nodes in different places (``arcs=[rel, …]``).

Nothing here needs to be configured for a graph whose nodes carry `lat`/`lon`:
:func:`infer` reads which types to place and which relations lead to a
place from the graph itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

from bibliotheke.graph import NOW, At, Graph
from bibliotheke.model import Node

VIA = ("based_in", "located_in", "affiliated_with")
WHO = ("person",)
DEPTH = 3  # person -> organisation -> campus -> city at most
ARCS = ("shared", "none")  # or a list of relations
# How far inference looks for a place: a thing at an organisation in a city.
# Further than that every type in a connected graph ends up placed through
# every other (a claim, in a paper, by authors, at a lab, in a city).
INFER_DEPTH = 2


def coordinates(graph: Graph, node_id: str, at: At = NOW) -> tuple[float, float] | None:
    """The node's own `(lat, lon)`, from its current `lat`/`lon` facts."""
    found: dict[str, float] = {}
    for fact in graph.facts(node_id, at):
        if fact.key in ("lat", "lon"):
            try:
                found[fact.key] = float(fact.value)
            except (TypeError, ValueError):
                continue
    if "lat" in found and "lon" in found and -90 <= found["lat"] <= 90 and -180 <= found["lon"] <= 180:
        return found["lat"], found["lon"]
    return None


def places_of(graph: Graph, node_id: str, at: At = NOW, via: tuple[str, ...] = VIA, depth: int = 0) -> list[str]:
    """The places a node is at: itself if it has coordinates, else wherever
    the first relation in `via` that leads anywhere takes it."""
    if coordinates(graph, node_id, at):
        return [node_id]
    if depth >= DEPTH:
        return []
    edges = graph.out_edges(node_id, at)
    for rel in via:
        found: list[str] = []
        for edge in edges:
            if edge.rel == rel and edge.to in graph.nodes:
                found += places_of(graph, edge.to, at, via, depth + 1)
        if found:
            return list(dict.fromkeys(found))
    return []


@dataclass
class Place:
    node: Node
    lat: float
    lon: float
    members: list[str] = field(default_factory=list)  # located node ids


@dataclass
class GeoScene:
    places: list[Place]
    links: dict[tuple[int, int], list[str]]  # (place, place) -> ids of the nodes linking them
    unplaced: list[str]  # nodes of the `who` types with no place


def has_coordinates(graph: Graph) -> bool:
    """Whether any node has ever had both a `lat` and a `lon` fact."""
    return bool(_coordinate_types(graph))


def _coordinate_types(graph: Graph) -> set[str]:
    return {
        node.type
        for node in graph.nodes.values()
        if {"lat", "lon"} <= {fact.key for fact in node.facts}
    }


def infer(graph: Graph) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Which node types to place and which relations place them, read from
    the graph: a relation *leads to a place* when it points at a type whose
    nodes have coordinates (``located_in`` an organisation's city), or at a
    type placed that way, up to :data:`INFER_DEPTH` hops. The relations closest to
    the coordinates come first, so a direct ``based_in`` beats an inherited
    ``affiliated_with``. Every type that reaches a place that way is placed:
    the people, the organisations, the papers, whatever is somewhere."""
    coordinated = _coordinate_types(graph)
    if not coordinated:
        return (), ()
    triples = {
        (node.type, edge.rel, graph.nodes[edge.to].type)
        for node in graph.nodes.values()
        for edge in node.edges
        if edge.to in graph.nodes and edge.superseded_at is None
    }
    reached = set(coordinated)  # types that have, or lead to, a place
    hops: dict[str, int] = {}  # relation -> how far from the coordinates
    level: dict[str, int] = {t: 0 for t in coordinated}
    for depth in range(1, INFER_DEPTH + 1):
        new = set()
        for src, rel, dst in sorted(triples):
            if dst in reached and src not in coordinated and level.get(dst, INFER_DEPTH) < depth:
                hops.setdefault(rel, depth)
                if src not in reached:
                    new.add(src)
                    level[src] = depth
        reached |= new
        if not new:
            break
    via = tuple(sorted(hops, key=lambda rel: (hops[rel], rel)))
    who = tuple(sorted(t for t in reached if t not in coordinated))
    return who, via


def build_geo(
    graph: Graph,
    at: At = NOW,
    who: tuple[str, ...] = WHO,
    via: tuple[str, ...] = VIA,
    arcs: str | tuple[str, ...] = "shared",
    hide: tuple[str, ...] = (),
) -> GeoScene:
    """Places, what is placed at each, and the links between them. ``hide``
    leaves out node types both as placed nodes and as linking ones."""
    hidden = set(hide)
    located: dict[str, list[str]] = {}
    unplaced = []
    for node in sorted(graph.list_nodes(at), key=lambda n: n.id):
        if node.type in who and node.type not in hidden:
            where = places_of(graph, node.id, at, via)
            if where:
                located[node.id] = where
            else:
                unplaced.append(node.id)
    order = sorted({p for where in located.values() for p in where}, key=lambda p: graph.nodes[p].name)
    index = {place_id: i for i, place_id in enumerate(order)}
    places = []
    for place_id in order:
        lat, lon = coordinates(graph, place_id, at)  # type: ignore[misc]
        places.append(Place(graph.nodes[place_id], lat, lon))
    for node_id, where in located.items():
        for place_id in where:
            places[index[place_id]].members.append(node_id)

    links: dict[tuple[int, int], list[str]] = {}
    if arcs == "none":
        return GeoScene(places, links, unplaced)
    if arcs != "shared":
        # an arc per edge of the given relations between two placed nodes
        # (or places) in different places
        def where(node_id: str) -> list[int]:
            if node_id in index:
                return [index[node_id]]
            return [index[p] for p in located.get(node_id, [])]

        for node in graph.list_nodes(at):
            if node.type in hidden:
                continue
            for edge in graph.out_edges(node.id, at):
                if edge.rel not in arcs or edge.to not in graph.nodes or graph.nodes[edge.to].type in hidden:
                    continue
                for a in where(node.id):
                    for b in where(edge.to):
                        if a != b:
                            links.setdefault((min(a, b), max(a, b)), []).append(edge.id)
        return GeoScene(places, links, unplaced)
    for node in graph.list_nodes(at):
        # only placeless nodes link places: a paper, not the lab its authors share
        if node.type in hidden or node.id in located or node.id in index or places_of(graph, node.id, at, via):
            continue
        neighbours = {e.to for e in graph.out_edges(node.id, at)}
        neighbours |= {other.id for other, _ in graph.in_edges(node.id, at)}
        here = sorted({index[p] for n in neighbours if n in located for p in located[n]})
        for pair in combinations(here, 2):
            links.setdefault(pair, []).append(node.id)
    return GeoScene(places, links, unplaced)
