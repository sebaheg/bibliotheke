"""Changing a data model once data exists.

Three things live here: edits to a model's ``nodes``, ``edges`` and
``vocabularies`` (add, rename, drop), applied to the dict ``store.load_model``
gives and written back with ``store.save_model`` in whichever layout the
graph uses, the
rewrites of node files that a rename or a drop needs, and the diff between
two models, each change classified as *additive* (nothing that was allowed
is refused), *destructive* (something that was allowed is now refused, so
existing records may break) or *neutral* (descriptions).

The model governs current records only. A rename rewrites every version of a
record, superseded ones included, and leaves its time stamps alone: it is a
change of vocabulary, not of what is known, and git keeps the old name. A
drop retracts the current records that use the dropped key or relation, so
they stay in history.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


from bibliotheke import store, timeutil
from bibliotheke.datamodel import FACT_TYPES, DataModel, EdgeSpec, FactSpec, ModelError, NodeSpec
from bibliotheke.model import Node

ADDITIVE, DESTRUCTIVE, NEUTRAL = "additive", "destructive", "neutral"
KINDS = (DESTRUCTIVE, ADDITIVE, NEUTRAL)


@dataclass(frozen=True)
class Change:
    kind: str  # additive, destructive or neutral
    where: str  # "word.lang", "translates_to", "time"
    what: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "where": self.where, "what": self.what}


# -- edits to the model dict (whatever layout holds it on disk) ------------


def _checked(marker: dict[str, Any]) -> dict[str, Any]:
    """The marker, once it parses as a model, without empty sections."""
    DataModel.from_marker(marker)
    for spec in marker["nodes"].values():
        if spec is not None and spec.get("facts") == {}:
            del spec["facts"]
    for key in ("edges", "vocabularies"):
        if marker.get(key) == {}:
            del marker[key]
    return marker


def _nodes(marker: dict[str, Any]) -> dict[str, Any]:
    if marker.get("nodes") is None:
        raise ModelError("this graph has no data model; `bib model add node <type>` starts one")
    return marker["nodes"]


def _node(marker: dict[str, Any], type: str) -> dict[str, Any]:
    nodes = _nodes(marker)
    if type not in nodes:
        raise ModelError(f"the model has no node type {type!r} (known: {', '.join(sorted(nodes))})")
    if nodes[type] is None:
        nodes[type] = {}
    return nodes[type]


def _edges(marker: dict[str, Any]) -> dict[str, Any]:
    _nodes(marker)
    if marker.get("edges") is None:
        marker["edges"] = {}
    return marker["edges"]


def _edge(marker: dict[str, Any], rel: str) -> dict[str, Any]:
    edges = _edges(marker)
    if rel not in edges:
        raise ModelError(f"the model has no edge {rel!r} (known: {', '.join(sorted(edges)) or 'none'})")
    return edges[rel]


def _facts(spec: dict[str, Any]) -> dict[str, Any]:
    if spec.get("facts") is None:
        spec["facts"] = {}
    return spec["facts"]


def _fact(marker: dict[str, Any], type: str, key: str) -> dict[str, Any]:
    facts = _facts(_node(marker, type))
    if key not in facts:
        raise ModelError(f"a {type} has no fact {key!r} (known: {', '.join(sorted(facts)) or 'none'})")
    return facts


def _types(given: Any) -> list[str]:
    return [given] if isinstance(given, str) else list(given or [])


# -- add -----------------------------------------------------------------------


def add_node_type(
    marker: dict[str, Any], type: str, *, description: str = "", id: str | None = None, body: str = ""
) -> dict[str, Any]:
    """A new node type. On a graph without a model this starts one, and the
    nodes already there are then checked against it."""
    marker = copy.deepcopy(marker)
    if marker.get("nodes") is None:
        marker["nodes"] = {}
    if type in marker["nodes"]:
        raise ModelError(f"the model already has a node type {type!r}")
    spec: dict[str, Any] = {}
    if description:
        spec["description"] = description
    if id:
        spec["id"] = id
    if body:
        spec["body"] = body
    marker["nodes"][type] = spec
    return _checked(marker)


def add_fact(
    marker: dict[str, Any],
    type: str,
    key: str,
    *,
    kind: str = "text",
    required: bool = False,
    many: bool = False,
    values: list[str] | str | None = None,
    description: str = "",
) -> dict[str, Any]:
    """A new fact on a node type. ``values`` is the list for an enum, or the
    name of a vocabulary."""
    marker = copy.deepcopy(marker)
    facts = _facts(_node(marker, type))
    if key in facts:
        raise ModelError(f"a {type} already has a fact {key!r}")
    if kind not in FACT_TYPES:
        raise ModelError(f"a fact type must be one of {', '.join(FACT_TYPES)}, not {kind!r}")
    if values and kind != "enum":
        raise ModelError("only an enum takes values")
    spec: dict[str, Any] = {"type": kind}
    if values:
        spec["values"] = values
    if required:
        spec["required"] = True
    if many:
        spec["many"] = True
    if description:
        spec["description"] = description
    facts[key] = kind if spec == {"type": kind} else spec
    return _checked(marker)


def add_edge(
    marker: dict[str, Any],
    rel: str,
    sources: list[str],
    targets: list[str],
    *,
    symmetric: bool = False,
    required: bool = False,
    many: bool = True,
    description: str = "",
) -> dict[str, Any]:
    marker = copy.deepcopy(marker)
    edges = _edges(marker)
    if rel in edges:
        raise ModelError(f"the model already has an edge {rel!r}")
    spec: dict[str, Any] = {"from": _one_or_list(sources), "to": _one_or_list(targets)}
    if symmetric:
        spec["symmetric"] = True
    if required:
        spec["required"] = True
    if not many:
        spec["many"] = False
    if description:
        spec["description"] = description
    edges[rel] = spec
    return _checked(marker)


def add_vocabulary(marker: dict[str, Any], name: str, values: list[str]) -> dict[str, Any]:
    marker = copy.deepcopy(marker)
    if marker.get("vocabularies") is None:
        marker["vocabularies"] = {}
    if name in marker["vocabularies"]:
        raise ModelError(f"the model already has a vocabulary {name!r}")
    if not values:
        raise ModelError("a vocabulary needs values")
    marker["vocabularies"][name] = list(values)
    return _checked(marker)


def _one_or_list(types: list[str]) -> str | list[str]:
    return types[0] if len(types) == 1 else list(types)


# -- rename ----------------------------------------------------------------------


def rename_node_type(marker: dict[str, Any], old: str, new: str) -> dict[str, Any]:
    """Rename a node type, in the edges that name it too."""
    marker = copy.deepcopy(marker)
    nodes = _nodes(marker)
    _node(marker, old)
    if new in nodes:
        raise ModelError(f"the model already has a node type {new!r}")
    marker["nodes"] = {new if type == old else type: spec for type, spec in nodes.items()}
    for spec in (marker.get("edges") or {}).values():
        for end in ("from", "to"):
            types = _types(spec.get(end))
            if old in types:
                spec[end] = _one_or_list([new if t == old else t for t in types])
    return _checked(marker)


def rename_fact(marker: dict[str, Any], type: str, old: str, new: str) -> dict[str, Any]:
    """Rename a fact key, in the type's id template too."""
    marker = copy.deepcopy(marker)
    facts = _fact(marker, type, old)
    if new in facts:
        raise ModelError(f"a {type} already has a fact {new!r}")
    _node(marker, type)["facts"] = {new if key == old else key: spec for key, spec in facts.items()}
    _rename_placeholder(_node(marker, type), old, new)
    return _checked(marker)


