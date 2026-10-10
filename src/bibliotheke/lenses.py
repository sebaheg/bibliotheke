"""Lenses declared in ``bibliotheke.yaml``: how a graph wants to be looked at.

The ``view:`` section holds defaults and, under ``lenses:``, any number of
named ways of drawing the graph, each answering one question. Every key
can sit at either level; a lens inherits what it does not set::

    view:
      theme: map
      colors: {paper: '#f0f0f4', concept: '#66d9a6'}   # category -> colour
      edges: {cites: '#c8ccd8'}                        # relation -> colour
      lenses:
        - name: Literature
          description: papers and concepts, and how they build on each other
          rels: [cites, builds_on, introduces, about]  # edges drawn; others not
          hide: [claim, city]                          # node types left out
          size_by: in:cites                            # degree, in:<rel>, out:<rel> or a numeric fact
          labels: {count: 8, types: [paper]}           # how many, and which types may carry one
          legend: [paper, system, concept]             # rows shown; false for no legend
        - name: Claims
          show: [claim, paper, article, video]         # the positive form of hide
          rels: [made_in]
          tree: made_in                                # a still tree instead of a force layout
          color_by: kind
          hide_unlinked: true                          # a node none of the drawn edges touch is left out

Nothing here names a type or relation bibliotheke knows about: the same
schema describes a literature graph and a repository. The first lens is the
default; ``bib view --lens NAME`` starts on another. This module parses and
validates the section into plain :class:`Spec` data (no display code is
imported, so ``bib check`` can report a lens that names a type the model
does not have) and builds :class:`~bibliotheke.scene.Lens` objects from it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Callable

from bibliotheke.model import Node

if TYPE_CHECKING:
    from bibliotheke.graph import Graph
    from bibliotheke.scene import Lens

SECTION = "view"
# Keys that describe one lens. All of them may also sit at the view level,
# where they are the defaults every lens starts from.
LENS_KEYS = {
    "name", "description", "rels", "show", "hide", "hide_unlinked", "tree", "layout", "color_by", "theme",
    "colors", "edges", "labels", "size_by", "legend", "min_parallel", "style", "place", "via", "arcs",
}
# Keys that belong to the view as a whole.
VIEW_KEYS = LENS_KEYS - {"name", "description"} | {"lenses", "valid_at"}
LAYOUTS = ("force", "tree", "globe")
GLOBE_NAME = "Globe"  # the lens a graph with coordinates gets when it declares none
DEFAULT_THEME = "plotui"  # see view.THEMES
DEFAULT_NAME = "Graph"
CUSTOM_NAME = "Custom"
# Theme fields a `style:` block may set (the look, not the colours, which
# have keys of their own).
STYLE_KEYS = {
    "edge_alpha", "edge_width", "fog", "halo", "size_min", "size_step", "size_max", "label_size", "styled",
    "globe",  # the Earth's look on a globe lens: dark, earth, light, blueprint or wire
}


class LensError(ValueError):
    """The ``view:`` section does not describe a lens."""


@dataclass(frozen=True)
class Spec:
    """One lens as declared: validated, plain data. ``hide`` already has
    ``show`` folded in; ``legend`` is None for every drawn category and
    empty for no legend at all; ``labels`` is None for the theme's number."""

    name: str
    description: str = ""
    rels: tuple[str, ...] | None = None
    hide: tuple[str, ...] = ()
    tree: str | None = None
    color_by: str = "type"
    theme: str = DEFAULT_THEME
    colors: dict[str, str] = field(default_factory=dict)
    edges: dict[str, str] = field(default_factory=dict)
    labels: int | None = None
    label_types: tuple[str, ...] | None = None
    size_by: str | None = None
    legend: tuple[str, ...] | None = None
    min_parallel: int = 1
    hide_unlinked: bool = False
    style: dict[str, Any] = field(default_factory=dict)
    # A globe lens (``layout: globe``): nodes of the ``place`` types are put
    # where they are, found through the ``via`` relations (both read from
    # the graph when not given, see geo.infer); ``arcs`` joins places
    # through a node shared by both (``shared``), along the edges of given
    # relations, or not at all (``none``).
    globe: bool = False
    place: tuple[str, ...] | None = None
    via: tuple[str, ...] | None = None
    arcs: str | tuple[str, ...] = "shared"

    @property
    def layout(self) -> str:
        return "globe" if self.globe else "tree" if self.tree else "force"


def section(graph: Graph) -> dict[str, Any]:
    """The ``view:`` section of the graph's marker, a dict even when absent."""
    settings = graph.marker.get(SECTION) or {}
    return settings if isinstance(settings, dict) else {}


