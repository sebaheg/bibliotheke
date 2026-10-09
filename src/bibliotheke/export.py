"""Nodes as a Markdown table, e.g. a catalogue of papers.

A column is ``id``, ``name`` or ``type``, a fact key (``year``), the names of
out-edge targets (``>authored_by``) or the names of in-edge sources
(``<cites``, headed ``← cites``). The name links to the node's body when it has one.
"""

from __future__ import annotations

from typing import Any

from bibliotheke import store
from bibliotheke.graph import NOW, At, Graph, GraphError


def cell(graph: Graph, node_id: str, column: str, at: At = NOW) -> list[Any]:
    """Every value of one column for one node."""
    node = graph.node(node_id)
    if column in ("id", "name", "type"):
        return [getattr(node, column)]
    if column.startswith(">"):
        return [_name(graph, e.to) for e in graph.out_edges(node_id, at) if e.rel == column[1:]]
    if column.startswith("<"):
        return [other.name for other, e in graph.in_edges(node_id, at) if e.rel == column[1:]]
    return [f.value for f in graph.facts(node_id, at) if f.key == column]


def markdown_table(
    graph: Graph,
    columns: list[str],
    *,
    at: At = NOW,
    type: str | None = None,
    sort: str | None = None,
) -> str:
    if not columns:
        raise GraphError("no columns")
    nodes = graph.list_nodes(at, type=type)
    nodes.sort(key=lambda n: n.name.casefold())
    if sort:
        key = sort.removeprefix("-")
        # Nodes without the value go last whichever way the table is sorted.
        have = [n for n in nodes if cell(graph, n.id, key, at)]
        rest = [n for n in nodes if not cell(graph, n.id, key, at)]
        have.sort(key=lambda n: _sort_key(cell(graph, n.id, key, at)[0]), reverse=sort.startswith("-"))
        nodes = have + rest
    lines = ["| " + " | ".join(_header(c) for c in columns) + " |", "|" + "---|" * len(columns)]
    for node in nodes:
        row = []
        for column in columns:
            text = "; ".join(str(v) for v in cell(graph, node.id, column, at))
            if column == "name" and graph.has_body(node):
                text = f"[{text}]({store.body_rel(node)})"
            row.append(text.replace("|", "\\|").replace("\n", " "))
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


def _header(column: str) -> str:
    """``>rel`` reads as ``rel``; ``<rel`` as ``← rel``, since its cells are the sources."""
    return f"← {column[1:]}" if column.startswith("<") else column.removeprefix(">")


def _name(graph: Graph, node_id: str) -> str:
    return graph.nodes[node_id].name if node_id in graph.nodes else node_id


def _sort_key(value: Any) -> tuple[int, Any]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (0, value)
    return (1, str(value).casefold())
