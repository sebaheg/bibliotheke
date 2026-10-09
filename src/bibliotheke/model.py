"""Nodes, facts and edges, and their plain-dict form for YAML."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bibliotheke import timeutil

TIME_FIELDS = ("valid_from", "valid_to", "known_at", "changed_at", "superseded_at")


@dataclass(kw_only=True)
class Record:
    """One version of a fact or edge, carrying all three time axes.

    Versions of the same record share an ``id``; an update appends a new
    version and stamps ``superseded_at`` on the previous one.
    """

    id: str
    valid_from: str | None = None
    valid_to: str | None = None
    known_at: str
    changed_at: str
    superseded_at: str | None = None

    def _times(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in TIME_FIELDS}


@dataclass(kw_only=True)
class Fact(Record):
    key: str
    value: Any

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "key": self.key, "value": self.value, **self._times()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Fact:
        return cls(id=str(data["id"]), key=str(data["key"]), value=data.get("value"), **_times(data))


@dataclass(kw_only=True)
class Edge(Record):
    rel: str
    to: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "rel": self.rel, "to": self.to, **self._times()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Edge:
        return cls(id=str(data["id"]), rel=str(data["rel"]), to=str(data["to"]), **_times(data))


@dataclass(kw_only=True)
class Node:
    id: str
    type: str
    name: str
    notes: str = ""
    facts: list[Fact] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "notes": self.notes,
            "facts": [f.to_dict() for f in self.facts],
            "edges": [e.to_dict() for e in self.edges],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Node:
        return cls(
            id=str(data["id"]),
            type=str(data["type"]),
            name=str(data.get("name") or data["id"]),
            notes=str(data.get("notes") or ""),
            facts=[Fact.from_dict(f) for f in data.get("facts") or []],
            edges=[Edge.from_dict(e) for e in data.get("edges") or []],
        )


def _times(data: dict[str, Any]) -> dict[str, str | None]:
    return {name: timeutil.canonical(data.get(name)) for name in TIME_FIELDS}
