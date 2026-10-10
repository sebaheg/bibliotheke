"""The on-disk layout: ``bibliotheke.yaml`` marks a graph root, and each node
is one file at ``nodes/<type>/<id>.yaml``, with optional long-form Markdown
next to it at ``nodes/<type>/<id>.md``.

The data model lives either in ``bibliotheke.yaml`` itself (``nodes:``,
``edges:``, ``vocabularies:``) or, so that each node type is one file to
read on GitHub, in a ``model/`` folder: ``model/<type>.yaml`` holds the
type's facts and, under ``edges:``, the relations that go out from it, and
``model/vocabularies.yaml`` holds the shared value lists. The two forms
are never mixed. ``load_model`` reads either into the same dict; that is what
a ``Graph.marker`` holds.

A graph is read through a ``Source``: the working tree, or a git revision
(``GitTree``) read straight from the repository without a checkout."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol

import yaml

from bibliotheke.datamodel import DEFAULT_NAME
from bibliotheke.model import Node, TIME_FIELDS

ROOT_MARKER = "bibliotheke.yaml"
NODES_DIR = "nodes"
MODEL_DIR = "model"
VOCABULARIES = "vocabularies"  # model/vocabularies.yaml
MODEL_KEYS = ("description", "time", "vocabularies", "nodes", "edges")
FOLDER, FILE = "folder", "file"  # the two layouts a model can have
SCHEMA_VERSION = 1


class Source(Protocol):
    """Where a graph's files are read from. Paths are relative to the root,
    with forward slashes."""

    root: Path
    writable: bool

    def read_text(self, rel: str) -> str: ...

    def read_many(self, rels: Iterable[str]) -> dict[str, str]: ...

    def exists(self, rel: str) -> bool: ...

    def files(self, prefix: str, suffix: str) -> list[str]:
        """Every file under ``prefix`` ending in ``suffix``, in path order."""
        ...


@dataclass
class WorkingTree:
    root: Path
    writable: bool = True

    def read_text(self, rel: str) -> str:
        return (self.root / rel).read_text()

    def read_many(self, rels: Iterable[str]) -> dict[str, str]:
        return {rel: self.read_text(rel) for rel in rels}

    def exists(self, rel: str) -> bool:
        return (self.root / rel).exists()

    def files(self, prefix: str, suffix: str) -> list[str]:
        found = (self.root / prefix).glob(f"**/*{suffix}")
        return _in_path_order(p.relative_to(self.root).as_posix() for p in found if p.is_file())


class GitTree:
    """The graph as committed at ``rev``. The graph may sit anywhere inside
    the repository; the whole tree is listed once and files are fetched in
    one batch, so a load costs a handful of git calls however many nodes."""

    writable = False

    def __init__(self, root: Path, rev: str) -> None:
        self.root = root
        self.rev = rev
        lines = self._git("rev-parse", "--show-toplevel", "--show-prefix").split("\n")
        self.prefix = lines[1] if len(lines) > 1 else ""
        try:
            self.commit = self._git("rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
        except ValueError:
            raise ValueError(f"no commit {rev!r} in the repository at {lines[0]}") from None
        listing = self._git("ls-tree", "-r", "-z", "--full-tree", self.commit, "--", *([self.prefix] if self.prefix else []))
        self._blobs: dict[str, str] = {}
        for entry in listing.split("\0"):
            if entry:
                meta, path = entry.split("\t", 1)
                self._blobs[path.removeprefix(self.prefix)] = meta.split()[2]
        self._texts: dict[str, str] = {}

    def _git(self, *args: str, data: bytes | None = None) -> str:
        done = subprocess.run(["git", *args], cwd=self.root, input=data, capture_output=True, check=False)
        if done.returncode:
            raise ValueError(done.stderr.decode().strip() or f"git {args[0]} failed")
        return done.stdout.decode().rstrip("\n")

    def read_text(self, rel: str) -> str:
        if rel not in self._texts:
            self._texts.update(self.read_many([rel]))
        return self._texts[rel]

    def read_many(self, rels: Iterable[str]) -> dict[str, str]:
        wanted = [rel for rel in rels if rel not in self._texts]
        missing = [rel for rel in wanted if rel not in self._blobs]
        if missing:
            raise FileNotFoundError(f"{missing[0]} is not in {self.rev}")
        if wanted:
            shas = "\n".join(self._blobs[rel] for rel in wanted) + "\n"
            done = subprocess.run(["git", "cat-file", "--batch"], cwd=self.root, input=shas.encode(), capture_output=True, check=False)
            if done.returncode:
                raise ValueError(done.stderr.decode().strip() or "git cat-file failed")
            out, pos = done.stdout, 0
            for rel in wanted:
                header_end = out.index(b"\n", pos)
                size = int(out[pos:header_end].split()[2])
                pos = header_end + 1
                self._texts[rel] = out[pos : pos + size].decode()
                pos += size + 1
        return {rel: self._texts[rel] for rel in rels}

    def exists(self, rel: str) -> bool:
        return rel in self._blobs

    def files(self, prefix: str, suffix: str) -> list[str]:
        return _in_path_order(rel for rel in self._blobs if rel.startswith(prefix) and rel.endswith(suffix))


@dataclass
class Memory:
    """A graph that lives only in memory, e.g. one built from a git log.
    ``texts`` maps relative paths (bodies, mostly) to their contents.
    Nothing is written back."""

    root: Path = Path(".")
    texts: dict[str, str] = field(default_factory=dict)
    writable: bool = False
    rev: str = "memory"

    def read_text(self, rel: str) -> str:
        try:
            return self.texts[rel]
        except KeyError:
            raise FileNotFoundError(rel) from None

    def read_many(self, rels: Iterable[str]) -> dict[str, str]:
        return {rel: self.read_text(rel) for rel in rels}

    def exists(self, rel: str) -> bool:
        return rel in self.texts

    def files(self, prefix: str, suffix: str) -> list[str]:
        return _in_path_order(rel for rel in self.texts if rel.startswith(prefix) and rel.endswith(suffix))


def _in_path_order(rels: Iterable[str]) -> list[str]:
    """Sorted directory by directory, as ``Path`` objects sort."""
    return sorted(rels, key=lambda rel: rel.split("/"))


def _source(where: Path | Source) -> Source:
    return WorkingTree(where) if isinstance(where, Path) else where

_INT = re.compile(r"^\d+$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}Z)?$")


class _Time(str):
    """A time value, written unquoted (``2013``, not ``'2013'``)."""


class _Dumper(yaml.SafeDumper):
    pass


def _represent_time(dumper: yaml.SafeDumper, value: _Time) -> yaml.Node:
    if _INT.match(value):
        return dumper.represent_scalar("tag:yaml.org,2002:int", str(int(value)))
    if _TIMESTAMP.match(value):
        return dumper.represent_scalar("tag:yaml.org,2002:timestamp", str(value))
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(value))


def _represent_str(dumper: yaml.SafeDumper, value: str) -> yaml.Node:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(_Time, _represent_time)
_Dumper.add_representer(str, _represent_str)


def find_root(start: Path | None = None) -> Path | None:
    """The nearest directory at or above ``start`` that holds a graph."""
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / ROOT_MARKER).is_file():
            return candidate
    return None


def init_root(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    marker = path / ROOT_MARKER
    if not marker.exists():
        marker.write_text(yaml.safe_dump({"schema_version": SCHEMA_VERSION}))
    (path / NODES_DIR).mkdir(exist_ok=True)
    return path


def node_rel(node: Node) -> str:
    """A node's file, relative to the graph root."""
    return f"{NODES_DIR}/{node.type}/{node.id}.yaml"


