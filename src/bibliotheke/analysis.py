"""Graph algorithms over the graph as it was at a point in time: paths,
rankings and communities, all on ``Graph.nx()``.

Edges are followed either way unless ``directed`` is set, since a path from a
paper to its author's employer runs against the arrow of ``authored_by``.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import Iterable

import networkx as nx

from bibliotheke.graph import NOW, At, Graph, GraphError
from bibliotheke.model import Node

BY = ("degree", "pagerank", "betweenness")


@dataclass(frozen=True)
class Step:
    """One hop of a path, from ``src`` to ``dst`` in path order. ``forward``
    is false when the hop runs against the edge's direction."""

    src: str
    rel: str
    dst: str
    id: str
    forward: bool


def shortest_path(
    graph: Graph,
    src: str,
    dst: str,
    *,
    at: At = NOW,
    rels: Iterable[str] | None = None,
    directed: bool = False,
) -> list[Step]:
    graph.node(src)
    graph.node(dst)
    H = graph.nx(at, rels)
    for end in (src, dst):
        if end not in H:
            raise GraphError(f"{end} is not in view")
    try:
        ids = nx.shortest_path(H if directed else H.to_undirected(as_view=True), src, dst)
    except nx.NetworkXNoPath:
        raise GraphError(f"no path from {src} to {dst}") from None
    return [_step(H, u, v) for u, v in pairwise(ids)]


def rank(
    graph: Graph,
    by: str,
    *,
    at: At = NOW,
    rels: Iterable[str] | None = None,
    types: Iterable[str] | None = None,
    directed: bool = False,
    top: int | None = 20,
) -> list[tuple[Node, float]]:
    """Nodes by centrality, best first. ``degree`` counts edges either way,
    ``betweenness`` how many shortest paths run through a node, ``pagerank``
    how much of the graph points at it, directly or through other nodes."""
    if by not in BY:
        raise GraphError(f"--by must be one of {', '.join(BY)}")
    H = graph.nx(at, rels, types)
    U = H if directed else H.to_undirected(as_view=True)
    if by == "degree":
        scores = {node: float(degree) for node, degree in U.degree()}
    elif by == "betweenness":
        scores = nx.betweenness_centrality(U)
    else:
        scores = pagerank(U)
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [(graph.nodes[node_id], score) for node_id, score in ranked[:top]]


def communities(
    graph: Graph,
    *,
    at: At = NOW,
    rels: Iterable[str] | None = None,
    types: Iterable[str] | None = None,
    seed: int = 0,
    min_size: int = 1,
) -> list[list[Node]]:
    """Louvain communities, largest first, members by id."""
    U = graph.nx(at, rels, types).to_undirected(as_view=True)
    groups = [sorted(c) for c in nx.community.louvain_communities(U, seed=seed) if len(c) >= min_size]
    groups.sort(key=lambda group: (-len(group), group[0]))
    return [[graph.nodes[node_id] for node_id in group] for group in groups]


def pagerank(H: nx.Graph, alpha: float = 0.85, max_iter: int = 100, tol: float = 1e-6) -> dict[str, float]:
    """PageRank by power iteration, the same scheme as NetworkX's reference
    implementation (every edge weighs 1, parallel edges count, a node with no
    out-edges spreads its rank over everyone), without its SciPy dependency."""
    D = H if H.is_directed() else H.to_directed(as_view=True)
    n = D.number_of_nodes()
    if n == 0:
        return {}
    x = dict.fromkeys(D, 1.0 / n)
    out = {node: sum(len(keyed) for keyed in D.succ[node].values()) for node in D}
    for _ in range(max_iter):
        last = x
        x = dict.fromkeys(D, 0.0)
        dangling = alpha * sum(last[node] for node in D if out[node] == 0)
        for u in D:
            if out[u]:
                share = alpha * last[u] / out[u]
                for v, keyed in D.succ[u].items():
                    x[v] += share * len(keyed)
        for node in x:
            x[node] += (dangling + 1.0 - alpha) / n
        if sum(abs(x[node] - last[node]) for node in x) < n * tol:
            return x
    raise GraphError(f"pagerank did not converge in {max_iter} iterations")


def _step(H: nx.MultiDiGraph, u: str, v: str) -> Step:
    """The edge behind a hop. A mirror counts as forward: a symmetric edge
    has no direction, whichever node's file holds it."""
    for key, attrs in (H.get_edge_data(u, v) or {}).items():
        return Step(u, attrs["rel"], v, key, True)
    for key, attrs in (H.get_edge_data(v, u) or {}).items():
        if not attrs.get("mirror"):
            return Step(u, attrs["rel"], v, key, False)
    raise GraphError(f"no edge between {u} and {v}")  # pragma: no cover