def declared(graph: Graph) -> list[Spec]:
    """The lenses the graph declares, validated against its model (or, with
    no model, against what the graph holds). A graph that declares none
    gets one lens from the view-level keys, so every graph has a lens."""
    settings = section(graph)
    for key in settings:
        if key not in VIEW_KEYS:
            raise LensError(f"view: unknown key {key!r}; the keys are {', '.join(sorted(VIEW_KEYS))}")
    base = {key: value for key, value in settings.items() if key in LENS_KEYS}
    entries = settings.get("lenses")
    if entries is None:
        return _with_globe([_spec(base, DEFAULT_NAME, "view", graph)], base, graph)
    if not isinstance(entries, list) or not entries:
        raise LensError("view.lenses: a list of lenses, each with a name")
    specs = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict) or not str(entry.get("name") or "").strip():
            raise LensError(f"view.lenses[{i}]: a lens is a mapping with a name")
        name = str(entry["name"]).strip()
        where = f"view.lenses[{name}]"
        for key in entry:
            if key not in LENS_KEYS:
                raise LensError(f"{where}: unknown key {key!r}; the keys are {', '.join(sorted(LENS_KEYS))}")
        merged = {**base, **entry}
        # A lens that says what it shows (or hides) says all of it: the
        # view-level list of the other kind does not add to it.
        for own, other in (("show", "hide"), ("hide", "show")):
            if own in entry and other not in entry:
                merged.pop(other, None)
        specs.append(_spec(merged, name, where, graph))
    names = Counter(spec.name.lower() for spec in specs)
    if any(count > 1 for count in names.values()):
        twice = sorted(name for name, count in names.items() if count > 1)
        raise LensError(f"view.lenses: two lenses are named {', '.join(twice)}")
    return _with_globe(specs, base, graph)


def _with_globe(specs: list[Spec], base: dict[str, Any], graph: Graph) -> list[Spec]:
    """The specs, and a Globe lens at the end when the graph has coordinates
    and declares no globe lens of its own: what to place and how is read
    from the graph (or from an old ``globe:`` section's ``who`` and
    ``via``). It shares the view-level look, not another lens's relations."""
    from bibliotheke.geo import has_coordinates

    if any(spec.globe for spec in specs) or not has_coordinates(graph):
        return specs
    old = graph.marker.get("globe") if isinstance(graph.marker.get("globe"), dict) else {}
    look = {key: base[key] for key in ("theme", "colors", "color_by", "labels", "style") if key in base}
    raw: dict[str, Any] = {**look, "layout": "globe", "description": "where things are, and what joins the places"}
    if old.get("who"):
        raw["place"] = old["who"]
    if old.get("via"):
        raw["via"] = old["via"]
    if not old.get("who"):
        # Everything that is somewhere is placed, so nothing is left to link
        # places through a shared node; arcs are for a declared lens.
        raw["arcs"] = "none"
    if old.get("theme"):
        raw["style"] = {**raw.get("style", {}), "globe": old["theme"]}
    name = GLOBE_NAME if GLOBE_NAME.lower() not in {s.name.lower() for s in specs} else f"{GLOBE_NAME} (places)"
    try:
        return [*specs, _spec(raw, name, f"view.lenses[{name}]", graph)]
    except LensError:  # an old globe: section naming what the model lacks: no automatic lens
        return specs


def valid_at(graph: Graph) -> str | None:
    """The view's default valid time: ``now`` or a time value, or None.
    A repository wants ``now`` (deleted files gone); a literature graph
    wants everything, which is no filter at all."""
    from bibliotheke import timeutil

    value = section(graph).get("valid_at")
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() == "now":
        return timeutil.now()
    try:
        return timeutil.canonical(text)
    except ValueError as error:
        raise LensError(f"view.valid_at: {error}") from None


def problems(graph: Graph) -> list[str]:
    """What is wrong with the ``view:`` section, as ``bib check`` reports it."""
    try:
        declared(graph)
        valid_at(graph)
    except LensError as error:
        return [f"bibliotheke.yaml: {error}"]
    return []


def custom(like: Spec, rels: list[str] | None, tree: str | None = None, hide: list[str] | None = None) -> Spec:
    """A lens made on the command line (``--rel``, ``--tree``, ``--hide``),
    in the look of ``like``: the given relations, laid out as a tree when
    one is named, hiding what is asked."""
    return replace(
        like,
        name=CUSTOM_NAME,
        description=", ".join(rels or []) or like.description,
        rels=tuple(rels) if rels else like.rels,
        # A tree stays only while its relation is still drawn.
        tree=tree or (like.tree if not rels or like.tree in rels else None),
        hide=tuple(hide) if hide else like.hide,
        min_parallel=1 if rels else like.min_parallel,
    )