def body_rel(node: Node) -> str:
    return f"{NODES_DIR}/{node.type}/{node.id}.md"


def node_path(root: Path, node: Node) -> Path:
    return root / node_rel(node)


def load_marker(where: Path | Source) -> dict:
    """The parsed ``bibliotheke.yaml``."""
    source = _source(where)
    try:
        marker = yaml.safe_load(source.read_text(ROOT_MARKER)) or {}
    except FileNotFoundError:
        raise ValueError(f"no {ROOT_MARKER} at {getattr(source, 'rev', source.root)}") from None
    except yaml.YAMLError as err:
        raise ValueError(f"{source.root / ROOT_MARKER}: {err}") from err
    if not isinstance(marker, dict):
        raise ValueError(f"{source.root / ROOT_MARKER}: must be a mapping")
    return marker


def load_model(where: Path | Source) -> tuple[dict, str | None]:
    """The marker with the model folded in, whichever layout holds it, and
    the layout: ``"folder"`` for ``model/``, ``"file"`` for a model in
    ``bibliotheke.yaml``, None when the graph has no model."""
    source = _source(where)
    marker = load_marker(source)
    rels = source.files(f"{MODEL_DIR}/", ".yaml")
    if not rels:
        return marker, FILE if marker.get("nodes") is not None else None
    if any(key in marker for key in ("nodes", "edges", "vocabularies")):
        raise ValueError(f"{source.root / ROOT_MARKER}: the model is defined both here and in {MODEL_DIR}/; keep one")
    return assemble_model(marker, source.read_many(rels)), FOLDER


