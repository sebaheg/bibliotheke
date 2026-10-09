"""The data model: an optional description, in ``bibliotheke.yaml``, of what a
graph holds. ``nodes:`` lists the node types and their facts, ``edges:`` the
relations allowed between them. When a graph has a model, writes that break
it are refused and ``bib check`` reports hand edits that do; a graph without
one accepts anything.

    description: A multilingual vocabulary.
    time: ignored               # or tracked (default)
    vocabularies:
      lang: [en, sv]
    nodes:
      word:
        description: A single word in one language.
        id: "<name>-<lang>"
        body: Usage notes.
        facts:
          lang: {type: enum, values: lang, required: true}
          example: {type: text, many: true}
    edges:
      translates_to: {from: word, to: word, symmetric: true}
      pos: {from: word, to: part_of_speech, required: true, many: false}

An id template's placeholders name a fact key or, failing that, an edge
whose target id fills it in. Facts take one value unless ``many: true``;
edges take many targets unless ``many: false``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from bibliotheke import timeutil
from bibliotheke.model import Edge, Fact, Node, Record

FACT_TYPES = ("text", "int", "number", "bool", "time", "url", "file", "enum")
TIME_MODES = ("tracked", "ignored")

_PLACEHOLDER = re.compile(r"<([^<>]+)>")
_TRUE = {"true", "yes", "1"}
_FALSE = {"false", "no", "0"}
_MIN = datetime.min.replace(tzinfo=timezone.utc)
_MAX = datetime.max.replace(tzinfo=timezone.utc)


class ModelError(ValueError):
    pass


@dataclass(frozen=True)
class FactSpec:
    key: str
    type: str = "text"
    required: bool = False
    many: bool = False
    values: tuple[str, ...] = ()
    vocabulary: str | None = None
    description: str = ""


@dataclass(frozen=True)
class NodeSpec:
    type: str
    description: str = ""
    id: str | None = None
    body: str = ""
    facts: dict[str, FactSpec] | None = None


@dataclass(frozen=True)
class EdgeSpec:
    rel: str
    sources: tuple[str, ...]
    targets: tuple[str, ...]
    symmetric: bool = False
    required: bool = False
    many: bool = True
    description: str = ""

    def allows(self, src_type: str, dst_type: str) -> bool:
        if src_type in self.sources and dst_type in self.targets:
            return True
        return self.symmetric and src_type in self.targets and dst_type in self.sources


@dataclass(frozen=True)
class DataModel:
    description: str
    time: str
    vocabularies: dict[str, tuple[str, ...]]
    nodes: dict[str, NodeSpec]
    edges: dict[str, EdgeSpec]

    @classmethod
    def from_marker(cls, marker: dict[str, Any]) -> DataModel | None:
        """The model in a parsed ``bibliotheke.yaml``, or None when it has no
        ``nodes:`` section."""
        if marker.get("nodes") is None:
            if marker.get("edges") is not None:
                raise ModelError("bibliotheke.yaml: `edges:` needs a `nodes:` section")
            return None
        time = str(marker.get("time") or "tracked")
        if time not in TIME_MODES:
            raise ModelError(f"bibliotheke.yaml: time must be one of {', '.join(TIME_MODES)}, not {time!r}")
        vocabularies = {
            str(name): tuple(str(v) for v in _list(values, f"vocabularies.{name}"))
            for name, values in _mapping(marker.get("vocabularies"), "vocabularies").items()
        }
        nodes = {
            str(type): _node_spec(str(type), raw, vocabularies)
            for type, raw in _mapping(marker["nodes"], "nodes").items()
        }
        edges = {
            str(rel): _edge_spec(str(rel), raw, nodes)
            for rel, raw in _mapping(marker.get("edges"), "edges").items()
        }
        for spec in nodes.values():
            for key in _PLACEHOLDER.findall(spec.id or ""):
                if key != "name" and key not in (spec.facts or {}) and not (
                    key in edges and spec.type in edges[key].sources
                ):
                    raise ModelError(
                        f"bibliotheke.yaml: nodes.{spec.type}.id: <{key}> is not name, a fact or an edge of {spec.type}"
                    )
        return cls(
            description=str(marker.get("description") or "").strip(),
            time=time,
            vocabularies=vocabularies,
            nodes=nodes,
            edges=edges,
        )

    # -- checks used on every write -----------------------------------------

    def node_spec(self, type: str) -> NodeSpec:
        if type not in self.nodes:
            raise ModelError(f"the model has no node type {type!r} (known: {_names(self.nodes)})")
        return self.nodes[type]

    def fact_value(self, type: str, key: str, value: Any) -> Any:
        """The value as the model stores it, e.g. ``"3"`` as ``3`` for an int."""
        facts = self.node_spec(type).facts or {}
        if key not in facts:
            raise ModelError(f"a {type} has no fact {key!r} (known: {_names(facts) or 'none'})")
        return _coerce(facts[key], value, f"{type}.{key}")

    def edge_spec(self, rel: str, src_type: str, dst_type: str) -> EdgeSpec:
        if rel not in self.edges:
            raise ModelError(f"the model has no edge {rel!r} (known: {_names(self.edges) or 'none'})")
        spec = self.edges[rel]
        if not spec.allows(src_type, dst_type):
            raise ModelError(
                f"{rel} goes from {'/'.join(spec.sources)} to {'/'.join(spec.targets)}, "
                f"not from {src_type} to {dst_type}"
            )
        return spec

    def times(self, valid_from: Any, valid_to: Any) -> None:
        if self.time == "ignored" and (valid_from is not None or valid_to is not None):
            raise ModelError("this graph's model sets `time: ignored`, so records take no valid_from or valid_to")

    def clash(self, type: str, key: str, new: Record, value: Any, current: list[Fact]) -> Fact | None:
        """A current fact that a new single-valued fact would contradict."""
        spec = (self.node_spec(type).facts or {}).get(key)
        if spec is None or spec.many:
            return None
        for fact in current:
            if fact.key == key and fact.value != value and overlaps(fact, new):
                return fact
        return None

    def edge_clash(self, rel: str, new: Record, dst: str, current: list[Edge]) -> Edge | None:
        """A current edge that a new edge of a single-target relation would contradict."""
        spec = self.edges.get(rel)
        if spec is None or spec.many:
            return None
        for edge in current:
            if edge.rel == rel and edge.to != dst and overlaps(edge, new):
                return edge
        return None

    def render_id(self, type: str, name: str, lookup: Callable[[str], Any]) -> str | None:
        """The id the type's ``id`` template gives, or None when there is no
        template or a placeholder has no value. ``<name>`` is the node name;
        any other placeholder is a fact key or an edge, looked up with
        ``lookup`` (a fact's value or an edge's target id)."""
        from bibliotheke.graph import slugify

        template = self.nodes[type].id if type in self.nodes else None
        if not template:
            return None
        parts = {}
        for key in _PLACEHOLDER.findall(template):
            value = name if key == "name" else lookup(key)
            if value in (None, ""):
                return None
            parts[key] = slugify(str(value))
        return slugify(_PLACEHOLDER.sub(lambda m: parts[m[1]], template))

    # -- whole-graph check ----------------------------------------------------

    def problems(self, nodes: dict[str, Node], root: Path) -> list[str]:
        """Everything in the files that the model does not allow."""
        found = []
        current_edges = {(n.id, e.rel, e.to) for n in nodes.values() for e in n.edges if e.superseded_at is None}
        for node in nodes.values():
            if node.type not in self.nodes:
                found.append(f"{node.id}: the model has no node type {node.type!r}")
                continue
            spec = self.nodes[node.type]
            facts = [f for f in node.facts if f.superseded_at is None]
            edges = [e for e in node.edges if e.superseded_at is None]
            for fact in facts:
                try:
                    self.fact_value(node.type, fact.key, fact.value)
                except ModelError as error:
                    found.append(f"{node.id}: fact {fact.id}: {error}")
                    continue
                if (spec.facts or {})[fact.key].type == "file" and not (root / str(fact.value)).exists():
                    found.append(f"{node.id}: fact {fact.id} names missing file {fact.value!r}")
                if (other := self.clash(node.type, fact.key, fact, fact.value, facts)) and other.id < fact.id:
                    found.append(f"{node.id}: {fact.key} has more than one value at once ({other.id}, {fact.id})")
            for key, fact_spec in (spec.facts or {}).items():
                if fact_spec.required and not any(f.key == key for f in facts):
                    found.append(f"{node.id}: missing required fact {key!r}")
            for rel, edge_spec in self.edges.items():
                if edge_spec.required and node.type in edge_spec.sources and not any(e.rel == rel for e in edges):
                    found.append(f"{node.id}: missing required edge {rel!r}")
            if self.time == "ignored":
                for record in (*facts, *edges):
                    if record.valid_from or record.valid_to:
                        found.append(f"{node.id}: {record.id} has valid time, but the model sets `time: ignored`")
            for edge in edges:
                if edge.to not in nodes:
                    continue
                try:
                    edge_spec = self.edge_spec(edge.rel, node.type, nodes[edge.to].type)
                except ModelError as error:
                    found.append(f"{node.id}: edge {edge.id}: {error}")
                    continue
                if edge_spec.symmetric and node.id < edge.to and (edge.to, edge.rel, node.id) in current_edges:
                    found.append(f"{node.id}: {edge.rel} {edge.to} is stored in both directions, but it is symmetric")
                if (other := self.edge_clash(edge.rel, edge, edge.to, edges)) and other.id < edge.id:
                    found.append(f"{node.id}: {edge.rel} has more than one target at once ({other.id}, {edge.id})")
            latest = {e.rel: e.to for e in edges} | {f.key: f.value for f in facts}
            expected = self.render_id(node.type, node.name, latest.get)
            if expected and expected != node.id:
                found.append(f"{node.id}: id should be {expected!r} (template {spec.id!r})")
        return found

    # -- display ------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        def fact(spec: FactSpec) -> dict[str, Any]:
            data: dict[str, Any] = {"type": spec.type}
            if spec.values:
                data["values"] = spec.vocabulary or list(spec.values)
            for name in ("required", "many"):
                if getattr(spec, name):
                    data[name] = True
            if spec.description:
                data["description"] = spec.description
            return data

        return {
            "description": self.description,
            "time": self.time,
            "vocabularies": {name: list(values) for name, values in self.vocabularies.items()},
            "nodes": {
                type: {
                    "description": spec.description,
                    "id": spec.id,
                    "body": spec.body,
                    "facts": {key: fact(f) for key, f in (spec.facts or {}).items()},
                }
                for type, spec in self.nodes.items()
            },
            "edges": {
                rel: {
                    "from": list(spec.sources),
                    "to": list(spec.targets),
                    "symmetric": spec.symmetric,
                    "required": spec.required,
                    "many": spec.many,
                    "description": spec.description,
                }
                for rel, spec in self.edges.items()
            },
        }


def overlaps(a: Record, b: Record) -> bool:
    """Whether two records' valid periods share any time; no bound means forever."""

    def span(r: Record) -> tuple[datetime, datetime]:
        lo = timeutil.start(r.valid_from) if r.valid_from else _MIN
        hi = timeutil.end(r.valid_to) if r.valid_to else _MAX
        return lo, hi

    (a_lo, a_hi), (b_lo, b_hi) = span(a), span(b)
    return a_lo < b_hi and b_lo < a_hi