def rename_edge(marker: dict[str, Any], old: str, new: str) -> dict[str, Any]:
    """Rename a relation, in the id templates that use it too."""
    marker = copy.deepcopy(marker)
    edges = _edges(marker)
    _edge(marker, old)
    if new in edges:
        raise ModelError(f"the model already has an edge {new!r}")
    marker["edges"] = {new if rel == old else rel: spec for rel, spec in edges.items()}
    for type in _types(edges[old].get("from")):
        if type in marker["nodes"]:
            _rename_placeholder(_node(marker, type), old, new)
    return _checked(marker)


def _rename_placeholder(spec: dict[str, Any], old: str, new: str) -> None:
    if spec.get("id"):
        spec["id"] = str(spec["id"]).replace(f"<{old}>", f"<{new}>")


# -- drop --------------------------------------------------------------------------


def drop_node_type(marker: dict[str, Any], type: str) -> dict[str, Any]:
    """Drop a node type and the edges that only go between dropped types."""
    marker = copy.deepcopy(marker)
    _node(marker, type)
    del marker["nodes"][type]
    for rel, spec in list((marker.get("edges") or {}).items()):
        ends = {end: [t for t in _types(spec.get(end)) if t != type] for end in ("from", "to")}
        if not ends["from"] or not ends["to"]:
            del marker["edges"][rel]
        else:
            spec["from"], spec["to"] = _one_or_list(ends["from"]), _one_or_list(ends["to"])
    return _checked(marker)


