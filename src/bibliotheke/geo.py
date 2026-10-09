"""Where things are: nodes with coordinates, and nodes placed through them.

A *place* is any node with `lat` and `lon` facts (a city, a lab, a site).
Any other node is placed by following its edges: the first relation in `via`
that leads to places decides, so an explicit `based_in` wins over a place
inherited through `affiliated_with`. Two places are *linked* through a node
without a place of its own that touches located nodes in both, e.g. a paper
whose authors work in different cities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

from bibliotheke.graph import NOW, At, Graph
from bibliotheke.model import Node

VIA = ("based_in", "located_in", "affiliated_with")
WHO = ("person",)
DEPTH = 3  # person -> organisation -> campus -> city at most


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


def build_geo(graph: Graph, at: At = NOW, who: tuple[str, ...] = WHO, via: tuple[str, ...] = VIA) -> GeoScene:
    located: dict[str, list[str]] = {}
    unplaced = []
    for node in sorted(graph.list_nodes(at), key=lambda n: n.id):
        if node.type in who:
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
    for node in graph.list_nodes(at):
        # only placeless nodes link places: a paper, not the lab its authors share
        if node.id in located or node.id in index or places_of(graph, node.id, at, via):
            continue
        neighbours = {e.to for e in graph.out_edges(node.id, at)}
        neighbours |= {other.id for other, _ in graph.in_edges(node.id, at)}
        here = sorted({index[p] for n in neighbours if n in located for p in located[n]})
        for pair in combinations(here, 2):
            links.setdefault(pair, []).append(node.id)
    return GeoScene(places, links, unplaced)