def _node_spec(type: str, raw: Any, vocabularies: dict[str, tuple[str, ...]]) -> NodeSpec:
    from bibliotheke.graph import slugify

    if slugify(type) != type:
        raise ModelError(f"bibliotheke.yaml: node type {type!r} must be lowercase with hyphens, e.g. {slugify(type)!r}")
    raw = _mapping(raw, f"nodes.{type}")
    facts = {}
    for key, given in _mapping(raw.get("facts"), f"nodes.{type}.facts").items():
        where = f"nodes.{type}.facts.{key}"
        given = {"type": given} if isinstance(given, str) else _mapping(given, where)
        kind = str(given.get("type") or "text")
        if kind not in FACT_TYPES:
            raise ModelError(f"bibliotheke.yaml: {where}: type must be one of {', '.join(FACT_TYPES)}, not {kind!r}")
        values, vocabulary = (), None
        if kind == "enum":
            named = given.get("values")
            if isinstance(named, str):
                if named not in vocabularies:
                    raise ModelError(f"bibliotheke.yaml: {where}: no vocabulary {named!r}")
                values, vocabulary = vocabularies[named], named
            else:
                values = tuple(str(v) for v in _list(named, f"{where}.values"))
            if not values:
                raise ModelError(f"bibliotheke.yaml: {where}: an enum needs `values`")
        facts[str(key)] = FactSpec(
            key=str(key),
            type=kind,
            required=bool(given.get("required")),
            many=bool(given.get("many")),
            values=values,
            vocabulary=vocabulary,
            description=str(given.get("description") or ""),
        )
    return NodeSpec(
        type=type,
        description=str(raw.get("description") or "").strip(),
        id=str(raw["id"]) if raw.get("id") else None,
        body=str(raw.get("body") or "").strip(),
        facts=facts,
    )