def drop_fact(marker: dict[str, Any], type: str, key: str) -> dict[str, Any]:
    marker = copy.deepcopy(marker)
    facts, spec = _fact(marker, type, key), _node(marker, type)
    if f"<{key}>" in str(spec.get("id") or ""):
        raise ModelError(f"the {type} id template {spec['id']!r} uses <{key}>; change the template first")
    del facts[key]
    return _checked(marker)


def drop_edge(marker: dict[str, Any], rel: str) -> dict[str, Any]:
    marker = copy.deepcopy(marker)
    _edge(marker, rel)
    del marker["edges"][rel]
    for type, spec in marker["nodes"].items():
        if spec and f"<{rel}>" in str(spec.get("id") or ""):
            raise ModelError(f"the {type} id template {spec['id']!r} uses <{rel}>; change the template first")
    return _checked(marker)


# -- data rewrites ---------------------------------------------------------------


def uses(nodes: dict[str, Node], *, type: str | None = None, key: str | None = None, rel: str | None = None) -> int:
    """How many current records (or nodes, for a type alone) use a fact key,
    a relation, or a node type."""
    count = 0
    for node in nodes.values():
        if type is not None and node.type != type:
            continue
        if key is None and rel is None:
            count += 1
            continue
        count += sum(1 for f in node.facts if key is not None and f.key == key and f.superseded_at is None)
        count += sum(1 for e in node.edges if rel is not None and e.rel == rel and e.superseded_at is None)
    return count


def rewrite_fact_key(nodes: dict[str, Node], root: Path, type: str, old: str, new: str) -> int:
    """Rename a key on every version of every fact of a type. Returns how many."""
    count = 0
    for node in nodes.values():
        if node.type != type:
            continue
        hits = [f for f in node.facts if f.key == old]
        for fact in hits:
            fact.key = new
        if hits:
            count += len(hits)
            store.save_node(root, node)
    return count


def rewrite_rel(nodes: dict[str, Node], root: Path, old: str, new: str) -> int:
    count = 0
    for node in nodes.values():
        hits = [e for e in node.edges if e.rel == old]
        for edge in hits:
            edge.rel = new
        if hits:
            count += len(hits)
            store.save_node(root, node)
    return count


def rewrite_node_type(nodes: dict[str, Node], root: Path, old: str, new: str) -> int:
    """Move every node of a type, its body too, to the new type's directory."""
    count = 0
    for node in nodes.values():
        if node.type != old:
            continue
        old_file, old_body = store.node_path(root, node), store.body_path(root, node)
        node.type = new
        store.save_node(root, node)
        if old_body.exists():
            old_body.rename(store.body_path(root, node))
        old_file.unlink(missing_ok=True)
        count += 1
    folder = root / store.NODES_DIR / old
    if folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()
    return count


def retract_all(
    nodes: dict[str, Node], root: Path, *, type: str | None = None, key: str | None = None, rel: str | None = None
) -> int:
    """Retract the current facts with a key (on one type) or edges with a
    relation; they stay in history."""
    stamp = timeutil.now()
    count = 0
    for node in nodes.values():
        if type is not None and node.type != type:
            continue
        hits = [
            r
            for r in (*node.facts, *node.edges)
            if r.superseded_at is None and ((key is not None and getattr(r, "key", None) == key) or (rel is not None and getattr(r, "rel", None) == rel))
        ]
        for record in hits:
            record.superseded_at = stamp
        if hits:
            count += len(hits)
            store.save_node(root, node)
    return count


# -- diff -------------------------------------------------------------------------