def pick(lenses: list, name: str) -> list:
    """The lenses rotated so the one whose name starts with ``name`` (any
    case) comes first: what ``bib view NAME`` does. ``globe`` also finds
    the first globe lens whatever it is called."""
    names = [lens.name.lower() for lens in lenses]
    matches = [i for i, each in enumerate(names) if each == name.lower()] or [
        i for i, each in enumerate(names) if each.startswith(name.lower())
    ]
    if not matches and name.lower() in ("globe", "map", "world"):
        matches = [i for i, lens in enumerate(lenses) if getattr(lens, "globe", False)][:1]
    if len(matches) != 1:
        hint = "" if any(getattr(lens, "globe", False) for lens in lenses) or name.lower() != "globe" else (
            " (no globe: no node has lat and lon facts)"
        )
        raise LensError(f"no lens {name!r}{hint}; the lenses are {', '.join(names)}")
    return lenses[matches[0] :] + lenses[: matches[0]]


def build(
    graph: Graph,
    specs: list[Spec],
    *,
    theme: str | None = None,
    color_by: str | None = None,
    group: Callable[[str], str] | None = None,
    label: Callable[[Node], str] | None = None,
    colors: dict[str, str] | None = None,
) -> list[Lens]:
    """:class:`~bibliotheke.scene.Lens` objects for the specs. ``theme`` and
    ``color_by`` are command-line overrides for every lens; ``group``,
    ``label`` and ``colors`` are hooks for what YAML cannot say (a legend
    row computed from the data, a label cut a particular way)."""
    from bibliotheke.scene import Lens, theme_for

    lenses = []
    for spec in specs:
        if theme:
            spec = replace(spec, theme=theme)
        if color_by:
            spec = replace(spec, color_by=color_by)
        try:
            look = theme_for(
                spec.theme,
                {**spec.colors, **(colors or {})},
                spec.labels,
                edge_colors={rel: _rgb(value) for rel, value in spec.edges.items()},
                **spec.style,
            )
        except ValueError as error:
            raise LensError(f"view.lenses[{spec.name}]: {error}") from None
        lenses.append(
            Lens(
                spec.name,
                spec.description,
                rels=list(spec.rels) if spec.rels else None,
                tree=spec.tree,
                color_by=spec.color_by,
                theme=look,
                group=group,
                weight=weight_by(graph, spec.size_by),
                label=_labeller(spec.label_types, label),
                min_parallel=spec.min_parallel,
                hide=list(spec.hide) or None,
                legend=list(spec.legend) if spec.legend is not None else None,
                linked_only=spec.hide_unlinked,
                globe=spec.globe,
                place=list(spec.place) if spec.place else None,
                via=list(spec.via) if spec.via else None,
                arcs=spec.arcs if isinstance(spec.arcs, str) else list(spec.arcs),
            )
        )
    return lenses


def lenses(
    graph: Graph,
    *,
    theme: str | None = None,
    color_by: str | None = None,
    rels: list[str] | None = None,
    tree: str | None = None,
    hide: list[str] | None = None,
    lens: str | None = None,
    **hooks: Any,
) -> list[Lens]:
    """What ``bib view`` shows: the declared lenses, or one custom lens when
    relations, a tree or hidden types are given on the command line, with
    the theme and colour key overridden for all, starting on ``lens``."""
    specs = declared(graph)
    if rels or tree or hide:
        graphs = [spec for spec in specs if not spec.globe] or [Spec(DEFAULT_NAME)]
        specs = [custom(graphs[0], rels, tree, hide)]
    found = build(graph, specs, theme=theme, color_by=color_by, **hooks)
    return pick(found, lens) if lens else found


def weight_by(graph: Graph, size_by: str | None) -> Callable[[Node], float] | None:
    """A node's size from ``size_by``: None for the default (how many
    distinct nodes it is drawn linked to), ``in:<rel>``/``out:<rel>`` for
    the number of such edges ever (``in``/``out`` alone count every
    relation), else a numeric fact's current value."""
    if size_by is None or size_by == "degree":
        return None
    direction, _, rel = size_by.partition(":")
    if direction in ("in", "out"):
        counts: Counter[str] = Counter()
        for node in graph.nodes.values():
            for edge in node.edges:
                if edge.superseded_at is None and (not rel or edge.rel == rel):
                    counts[edge.to if direction == "in" else node.id] += 1
        return lambda node: float(counts[node.id])

    def by_fact(node: Node) -> float:
        for fact in node.facts:
            if fact.key == size_by and fact.superseded_at is None:
                try:
                    return float(fact.value)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    return by_fact


