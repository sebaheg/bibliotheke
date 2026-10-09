"""What changed between two states of a graph, e.g. two git revisions.

Nodes are compared by id; facts and edges by record id, on their current
version, so an update shows as ``changed`` and a retraction as ``retracted``
whatever the file-level diff looks like.
"""

from __future__ import annotations

from typing import Any

from bibliotheke.graph import Graph
from bibliotheke.model import Edge, Fact, Node, Record

KINDS = ("nodes", "facts", "edges")
CHANGES = {"nodes": ("added", "removed", "changed"), "facts": ("added", "changed", "retracted"), "edges": ("added", "changed", "retracted")}


def diff_graphs(old: Graph, new: Graph) -> dict[str, dict[str, list[dict[str, Any]]]]:
    result: dict[str, dict[str, list[dict[str, Any]]]] = {kind: {change: [] for change in CHANGES[kind]} for kind in KINDS}
    for node_id in sorted(set(old.nodes) | set(new.nodes)):
        before, after = old.nodes.get(node_id), new.nodes.get(node_id)
        if before is None:
            result["nodes"]["added"].append(_node(after))
        elif after is None:
            result["nodes"]["removed"].append(_node(before))
        elif (before.type, before.name, before.notes) != (after.type, after.name, after.notes):
            result["nodes"]["changed"].append(_node(after))

    was, now = _current(old), _current(new)
    for record_id in sorted(set(was) | set(now)):
        before, after = was.get(record_id), now.get(record_id)
        if after is not None and before is None:
            change = "added"
        elif before is not None and after is None:
            if before[0].id not in new.nodes:
                continue  # the whole node went; reported above
            change = "retracted"
        elif before is not None and after is not None and before[1].to_dict() != after[1].to_dict():
            change = "changed"
        else:
            continue
        node, record = after or before
        kind = "facts" if isinstance(record, Fact) else "edges"
        result[kind][change].append(_record(node, record))
    return result


def summary(diff: dict[str, dict[str, list[Any]]]) -> str:
    parts = [
        f"{len(entries)} {kind[:-1] if len(entries) == 1 else kind} {change}"
        for kind in KINDS
        for change, entries in diff[kind].items()
        if entries
    ]
    return ", ".join(parts) or "no changes"


def _current(graph: Graph) -> dict[str, tuple[Node, Record]]:
    return {
        record.id: (node, record)
        for node in graph.nodes.values()
        for record in (*node.facts, *node.edges)
        if record.superseded_at is None
    }


def _node(node: Node) -> dict[str, Any]:
    return {"id": node.id, "type": node.type, "name": node.name}


def _record(node: Node, record: Record) -> dict[str, Any]:
    if isinstance(record, Edge):
        return {"node": node.id, "id": record.id, "rel": record.rel, "to": record.to}
    return {"node": node.id, "id": record.id, "key": record.key, "value": record.value}