def assemble_model(marker: dict, texts: dict[str, str]) -> dict:
    """The marker with a ``model/`` folder's files (``rel -> contents``) folded
    into ``vocabularies``, ``nodes`` and ``edges``. Node types come in file
    order; an edge's ``from`` defaults to the type whose file declares it."""
    nodes: dict[str, Any] = {}
    edges: dict[str, Any] = {}
    homes: dict[str, str] = {}
    vocabularies: Any = {}
    for rel in sorted(texts, key=lambda r: r.split("/")):
        name = rel[len(MODEL_DIR) + 1 : -len(".yaml")]
        if "/" in name:
            raise ValueError(f"{rel}: model files go directly in {MODEL_DIR}/")
        try:
            data = yaml.safe_load(texts[rel]) or {}
        except yaml.YAMLError as err:
            raise ValueError(f"{rel}: {err}") from err
        if not isinstance(data, dict):
            raise ValueError(f"{rel}: must be a mapping")
        if name == VOCABULARIES:
            vocabularies = data
            continue
        nodes[name] = {key: value for key, value in data.items() if key != "edges"}
        declared = data.get("edges") or {}
        if not isinstance(declared, dict):
            raise ValueError(f"{rel}: edges must be a mapping")
        for rel_name, raw in declared.items():
            if rel_name in edges:
                raise ValueError(f"{rel}: edge {rel_name!r} is also defined in {homes[rel_name]}")
            if isinstance(raw, dict) and "from" not in raw:
                raw = {"from": name, **raw}
            edges[str(rel_name)] = raw
            homes[str(rel_name)] = rel
    return {**marker, "vocabularies": vocabularies, "nodes": nodes, "edges": edges}