# -- parsing ------------------------------------------------------------------


def _spec(raw: dict[str, Any], name: str, where: str, graph: Graph) -> Spec:
    types, rels, keys = _known(graph)
    description = str(raw.get("description") or "")
    drawn = _names(raw.get("rels"), f"{where}.rels", rels, "relation")
    hide = set(_names(raw.get("hide"), f"{where}.hide", types, "node type") or ())
    show = _names(raw.get("show"), f"{where}.show", types, "node type")
    if show is not None:
        hide |= set(types) - set(show)
    tree = raw.get("tree")
    if tree is not None:
        tree = str(tree)
        if tree not in rels:
            raise LensError(f"{where}.tree: no relation {tree!r}{_hint(rels)}")
    layout = raw.get("layout")
    if layout is not None:
        if layout not in LAYOUTS:
            raise LensError(f"{where}.layout: {layout!r} is not one of {', '.join(LAYOUTS)}")
        if layout == "tree" and tree is None:
            raise LensError(f"{where}.layout: a tree needs a `tree:` relation")
        if layout in ("force", "globe"):
            tree = None
    globe = layout == "globe"
    place = _names(raw.get("place"), f"{where}.place", types, "node type")
    via = _names(raw.get("via"), f"{where}.via", rels, "relation")
    arcs = raw.get("arcs", "shared")
    if isinstance(arcs, str) and arcs in ("shared", "none"):
        pass
    elif arcs is False:
        arcs = "none"
    else:
        arcs = _names(arcs, f"{where}.arcs", rels, "relation") or "none"
    color_by = str(raw.get("color_by") or "type")
    scope, dot, key = color_by.partition(".")
    if dot and scope in types:  # claim.kind: one type by a key, the rest by type
        if key not in keys and key not in rels:
            raise LensError(f"{where}.color_by: {key!r} is not a fact key or a relation")
    elif color_by != "type" and color_by not in keys and color_by not in rels:
        raise LensError(f"{where}.color_by: {color_by!r} is not 'type', a fact key, a relation or <type>.<key>")
    labels, label_types = _labels(raw.get("labels"), f"{where}.labels", types)
    size_by = raw.get("size_by")
    if size_by is not None:
        size_by = str(size_by)
        direction, _, rel = size_by.partition(":")
        if direction in ("in", "out"):
            if rel and rel not in rels:
                raise LensError(f"{where}.size_by: no relation {rel!r}{_hint(rels)}")
        elif size_by != "degree" and size_by not in keys:
            raise LensError(f"{where}.size_by: {size_by!r} is not degree, in:<relation>, out:<relation> or a fact key")
    legend = raw.get("legend")
    if legend is False:
        legend = ()
    elif legend is not None:
        legend = tuple(_names(legend, f"{where}.legend", None, "category") or ())
    min_parallel = raw.get("min_parallel", 1)
    if not isinstance(min_parallel, int) or isinstance(min_parallel, bool) or min_parallel < 1:
        raise LensError(f"{where}.min_parallel: a whole number of edges, 1 or more")
    style = raw.get("style") or {}
    if not isinstance(style, dict) or any(key not in STYLE_KEYS for key in style):
        raise LensError(f"{where}.style: a mapping of {', '.join(sorted(STYLE_KEYS))}")
    return Spec(
        name=name,
        description=description,
        rels=drawn,
        hide=tuple(sorted(hide)),
        tree=tree,
        color_by=color_by,
        theme=str(raw.get("theme") or DEFAULT_THEME),
        colors=_colors(raw.get("colors"), f"{where}.colors"),
        edges=_colors(raw.get("edges"), f"{where}.edges", rels),
        labels=labels,
        label_types=label_types,
        size_by=size_by,
        legend=legend,
        min_parallel=min_parallel,
        hide_unlinked=bool(raw.get("hide_unlinked", False)),
        style=dict(style),
        globe=globe,
        place=place,
        via=via,
        arcs=arcs,
    )


def _known(graph: Graph) -> tuple[list[str], list[str], set[str]]:
    """The node types, relations and fact keys a lens may name: the
    model's, or with no model whatever the graph holds."""
    if graph.model:
        types = list(graph.model.nodes)
        rels = list(graph.model.edges)
        keys = {key for spec in graph.model.nodes.values() for key in (spec.facts or {})}
    else:
        types = sorted({node.type for node in graph.nodes.values()})
        rels = sorted({edge.rel for node in graph.nodes.values() for edge in node.edges})
        keys = {fact.key for node in graph.nodes.values() for fact in node.facts}
    return types, rels, keys