def _edge_spec(rel: str, raw: Any, nodes: dict[str, NodeSpec]) -> EdgeSpec:
    raw = _mapping(raw, f"edges.{rel}")
    ends = {}
    for end in ("from", "to"):
        types = raw.get(end)
        types = tuple(str(t) for t in ([types] if isinstance(types, str) else _list(types, f"edges.{rel}.{end}")))
        if not types:
            raise ModelError(f"bibliotheke.yaml: edges.{rel} needs `{end}`")
        for type in types:
            if type not in nodes:
                raise ModelError(f"bibliotheke.yaml: edges.{rel}.{end}: no node type {type!r}")
        ends[end] = types
    return EdgeSpec(
        rel=rel,
        sources=ends["from"],
        targets=ends["to"],
        symmetric=bool(raw.get("symmetric")),
        required=bool(raw.get("required")),
        many=bool(raw.get("many", True)),
        description=str(raw.get("description") or "").strip(),
    )


def _coerce(spec: FactSpec, value: Any, where: str) -> Any:
    def bad(expected: str) -> ModelError:
        return ModelError(f"{where} is {'an' if expected[0] in 'aeiou' else 'a'} {expected}, not {value!r}")

    text = str(value).strip()
    if spec.type == "text":
        return value if isinstance(value, str) else text
    if spec.type == "int":
        if isinstance(value, bool) or not re.fullmatch(r"[+-]?\d+", text):
            raise bad("int")
        return int(text)
    if spec.type == "number":
        try:
            return value if isinstance(value, (int, float)) and not isinstance(value, bool) else float(text)
        except ValueError:
            raise bad("number") from None
    if spec.type == "bool":
        if isinstance(value, bool):
            return value
        if text.lower() in _TRUE | _FALSE:
            return text.lower() in _TRUE
        raise bad("bool")
    if spec.type == "time":
        try:
            return timeutil.canonical(value)
        except ValueError:
            raise bad("time (YYYY, YYYY-MM, YYYY-MM-DD or ISO timestamp)") from None
    if spec.type == "url":
        if not re.match(r"^https?://\S+$", text):
            raise bad("url")
        return text
    if spec.type == "file":
        return text
    if text not in spec.values:
        raise ModelError(f"{where} must be one of {', '.join(spec.values)}, not {value!r}")
    return text


def _mapping(value: Any, where: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ModelError(f"bibliotheke.yaml: {where} must be a mapping")
    return value


def _list(value: Any, where: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ModelError(f"bibliotheke.yaml: {where} must be a list")
    return value


def _names(mapping: dict) -> str:
    return ", ".join(sorted(mapping))