def diff_models(old: DataModel | None, new: DataModel | None) -> list[Change]:
    """Every difference between two models, classified."""
    if old is None and new is None:
        return []
    if old is None:
        return [Change(DESTRUCTIVE, "model", "a model was added; the data is now checked against it")]
    if new is None:
        return [Change(ADDITIVE, "model", "the model was removed; the graph accepts anything")]
    changes: list[Change] = []
    if old.description != new.description:
        changes.append(Change(NEUTRAL, "description", "changed"))
    if old.time != new.time:
        changes.append(Change(DESTRUCTIVE if new.time == "ignored" else ADDITIVE, "time", f"{old.time} → {new.time}"))
    for type in _in_order(old.nodes, new.nodes):
        if type not in old.nodes:
            changes.append(Change(ADDITIVE, type, "new node type"))
            # nothing of a new type exists yet, so even its required facts break nothing
            changes += [c for key, spec in (new.nodes[type].facts or {}).items() for c in _new_fact(f"{type}.{key}", spec, fresh=True)]
        elif type not in new.nodes:
            changes.append(Change(DESTRUCTIVE, type, "node type removed"))
        else:
            changes += _node_changes(type, old.nodes[type], new.nodes[type])
    for rel in _in_order(old.edges, new.edges):
        if rel not in old.edges:
            spec = new.edges[rel]
            breaks = spec.required and any(t in old.nodes for t in spec.sources)
            changes.append(Change(DESTRUCTIVE if breaks else ADDITIVE, rel, f"new edge ({_ends(spec)}){', required' if spec.required else ''}"))
        elif rel not in new.edges:
            changes.append(Change(DESTRUCTIVE, rel, "edge removed"))
        else:
            changes += _edge_changes(rel, old.edges[rel], new.edges[rel])
    return changes


def _in_order(old: dict, new: dict) -> list[str]:
    return [*new, *(name for name in old if name not in new)]


def _new_fact(where: str, spec: FactSpec, fresh: bool = False) -> Iterator[Change]:
    """A fact added to a type; ``fresh`` when the type itself is new."""
    breaks = spec.required and not fresh
    yield Change(DESTRUCTIVE if breaks else ADDITIVE, where, f"new fact ({_kind(spec)}){', required' if spec.required else ''}")


def _kind(spec: FactSpec) -> str:
    return f"enum: {', '.join(spec.values)}" if spec.type == "enum" else spec.type


def _ends(spec: EdgeSpec) -> str:
    return f"{'/'.join(spec.sources)} {'↔' if spec.symmetric else '→'} {'/'.join(spec.targets)}"


def _node_changes(type: str, a: NodeSpec, b: NodeSpec) -> Iterator[Change]:
    if a.id != b.id:
        yield Change(ADDITIVE if b.id is None else DESTRUCTIVE, type, f"id template {a.id!r} → {b.id!r}")
    if a.description != b.description or a.body != b.body:
        yield Change(NEUTRAL, type, "description changed")
    old, new = a.facts or {}, b.facts or {}
    for key in _in_order(old, new):
        where = f"{type}.{key}"
        if key not in old:
            yield from _new_fact(where, new[key])
        elif key not in new:
            yield Change(DESTRUCTIVE, where, "fact removed")
        else:
            yield from _fact_changes(where, old[key], new[key])


def _fact_changes(where: str, a: FactSpec, b: FactSpec) -> Iterator[Change]:
    if a.type != b.type:
        yield Change(DESTRUCTIVE, where, f"type {a.type} → {b.type}")
    if a.required != b.required:
        yield Change(DESTRUCTIVE if b.required else ADDITIVE, where, "now required" if b.required else "no longer required")
    if a.many != b.many:
        yield Change(ADDITIVE if b.many else DESTRUCTIVE, where, "now takes many values" if b.many else "now takes one value")
    if a.type == b.type == "enum":
        if added := [v for v in b.values if v not in a.values]:
            yield Change(ADDITIVE, where, f"values added: {', '.join(added)}")
        if removed := [v for v in a.values if v not in b.values]:
            yield Change(DESTRUCTIVE, where, f"values removed: {', '.join(removed)}")
    if a.description != b.description:
        yield Change(NEUTRAL, where, "description changed")


def _edge_changes(rel: str, a: EdgeSpec, b: EdgeSpec) -> Iterator[Change]:
    for end, label in (("sources", "from"), ("targets", "to")):
        before, after = getattr(a, end), getattr(b, end)
        if added := [t for t in after if t not in before]:
            yield Change(ADDITIVE, rel, f"{label} types added: {', '.join(added)}")
        if removed := [t for t in before if t not in after]:
            yield Change(DESTRUCTIVE, rel, f"{label} types removed: {', '.join(removed)}")
    if a.symmetric != b.symmetric:
        yield Change(DESTRUCTIVE, rel, "now symmetric" if b.symmetric else "no longer symmetric")
    if a.required != b.required:
        yield Change(DESTRUCTIVE if b.required else ADDITIVE, rel, "now required" if b.required else "no longer required")
    if a.many != b.many:
        yield Change(ADDITIVE if b.many else DESTRUCTIVE, rel, "now takes many targets" if b.many else "now takes one target")
    if a.description != b.description:
        yield Change(NEUTRAL, rel, "description changed")