def _names(value: Any, where: str, known: list[str] | None, what: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",") if part.strip()]
    if not isinstance(value, list):
        raise LensError(f"{where}: a list of {what}s")
    names = tuple(str(item) for item in value)
    if known is not None:
        for name in names:
            if name not in known:
                raise LensError(f"{where}: no {what} {name!r}{_hint(known)}")
    return names


def _labels(value: Any, where: str, types: list[str]) -> tuple[int | None, tuple[str, ...] | None]:
    """``labels: 8`` or ``labels: {count: 8, types: [paper]}``."""
    if value is None:
        return None, None
    if isinstance(value, bool) or not isinstance(value, (int, dict)):
        raise LensError(f"{where}: a number, or a mapping with count and types")
    if isinstance(value, int):
        return value, None
    for key in value:
        if key not in ("count", "types"):
            raise LensError(f"{where}: the keys are count and types, not {key!r}")
    count = value.get("count")
    if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 0):
        raise LensError(f"{where}.count: a number of nodes to label")
    return count, _names(value.get("types"), f"{where}.types", types, "node type")


def _colors(value: Any, where: str, known: list[str] | None = None) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise LensError(f"{where}: a mapping to '#rrggbb' colours")
    for name, color in value.items():
        if known is not None and str(name) not in known:
            raise LensError(f"{where}: no relation {name!r}{_hint(known)}")
        _rgb(color, f"{where}.{name}")
    return {str(name): str(color) for name, color in value.items()}


def _rgb(value: Any, where: str = "") -> tuple[int, int, int]:
    text = str(value).strip().removeprefix("#")
    try:
        if len(text) != 6:
            raise ValueError
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        raise LensError(f"{where or 'colour'}: not a colour: {value!r} (use '#rrggbb')") from None


def _hint(known: list[str]) -> str:
    return f"; the graph has {', '.join(known)}" if known else ""


def _labeller(types: tuple[str, ...] | None, label: Callable[[Node], str] | None) -> Callable[[Node], str] | None:
    """A label function that keeps ``label`` (or the default short name)
    for the given types and names nothing else."""
    if types is None:
        return label
    from bibliotheke.scene import short_name

    text = label or (lambda node: short_name(node.name))
    allowed = set(types)
    return lambda node: text(node) if node.type in allowed else ""



# -- following the model ------------------------------------------------------


def rewrite(settings: dict[str, Any], kind: str, old: str, new: str | None) -> dict[str, Any]:
    """The ``view:`` section with a node type, fact key or relation
    (``kind``: ``type``, ``fact``, ``rel``) renamed to ``new``, or with
    every mention of it taken out when ``new`` is None (it was dropped), at
    the view level and in every lens. A lens that only named the dropped
    thing is left as it is: ``bib check`` says what is missing."""
    settings = {key: value for key, value in settings.items()}
    for key in ("lenses",):
        if isinstance(settings.get(key), list):
            settings[key] = [
                _rewrite_one(entry, kind, old, new) if isinstance(entry, dict) else entry for entry in settings[key]
            ]
    return _rewrite_one(settings, kind, old, new)


def _rewrite_one(entry: dict[str, Any], kind: str, old: str, new: str | None) -> dict[str, Any]:
    entry = dict(entry)
    lists = {"type": ("hide", "show", "legend"), "rel": ("rels",), "fact": ()}[kind]
    for key in lists:
        if isinstance(entry.get(key), list):
            entry[key] = [new if item == old else item for item in entry[key] if item != old or new is not None]
    if kind == "type" and isinstance(entry.get("labels"), dict) and isinstance(entry["labels"].get("types"), list):
        entry["labels"] = {**entry["labels"], "types": [new if t == old else t for t in entry["labels"]["types"] if t != old or new]}
    if kind == "rel":
        if isinstance(entry.get("edges"), dict) and old in entry["edges"]:
            edges = {(new if rel == old else rel): color for rel, color in entry["edges"].items() if rel != old or new}
            entry["edges"] = edges
        if entry.get("tree") == old:
            entry["tree"] = new
            if new is None:
                entry.pop("tree")
    if kind in ("rel", "fact"):
        if entry.get("color_by") == old:
            if new is None:
                entry.pop("color_by")
            else:
                entry["color_by"] = new
        size_by = str(entry.get("size_by") or "")
        direction, _, rel = size_by.partition(":")
        hit = (kind == "rel" and direction in ("in", "out") and rel == old) or (kind == "fact" and size_by == old)
        if hit:
            if new is None:
                entry.pop("size_by")
            else:
                entry["size_by"] = f"{direction}:{new}" if kind == "rel" else new
    return entry