def model_files(marker: dict) -> dict[str, str]:
    """The ``model/`` folder for a marker's model: ``rel -> contents``. An edge
    goes in the file of its first ``from`` type, and ``from`` is left out
    when it is only that type."""
    files: dict[str, str] = {}
    by_type: dict[str, dict[str, Any]] = {type: {} for type in marker.get("nodes") or {}}
    for rel_name, raw in (marker.get("edges") or {}).items():
        raw = dict(raw) if isinstance(raw, dict) else {"from": raw}
        sources = raw.get("from")
        sources = [sources] if isinstance(sources, str) else list(sources or [])
        home = sources[0] if sources and sources[0] in by_type else next(iter(by_type), None)
        if home is None:
            continue
        if sources == [home]:
            del raw["from"]
        by_type[home][rel_name] = raw
    for type, spec in (marker.get("nodes") or {}).items():
        spec = dict(spec or {})
        # built-in fields first, so a file shows that a node has a name even with no facts
        data: dict[str, Any] = {"description": spec.pop("description", ""), "name": spec.pop("name", None) or DEFAULT_NAME}
        data.update(spec)
        if by_type[type]:
            data["edges"] = by_type[type]
        files[f"{MODEL_DIR}/{type}.yaml"] = _yaml(data)
    if marker.get("vocabularies"):
        files[f"{MODEL_DIR}/{VOCABULARIES}.yaml"] = _yaml(marker["vocabularies"])
    return files


def marker_text(marker: dict, layout: str = FILE) -> str:
    """``bibliotheke.yaml`` contents: ``schema_version`` first, the model
    sections in their documented order (left out in the folder layout, where
    only ``description`` and ``time`` stay), then anything else (``view:``,
    ``globe:``) as it was. Comments in the file are not kept."""
    ordered: dict[str, Any] = {"schema_version": marker.get("schema_version", SCHEMA_VERSION)}
    for key in MODEL_KEYS:
        if key in marker and not (layout == FOLDER and key in ("vocabularies", "nodes", "edges")):
            ordered[key] = marker[key].strip() if isinstance(marker[key], str) else marker[key]
    for key, value in marker.items():
        if key not in ("vocabularies", "nodes", "edges"):
            ordered.setdefault(key, value)
    return _yaml(ordered)


def save_model(root: Path, marker: dict, layout: str) -> list[str]:
    """Write a model in the given layout, removing what the other layout or
    a dropped type left behind. Returns the files written."""
    written = [ROOT_MARKER]
    (root / ROOT_MARKER).write_text(marker_text(marker, layout))
    folder = root / MODEL_DIR
    wanted = model_files(marker) if layout == FOLDER else {}
    for rel, text in wanted.items():
        folder.mkdir(exist_ok=True)
        (root / rel).write_text(text)
        written.append(rel)
    if folder.is_dir():
        for path in folder.glob("*.yaml"):
            if path.relative_to(root).as_posix() not in wanted:
                path.unlink()
        if not any(folder.iterdir()):
            folder.rmdir()
    return written


def _yaml(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100, default_flow_style=None)


def view_settings(where: Path | Source, section: str = "view") -> dict:
    """An optional display section of ``bibliotheke.yaml`` (``view:``, ``globe:``)."""
    settings = load_marker(where).get(section) or {}
    return settings if isinstance(settings, dict) else {}


def body_path(root: Path, node: Node) -> Path:
    return root / body_rel(node)


def load_nodes(where: Path | Source) -> dict[str, Node]:
    source = _source(where)
    nodes: dict[str, Node] = {}
    rels = source.files(f"{NODES_DIR}/", ".yaml")
    texts = source.read_many(rels)
    for rel in rels:
        try:
            node = Node.from_dict(yaml.safe_load(texts[rel]))
        except (yaml.YAMLError, KeyError, TypeError, ValueError) as err:
            raise ValueError(f"{source.root / rel}: {err}") from err
        if node.id in nodes:
            raise ValueError(f"{source.root / rel}: duplicate node id {node.id!r}")
        nodes[node.id] = node
    return nodes


def dump_node(node: Node) -> str:
    """A node's file contents. Deterministic: the same node always dumps to
    the same bytes, so a git diff shows only what changed."""
    data = node.to_dict()
    for record in (*data["facts"], *data["edges"]):
        for name in TIME_FIELDS:
            if record[name] is not None:
                record[name] = _Time(record[name])
    return yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)


def save_node(root: Path, node: Node) -> Path:
    path = node_path(root, node)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_node(node))
    return path
