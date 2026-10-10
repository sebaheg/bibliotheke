"""The interactive graph view: a plotui 3D force-directed graph in a Textual app."""

from __future__ import annotations

import math
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Callable

from plotui import ForceLayout, Plot
from plotui.textual import PlotWidget
from rich.markup import escape
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult, ScreenStackError
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Footer, Header, Static

from bibliotheke import timeutil
from bibliotheke.graph import At, Graph
from bibliotheke.model import Edge, Node, Record

RGB = tuple[int, int, int]


@dataclass(frozen=True)
class Theme:
    """How the graph view looks. ``canvas`` is the opaque background; with it,
    plotui antialiases and blends (``styled``), without it the plot floats
    over the terminal with hard pixel edges."""

    palette: list[RGB]
    edge: RGB  # edges of the hovered node
    edge_dim: RGB  # every other edge while hovering
    canvas: RGB | None = None
    styled: bool = False
    edge_alpha: float = 0.35
    edge_width: float = 1.0
    fog: float = 0.2
    halo: float = 0.0
    size_min: float = 3.0
    size_step: float = 0.45  # radius grows by this per sqrt(degree) in styled themes, per degree otherwise
    size_max: float = 6.6
    labels: int = 0  # label this many of the heaviest nodes
    label_size: float = 1.0
    overrides: dict[str, RGB] = field(default_factory=dict)
    # Edge colour per relation. A relation not named here takes its
    # endpoints' colours (styled) or ``edge`` (classic).
    edge_colors: dict[str, RGB] = field(default_factory=dict)


THEMES = {
    # A network map: near-black canvas, bright nodes sized by degree, thin
    # translucent edges in their endpoints' colours, nodes always on top.
    "map": Theme(
        palette=[
            (79, 195, 247), (102, 217, 166), (255, 209, 102), (186, 148, 255), (255, 121, 121),
            (240, 240, 244), (94, 129, 244), (160, 220, 90), (255, 160, 200), (130, 230, 230),
        ],
        edge=(240, 240, 248),
        edge_dim=(30, 32, 38),
        canvas=(17, 18, 21),
        styled=True,
        edge_alpha=0.32,
        edge_width=0.7,
        fog=0.25,
        halo=0.6,
        size_min=1.8,
        size_step=1.15,
        size_max=9.0,
        labels=16,
        label_size=0.72,
    ),
    # The original look: transparent background, hard edges, flat grey lines.
    "classic": Theme(
        palette=[
            (230, 60, 120), (69, 200, 209), (240, 161, 60), (120, 200, 90), (150, 120, 240),
            (240, 220, 90), (240, 110, 70), (90, 150, 240), (200, 120, 200), (150, 160, 170),
        ],
        edge=(120, 126, 140),
        edge_dim=(34, 38, 48),
    ),
}
SEED = 7
SETTLED = 1e-3
WARMUP_STEPS = 120
WARMUP_ENERGY = 0.03  # mean movement per step, in layout units, below which the shape is there
WARMUP_SECONDS = 0.5
TIME_STEP = 0.01  # share of the time span one key press moves
TIME_JUMP = 0.1  # with shift
PLAY_FPS = 15
PLAY_SECONDS = 20.0  # how long playing the whole span takes
HINT = "Hover a node to see its neighbourhood.\nClick a node or an edge to inspect it."


@dataclass
class Scene:
    """What the view draws: the nodes and edges in view, as index lists.
    ``weights`` size the nodes and pick which get labels."""

    nodes: list[Node]
    edges: list[tuple[int, int]]
    edge_records: list[Edge]
    categories: list[str]
    legend: dict[str, tuple[int, int, int]]
    weights: list[float] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)  # what a label says, per node


def build_scene(
    graph: Graph,
    at: At,
    color_by: str = "type",
    theme: Theme = THEMES["classic"],
    rels: list[str] | None = None,
    group: Callable[[str], str] | None = None,
    weight: Callable[[Node], float] | None = None,
    label: Callable[[Node], str] | None = None,
    tree: str | None = None,
    min_parallel: int = 1,
) -> Scene:
    """What to draw. ``rels`` keeps only edges of those relations, and then
    only nodes that take part in one of them (or in the ``tree`` relation,
    or whose type does, so a file at the top of a repository stays although
    no directory holds it): an author is not drawn unless their edges are.
    ``group`` maps a raw category (a type, a fact value) to the one shown,
    so a long tail can fold into one legend row; ``weight`` gives a node's
    size (and its claim to a label), by default how many distinct nodes it
    is drawn linked to; ``label`` gives a node's label text, by default its
    short name."""
    nodes = sorted(graph.list_nodes(at), key=lambda n: n.id)
    if rels:
        shown = set(rels) | ({tree} if tree else set())
        tree_types = set()
        if tree:
            for node in graph.nodes.values():
                for edge in node.edges:
                    if edge.rel == tree and edge.to in graph.nodes:
                        tree_types |= {node.type, graph.nodes[edge.to].type}
        nodes = [
            node
            for node in nodes
            if node.type in tree_types
            or any(e.rel in shown for e in node.edges)
            or any(e.rel in shown for _, e in graph.in_edges(node.id, at=None))
        ]
    index = {node.id: i for i, node in enumerate(nodes)}
    edges, edge_records = [], []
    for node in nodes:
        for edge in graph.out_edges(node.id, at):
            if rels and edge.rel not in rels:
                continue
            if edge.to in index and edge.to != node.id:
                edges.append((index[node.id], index[edge.to]))
                edge_records.append(edge)
    if min_parallel > 1:
        # Keep the pairs joined often enough, then only the nodes they link.
        count = Counter((min(a, b), max(a, b)) for a, b in edges)
        kept = [k for k, (a, b) in enumerate(edges) if count[(min(a, b), max(a, b))] >= min_parallel]
        linked = sorted({i for k in kept for i in edges[k]})
        remap = {old: new for new, old in enumerate(linked)}
        nodes = [nodes[i] for i in linked]
        index = {node.id: i for i, node in enumerate(nodes)}
        edge_records = [edge_records[k] for k in kept]
        edges = [(remap[edges[k][0]], remap[edges[k][1]]) for k in kept]
    group = group or (lambda category: category)
    categories = [group(_category(graph, node, at, color_by)) for node in nodes]
    # Colours come from every category the graph has ever had, so a category
    # keeps its colour whichever point in time is being viewed.
    known = {node.type for node in graph.nodes.values()}
    if color_by != "type":
        known |= {str(f.value) for n in graph.nodes.values() for f in n.facts if f.key == color_by}
        known |= {e.to for n in graph.nodes.values() for e in n.edges if e.rel == color_by}
    known = {group(category) for category in known}
    palette = theme.palette
    colors = {category: palette[i % len(palette)] for i, category in enumerate(sorted(known))}
    colors.update({k: v for k, v in theme.overrides.items() if k in colors})
    legend = {category: colors[category] for category in sorted(set(categories))}
    if weight is None:  # how many distinct nodes each is drawn linked to
        linked: list[set[int]] = [set() for _ in nodes]
        for a, b in edges:
            linked[a].add(b)
            linked[b].add(a)
        weights = [float(len(s)) for s in linked]
    else:
        weights = [float(weight(node)) for node in nodes]
    labels = [(label or (lambda n: short_name(n.name)))(node) for node in nodes]
    return Scene(nodes, edges, edge_records, categories, legend, weights, labels)


def layout_edges(n: int, edges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Edges for the force simulation: the real ones plus a link from an
    unseen hub (index ``n``) to each connected component, so that separate
    clusters stay near each other instead of drifting apart."""
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    degree = [0] * n
    for a, b in edges:
        parent[find(a)] = find(b)
        degree[a] += 1
        degree[b] += 1
    anchors: dict[int, int] = {}
    for i in range(n):
        root = find(i)
        if root not in anchors or degree[i] > degree[anchors[root]]:
            anchors[root] = i
    if len(anchors) < 2:
        return list(edges)
    return [*edges, *((n, anchor) for anchor in sorted(anchors.values()))]


GOLDEN = math.pi * (3.0 - math.sqrt(5.0))
FILE_SPACING = 0.45  # shell radius grows by this per sqrt(files on it)
FILE_SHELL_MIN = 0.4
GAP = 0.25


def tree_layout(graph: Graph, scene: Scene, rel: str = "contains") -> list[tuple[float, float, float]]:
    """A still 3D layout from a hierarchy: a balloon tree.

    Every node that is the source of a ``rel`` edge is a *branch* (a
    directory); every other node with a parent is a *leaf* (a file). A
    branch keeps its leaves on a small sphere around itself and its child
    branches on a larger one, each child given a cap of that sphere big
    enough for its whole subtree, so subtrees never overlap. A child branch
    sits on the hemisphere facing away from its own parent, which makes the
    tree radiate from the centre. Branches with no parent hang off a virtual
    root at the origin. Nodes outside the hierarchy sit near the nodes they
    are linked to, or on the rim when linked to nothing.
    """
    index = {node.id: i for i, node in enumerate(scene.nodes)}
    n = len(scene.nodes)
    parent: dict[int, int] = {}
    for node in scene.nodes:
        edges = [e for e in graph.out_edges(node.id, at=None) if e.rel == rel and e.to in index]
        for edge in edges:
            child = index[edge.to]
            if child != index[node.id] and (child not in parent or edge.valid_to is None):
                parent[child] = index[node.id]
    branches = {p for p in parent.values()}
    children: dict[int, list[int]] = {b: [] for b in branches}
    leaves: dict[int, list[int]] = {b: [] for b in branches}
    for child, p in parent.items():
        (children if child in branches else leaves)[p].append(child)
    roots = sorted(b for b in branches if b not in parent)
    ROOT = -1
    children[ROOT] = roots
    # A node of a kind that is a leaf elsewhere but has no parent of its own
    # (a file at the top of a repository) is a leaf of the root.
    leaf_types = {scene.nodes[c].type for c in parent if c not in branches}
    leaves[ROOT] = [i for i in range(n) if i not in parent and i not in branches and scene.nodes[i].type in leaf_types]
    for b in children:
        children[b].sort()
        leaves[b].sort()

    # Bottom-up: pack each branch's children around it. A child sits just
    # clear of the branch's file shell, in the first direction along an even
    # spiral where its subtree touches no sibling's, largest first, so small
    # subtrees fill the gaps close in. Everything is in the branch's own
    # frame, +y pointing away from its parent. A subtree is summed up as its
    # bounding sphere, which lies towards its children rather than on its
    # branch, so a chain of single-child directories grows in a line instead
    # of doubling at every level.
    shell: dict[int, float] = {}
    offset: dict[int, tuple[float, float, float]] = {}  # child position in the parent's frame
    bounds: dict[int, tuple[tuple[float, float, float], float]] = {}  # subtree sphere in its own frame

    def measure(b: int) -> tuple[tuple[float, float, float], float]:
        count = len(leaves[b])
        shell[b] = max(FILE_SHELL_MIN, FILE_SPACING * math.sqrt(count)) if count else 0.0
        own: tuple[tuple[float, float, float], float] = ((0.0, 0.0, 0.0), shell[b] + GAP / 2)
        kids = children[b]
        if not kids:
            bounds[b] = own
            return own
        sub = {k: measure(k) for k in kids}
        whole = b == ROOT
        count = max(64, 8 * len(kids))
        candidates = [_fibonacci(i, count, whole) for i in range(count)]
        placed: list[tuple[tuple[float, float, float], float]] = []
        total = own
        for k in sorted(kids, key=lambda k: (-sub[k][1], k)):
            (kc, kr) = sub[k]
            d = shell[b] + GAP
            while True:
                for c in candidates:
                    p = (c[0] * d, c[1] * d, c[2] * d)
                    oc = _orient(kc, c)  # the child's subtree sphere, in this frame
                    centre = (p[0] + oc[0], p[1] + oc[1], p[2] + oc[2])
                    clear_of_shell = _dist(centre, (0.0, 0.0, 0.0)) >= shell[b] + GAP + kr
                    if clear_of_shell and all(_dist(centre, q) >= kr + s + GAP for q, s in placed):
                        break
                else:
                    d *= 1.15
                    continue
                break
            offset[k] = p
            placed.append((centre, kr))
            total = _enclose(total, (centre, kr))
        bounds[b] = total
        return total

    measure(ROOT)
    radius = {ROOT: _dist(bounds[ROOT][0], (0.0, 0.0, 0.0)) + bounds[ROOT][1]}

    # Top-down placement.
    pos: list[tuple[float, float, float] | None] = [None] * n

    def place(b: int, centre: tuple[float, float, float], outward: tuple[float, float, float]) -> None:
        for k in children[b]:
            o = _orient(offset[k], outward)
            p = (centre[0] + o[0], centre[1] + o[1], centre[2] + o[2])
            pos[k] = p
            length = math.sqrt(o[0] ** 2 + o[1] ** 2 + o[2] ** 2) or 1.0
            place(k, p, (o[0] / length, o[1] / length, o[2] / length))
        for i, leaf in enumerate(leaves[b]):
            d = _orient(_fibonacci(i, len(leaves[b]), True), outward)
            pos[leaf] = (centre[0] + d[0] * shell[b], centre[1] + d[1] * shell[b], centre[2] + d[2] * shell[b])

    place(ROOT, (0.0, 0.0, 0.0), (0.0, 1.0, 0.0))

    # Nodes outside the hierarchy: beside what they link to, else on the rim.
    neighbours: dict[int, set[int]] = {}
    for a, c in scene.edges:
        neighbours.setdefault(a, set()).add(c)
        neighbours.setdefault(c, set()).add(a)
    loose = [i for i in range(n) if pos[i] is None]
    rim = radius[ROOT] * 1.15
    for _ in range(2):
        for i in loose:
            placed = [pos[j] for j in neighbours.get(i, ()) if pos[j] is not None]
            if placed:
                cx = sum(p[0] for p in placed) / len(placed)
                cy = sum(p[1] for p in placed) / len(placed)
                cz = sum(p[2] for p in placed) / len(placed)
                length = math.sqrt(cx * cx + cy * cy + cz * cz) or 1.0
                push = GAP * 2
                pos[i] = (cx + cx / length * push, cy + cy / length * push, cz + cz / length * push)
    for k, i in enumerate(j for j in loose if pos[j] is None):
        d = _fibonacci(k, max(1, len(loose)), True)
        pos[i] = (d[0] * rim, d[1] * rim, d[2] * rim)
    return [p if p is not None else (0.0, 0.0, 0.0) for p in pos]


def _dist(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


Sphere = tuple[tuple[float, float, float], float]


def _enclose(a: Sphere, b: Sphere) -> Sphere:
    """The smallest sphere holding both."""
    (ca, ra), (cb, rb) = a, b
    gap = _dist(ca, cb)
    if gap + rb <= ra:
        return a
    if gap + ra <= rb:
        return b
    r = (ra + rb + gap) / 2
    t = (r - ra) / gap  # how far from a's centre towards b's the new centre lies
    return ((ca[0] + (cb[0] - ca[0]) * t, ca[1] + (cb[1] - ca[1]) * t, ca[2] + (cb[2] - ca[2]) * t), r)


def _fibonacci(i: int, count: int, whole: bool) -> tuple[float, float, float]:
    """Point ``i`` of ``count`` spread evenly over the unit sphere (``whole``)
    or over the hemisphere around +y."""
    y = 1.0 - (2.0 if whole else 1.0) * (i + 0.5) / count
    r = math.sqrt(max(0.0, 1.0 - y * y))
    phi = i * GOLDEN
    return (r * math.cos(phi), y, r * math.sin(phi))


def _orient(d: tuple[float, float, float], up: tuple[float, float, float]) -> tuple[float, float, float]:
    """``d``, expressed in a frame whose +y is ``up``."""
    ux, uy, uz = up
    length = math.sqrt(ux * ux + uy * uy + uz * uz) or 1.0
    ux, uy, uz = ux / length, uy / length, uz / length
    # Any vector not parallel to `up` seeds the other two axes.
    ax, ay, az = (1.0, 0.0, 0.0) if abs(ux) < 0.9 else (0.0, 0.0, 1.0)
    vx, vy, vz = uy * az - uz * ay, uz * ax - ux * az, ux * ay - uy * ax
    vl = math.sqrt(vx * vx + vy * vy + vz * vz) or 1.0
    vx, vy, vz = vx / vl, vy / vl, vz / vl
    wx, wy, wz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    x, y, z = d
    return (x * vx + y * ux + z * wx, x * vy + y * uy + z * wy, x * vz + y * uz + z * wz)


def fit(xs: list[float], ys: list[float], zs: list[float]) -> tuple[list[float], ...]:
    """Centre the points and scale them into the unit cube."""
    centred = []
    for axis in (xs, ys, zs):
        middle = (min(axis) + max(axis)) / 2
        centred.append([value - middle for value in axis])
    extent = max(max(abs(v) for v in axis) for axis in centred) or 1.0
    return tuple([v / extent for v in axis] for axis in centred)


def _category(graph: Graph, node: Node, at: At, color_by: str) -> str:
    """A fact's value, or for an edge its target's id, so that a target (a
    word class, say) shares the colour of the nodes that point to it. Nodes
    with neither fall back to their type."""
    if color_by != "type":
        for fact in graph.facts(node.id, at):
            if fact.key == color_by:
                return str(fact.value)
        for edge in graph.out_edges(node.id, at):
            if edge.rel == color_by:
                return edge.to
        if any(e.rel == color_by and e.to == node.id for n in graph.nodes.values() for e in n.edges):
            return node.id
    return node.type


def _dim(color: RGB) -> RGB:
    return (color[0] // 4, color[1] // 4, color[2] // 4)


def parse_color(value: str) -> RGB:
    """``#rrggbb`` (or ``rrggbb``) to an RGB tuple."""
    text = str(value).strip().removeprefix("#")
    if len(text) != 6:
        raise ValueError(f"not a colour: {value!r} (use #rrggbb)")
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


def theme_for(name: str, colors: dict[str, str] | None = None, labels: int | None = None, **changes: Any) -> Theme:
    """A built-in theme, with per-category colour overrides (``#rrggbb``),
    the number of nodes to label, and any other :class:`Theme` field."""
    if name not in THEMES:
        raise ValueError(f"unknown theme {name!r}; choose from {', '.join(THEMES)}")
    if colors:
        changes["overrides"] = {str(k): parse_color(v) for k, v in colors.items()}
    if labels is not None:
        changes["labels"] = int(labels)
    return Theme(**{**THEMES[name].__dict__, **changes})


def short_name(name: str) -> str:
    """A node's name without a trailing parenthetical ('X (Zhang et al.,
    2025)' is 'X'), with accents folded away: the label face is ASCII."""
    head = name.split(" (")[0].strip() or name
    folded = unicodedata.normalize("NFKD", head).encode("ascii", "ignore").decode()
    return folded or head


def label_texts(nodes: list[Node], degree: list[float], count: int, texts: list[str] | None = None) -> list[str]:
    """Short names for the ``count`` best-connected nodes, '' for the rest."""
    ranked = sorted(range(len(nodes)), key=lambda i: (-degree[i], nodes[i].id))[:count]
    chosen = {i for i in ranked if degree[i] > 0}
    texts = texts or [short_name(node.name) for node in nodes]
    return [texts[i] if i in chosen else "" for i in range(len(nodes))]


def node_size(theme: Theme, degree: float) -> float:
    if theme.styled:
        return min(theme.size_max, theme.size_min + theme.size_step * math.sqrt(degree))
    return theme.size_min + min(degree, 8) * theme.size_step


def time_span(records: list[Record]) -> tuple[datetime, datetime] | None:
    """The first and last instants the records' valid times name, or None
    when none of them is dated."""
    starts = [timeutil.start(r.valid_from) for r in records if r.valid_from]
    ends = [timeutil.end(r.valid_to) for r in records if r.valid_to]
    if not starts and not ends:
        return None
    first = min(starts) if starts else min(ends)
    last = max([*starts, *ends])
    return first, last if last > first else first + timedelta(days=1)


class TimeSlider(Widget):
    """One line: the span's ends, a bar with a handle, and the time under
    the handle. Drag or click the bar, or step it with ←/→ (shift: jumps)
    while it has focus; every change posts :class:`Changed`."""

    DEFAULT_CSS = """
    TimeSlider { height: 1; padding: 0 1; background: $panel; }
    TimeSlider:focus { background: $panel-lighten-1; }
    """
    can_focus = True
    ALLOW_SELECT = False  # a drag moves the handle; it does not select text

    class Changed(Message):
        """The slider moved to ``when``; ``by_user`` when by mouse or key
        rather than by code (playback), so a host can stop playing."""

        def __init__(self, when: datetime, by_user: bool) -> None:
            super().__init__()
            self.when = when
            self.by_user = by_user

    def __init__(self, first: datetime, last: datetime, when: datetime | None = None, id: str | None = None) -> None:
        super().__init__(id=id)
        self.first, self.last = first, last
        self.fraction = 1.0 if when is None else self.fraction_of(when)
        self._dragging = False

    def fraction_of(self, when: datetime) -> float:
        span = (self.last - self.first).total_seconds()
        return min(1.0, max(0.0, (when - self.first).total_seconds() / span)) if span > 0 else 1.0

    @property
    def when(self) -> datetime:
        return self.first + (self.last - self.first) * self.fraction

    def set_fraction(self, fraction: float, by_user: bool = False) -> None:
        fraction = min(1.0, max(0.0, fraction))
        if fraction == self.fraction:
            return
        self.fraction = fraction
        self.refresh()
        self.post_message(self.Changed(self.when, by_user))

    # -- drawing ------------------------------------------------------------

    def _bar(self) -> tuple[int, int]:
        """Column where the bar starts (inside the padding) and its width."""
        left = len(_day(self.first)) + 1
        width = max(3, self.size.width - 2 - left - len(_day(self.last)) - 1 - 12)
        return left, width

    def render(self) -> Text:
        left, width = self._bar()
        at = round(self.fraction * (width - 1))
        text = Text(_day(self.first) + " ", style="dim")
        text.append("━" * at, style="bold")
        text.append("●", style="bold")
        text.append("━" * (width - 1 - at), style="dim")
        text.append(" " + _day(self.last), style="dim")
        text.append(f"  {_day(self.when)}", style="bold")
        return text

    def _fraction_at(self, x: int) -> float:
        left, width = self._bar()
        return (x - 1 - left) / max(1, width - 1)

    # -- input --------------------------------------------------------------

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self._dragging = True
        self.capture_mouse()
        self.focus()
        self.set_fraction(self._fraction_at(event.x), by_user=True)

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._dragging:
            self.set_fraction(self._fraction_at(event.x), by_user=True)

    def on_mouse_up(self, event: events.MouseUp) -> None:
        self._dragging = False
        self.release_mouse()

    def on_key(self, event: events.Key) -> None:
        steps = {"left": -TIME_STEP, "right": TIME_STEP, "shift+left": -TIME_JUMP, "shift+right": TIME_JUMP, "home": -1.0, "end": 1.0}
        if event.key in steps:
            self.set_fraction(self.fraction + steps[event.key], by_user=True)
            event.stop()


def _day(when: datetime) -> str:
    return when.strftime("%Y-%m-%d")


@dataclass(frozen=True)
class Lens:
    """One way of looking at a graph: which relations are drawn, laid out
    how, coloured and sized by what. A view can offer several."""

    name: str
    description: str = ""
    rels: list[str] | None = None
    tree: str | None = None  # a hierarchy relation: a still tree instead of a force layout
    color_by: str = "type"
    theme: Theme = THEMES["map"]
    group: Callable[[str], str] | None = None
    weight: Callable[[Node], float] | None = None
    label: Callable[[Node], str] | None = None
    # Draw a pair of nodes only when at least this many edges join them, and
    # leave out nodes left with nothing: a coupling that happened once is noise.
    min_parallel: int = 1


class Picture:
    """A lens applied to a graph: the scene, its layout and the plot that
    draws it. Built once per lens and kept while the view runs."""

    def __init__(self, graph: Graph, at: At, lens: Lens) -> None:
        theme = lens.theme
        if theme.styled and not hasattr(Plot, "set_graph_style"):
            theme = THEMES["classic"]  # a plotui without graph styling
        self.lens = lens
        self.look = theme
        self.scene = build_scene(
            graph, at, lens.color_by, theme, lens.rels, lens.group, lens.weight, lens.label, lens.tree, lens.min_parallel
        )
        self.plot = Plot()
        self.handle: int | None = None
        self.energy = float("inf")
        self.layout: ForceLayout | None = None
        self.still: list[tuple[float, float, float]] = []
        self.base: list[RGB] = []
        scene = self.scene
        if not scene.nodes:
            return
        self.base = [scene.legend[c] for c in scene.categories]
        n = len(scene.nodes)
        if lens.tree:
            self.still = tree_layout(graph, scene, lens.tree)
            self.energy = 0.0  # nothing to settle
        else:
            # One spring per pair: parallel edges (files that changed together
            # in a hundred commits) show their weight as a bundle, not by
            # collapsing the layout.
            springs = sorted({(min(a, b), max(a, b)) for a, b in scene.edges})
            force_edges = layout_edges(n, springs)
            self.layout = ForceLayout(n + (len(force_edges) > len(springs)), force_edges, seed=SEED)
            # Warm up past the initial spread from the random start, so the
            # first frame already has its shape; bounded in time for very
            # large graphs.
            started = time.monotonic()
            for _ in range(WARMUP_STEPS):
                if self.layout.step() < WARMUP_ENERGY or time.monotonic() - started > WARMUP_SECONDS:
                    break
        self.plot.set_show_box(False)
        reach = 0.82 if theme.styled else 1.15
        self.plot.set_bounds((-reach, -reach, -reach), (reach, reach, reach))
        if theme.canvas:
            lift = tuple(min(255, c + 14) for c in theme.canvas)
            self.plot.set_chrome(canvas=theme.canvas, bg=lift, frame=tuple(min(255, c + 40) for c in theme.canvas))
        xs, ys, zs = self.positions()
        self.handle = self.plot.add_graph3d(
            xs, ys, zs,
            edges=scene.edges,
            node_colors=self.base,
            node_sizes=[node_size(theme, w) for w in scene.weights],
            edge_colors=resting_edges(theme, scene, [True] * len(scene.edges), self.base),
        )
        if theme.styled:
            self.plot.set_graph_style(
                self.handle,
                edge_alpha=theme.edge_alpha,
                edge_width=theme.edge_width,
                fog=theme.fog,
                halo=theme.halo,
                label_size=theme.label_size,
            )
            if theme.labels:
                self.plot.set_graph_labels(self.handle, label_texts(scene.nodes, scene.weights, theme.labels, scene.labels))
        self.plot.set_legend_entries(
            [(category, "disc", color, None, True) for category, color in scene.legend.items()]
        )
        self.plot.set_legend_corner("top-left")

    def positions(self) -> tuple[list[float], ...]:
        n = len(self.scene.nodes)
        if self.layout is None:
            return fit([p[0] for p in self.still], [p[1] for p in self.still], [p[2] for p in self.still])
        return fit(*(axis[:n] for axis in self.layout.positions()))


def faded(theme: Theme) -> RGB:
    """The colour of an edge that is not valid at the time shown: the
    canvas colour, so it vanishes into it."""
    return theme.canvas or (0, 0, 0)


def resting_edges(theme: Theme, scene: Scene, edge_valid: list[bool], base: list[RGB]) -> list[RGB] | None:
    """Edge colours when nothing is hovered: the theme's colour for the
    relation, else the endpoints' (None) when styled or ``edge`` when not;
    edges not valid at the time shown fade out."""
    if all(edge_valid) and not theme.edge_colors:
        return None if theme.styled else [theme.edge] * len(scene.edges)
    colors = []
    for (a, _b), record, valid in zip(scene.edges, scene.edge_records, edge_valid):
        if not valid:
            colors.append(faded(theme))
        elif record.rel in theme.edge_colors:
            colors.append(theme.edge_colors[record.rel])
        else:  # a styled edge takes its endpoints' colours; naming one keeps that
            colors.append(base[a] if theme.styled else theme.edge)
    return colors


class GraphView(App):
    CSS = """
    #body { height: 1fr; }
    #plot { width: 1fr; height: 1fr; }
    #sidebar { width: 44; height: 1fr; background: $panel; border-left: thick $accent; padding: 0 1; display: none; }
    #sb-title { color: $accent; text-style: bold; padding: 1 0; }
    """
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("i", "toggle_sidebar", "Details"),
        ("f", "focus", "Focus"),
        ("space", "play", "Play"),
        ("question_mark", "help", "Keys"),
        # Priority, or the screen's own tab bindings (focus cycling) win.
        Binding("tab", "next_lens", "Next lens", priority=True),
        Binding("shift+tab", "previous_lens", "Previous lens", priority=True, show=False),
        Binding("j", "next_lens", "Next lens", show=False),
        Binding("k", "previous_lens", "Previous lens", show=False),
        *[Binding(str(n), f"lens({n})", f"Lens {n}", show=False) for n in range(1, 10)],
        Binding("r", "reset_view", "Reset view", show=False),
        Binding("escape", "deselect", "Deselect", show=False),
        Binding("comma", "step_time(-1)", "Earlier", show=False),
        Binding("full_stop", "step_time(1)", "Later", show=False),
        Binding("less_than_sign", "step_time(-10)", "Much earlier", show=False),
        Binding("greater_than_sign", "step_time(10)", "Much later", show=False),
    ]

    def __init__(
        self,
        graph: Graph,
        at: At,
        color_by: str = "type",
        theme: Theme = THEMES["map"],
        rels: list[str] | None = None,
        group: Callable[[str], str] | None = None,
        weight: Callable[[Node], float] | None = None,
        label: Callable[[Node], str] | None = None,
        tree: str | None = None,
        lenses: list[Lens] | None = None,
    ) -> None:
        """One lens from the arguments, or several given as ``lenses`` (the
        first is shown; the number keys switch). ``tree`` names a hierarchy
        relation: the layout is then the still balloon tree of
        :func:`tree_layout` rather than a force simulation."""
        super().__init__()
        self.graph = graph
        self.at = at
        self.lenses = lenses or [Lens("Graph", "", rels, tree, color_by, theme, group, weight, label)]
        self.pictures: dict[int, Picture] = {}
        self.lens_index = 0
        self.switching = False
        self.focus = False  # hovering dims everything but a node's neighbourhood
        self.pinned: int | None = None  # the node focus is held on, after a click
        self.span = time_span(
            [r for node in graph.nodes.values() for r in (*node.facts, *node.edges) if r.superseded_at is None]
        )
        self.title = f"bibliotheke — {graph.root.name}"
        self.filters = ""
        self.sub_title = ""
        self.playing: bool = False
        self._adopt(self._picture(0))
        self._set_at(at)

    # -- lenses -------------------------------------------------------------

    def _picture(self, i: int) -> Picture:
        """The lens built, once; the scene holds everything that was ever
        valid (the other two axes still apply), laid out once, so the time
        slider only hides nodes and fades edges and nothing jumps."""
        if i not in self.pictures:
            self.pictures[i] = Picture(self.graph, replace(self.at, valid_at=None), self.lenses[i])
        return self.pictures[i]

    def _adopt(self, picture: Picture) -> None:
        """Make a picture the one shown."""
        self.picture = picture
        self.look = picture.look
        self.scene = picture.scene
        self.plot = picture.plot
        self.handle = picture.handle
        self.layout = picture.layout
        self.still = picture.still
        self.energy = picture.energy
        self.base = picture.base
        self.node_visible = [True] * len(self.scene.nodes)
        self.edge_valid = [True] * len(self.scene.edges)
        self.hidden_categories: set[str] = set()
        self.pinned = None  # a pin belongs to one picture's node indices

    @property
    def lens(self) -> Lens:
        return self.lenses[self.lens_index]

    async def action_lens(self, number: int) -> None:
        """Switch to lens ``number`` (1-based): a new plot in place of the old."""
        i = number - 1
        if i == self.lens_index or not 0 <= i < len(self.lenses) or self.switching:
            return
        self.switching = True
        try:
            self.picture.energy = self.energy
            self.lens_index = i
            self._adopt(self._picture(i))
            old = self.query_one("#plot", PlotWidget)
            body = self.query_one("#body")
            await old.remove()
            widget = PlotWidget(self.plot, id="plot", pickable=True)
            await body.mount(widget, before=0)
            widget.focus()
            self._set_at(self.at)
            self._show_overview()
        finally:
            self.switching = False

    async def action_next_lens(self) -> None:
        await self.action_lens((self.lens_index + 1) % len(self.lenses) + 1)

    async def action_previous_lens(self) -> None:
        await self.action_lens((self.lens_index - 1) % len(self.lenses) + 1)

    def _subtitle(self) -> str:
        parts = [self.lens.name] if len(self.lenses) > 1 else []
        if self.filters:
            parts.append(self.filters)
        if self.focus:
            parts.append("focus")
        return "  ·  ".join(parts)

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            with Horizontal(id="body"):
                yield PlotWidget(self.plot, id="plot", pickable=True)
                with VerticalScroll(id="sidebar"):
                    yield Static("", id="sb-title")
                    yield Static("", id="sb-body")
            if self.span:
                first, last = self.span
                when = timeutil.start(self.at.valid_at) if self.at.valid_at else None
                yield TimeSlider(first, last, when, id="time")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#plot", PlotWidget).focus()
        self._show_overview()
        if self.handle is not None:
            self.set_interval(1 / 30, self._tick)
            if self.span:
                self.set_interval(1 / PLAY_FPS, self._play_tick)

    # -- time ---------------------------------------------------------------

    def _set_at(self, at: At) -> None:
        """Show the graph as of ``at``: hide the nodes not in view and fade
        the edges that are not, without touching the layout."""
        self.at = at
        self.filters = "  ".join(
            f"{label} {_brief(value)}"
            for label, value in (("valid", at.valid_at), ("known", at.known_at), ("changed", at.changed_at))
            if value
        )
        self.sub_title = self._subtitle()
        if self.handle is None:
            return
        if at.filtered:
            shown = {node.id for node in self.graph.list_nodes(at)}
            self.node_visible = [node.id in shown for node in self.scene.nodes]
            self.edge_valid = [at.sees(edge) for edge in self.scene.edge_records]
        else:
            self.node_visible = [True] * len(self.scene.nodes)
            self.edge_valid = [True] * len(self.scene.edges)
        self._apply_visibility()

    def _apply_visibility(self) -> None:
        hidden = [
            not visible or category in self.hidden_categories
            for visible, category in zip(self.node_visible, self.scene.categories)
        ]
        self.plot.set_graph_hidden(self.handle, hidden if any(hidden) else None)
        self.plot.set_graph_colors(self.handle, list(self.base), self._resting_edges())
        try:
            self.query_one("#plot", PlotWidget).invalidate()
        except (NoMatches, ScreenStackError):  # before the app is running
            pass

    @on(TimeSlider.Changed)
    def _time_changed(self, message: TimeSlider.Changed) -> None:
        if message.by_user:
            self.playing = False
        self._set_at(replace(self.at, valid_at=timeutil.canonical(message.when)))
        self._show_overview()

    def action_step_time(self, steps: int) -> None:
        try:
            slider = self.query_one("#time", TimeSlider)
        except NoMatches:
            return
        slider.set_fraction(slider.fraction + steps * TIME_STEP, by_user=True)

    def action_play(self) -> None:
        try:
            slider = self.query_one("#time", TimeSlider)
        except NoMatches:
            return
        if not self.playing and slider.fraction >= 1.0:
            slider.set_fraction(0.0)
        self.playing = not self.playing

    def _play_tick(self) -> None:
        if not self.playing:
            return
        slider = self.query_one("#time", TimeSlider)
        slider.set_fraction(slider.fraction + 1.0 / (PLAY_FPS * PLAY_SECONDS))
        if slider.fraction >= 1.0:
            self.playing = False

    def _widget(self) -> PlotWidget | None:
        """The plot widget, when it shows the current picture: during a lens
        switch the old widget is on screen for a moment after the new
        picture has been adopted, and must not be fed the new one's data."""
        try:
            widget = self.query_one("#plot", PlotWidget)
        except NoMatches:  # while switching lenses, or closing
            return None
        return widget if widget.plot is self.plot else None

    def _tick(self) -> None:
        if self.energy < SETTLED or self.layout is None:
            return
        widget = self._widget()
        if widget is None:
            return
        self.energy = self.layout.step()
        widget.set_graph_positions(self.handle, *self._positions())

    def _positions(self) -> tuple[list[float], ...]:
        return self.picture.positions()

    def _set_sidebar(self, title: str, body: str) -> None:
        self.query_one("#sb-title", Static).update(escape(title))
        self.query_one("#sb-body", Static).update(body)

    def _show_overview(self) -> None:
        nodes = sum(self.node_visible)
        edges = sum(1 for (a, b), valid in zip(self.scene.edges, self.edge_valid) if valid and self.node_visible[a] and self.node_visible[b])
        lines = [f"{nodes} nodes, {edges} edges"]
        if self.filters:
            lines.append(escape(self.filters))
        if len(self.lenses) > 1:
            lines += ["", "[bold]Lenses[/bold]  j / k or tab / shift+tab to cycle, a number to jump"]
            for i, lens in enumerate(self.lenses, 1):
                mark = "●" if i - 1 == self.lens_index else "○"
                lines.append(f"{mark} {i} [bold]{escape(lens.name)}[/bold]  [dim]{escape(lens.description)}[/dim]")
        lines += ["", HINT]
        lines += [
            "",
            "[bold]Keys[/bold]",
            "drag: rotate   shift+drag: pan   scroll: zoom",
            "f: focus mode (hover dims the rest; click pins)",
            "r: reset view   i: details   esc: clear   q: quit",
        ]
        if self.span:
            lines += ["", "Drag the time bar, or , and . to step,", "< and > to jump, space to play."]
        self._set_sidebar(self.graph.root.name, "\n".join(lines))

    def _show_node(self, i: int) -> None:
        node = self.scene.nodes[i]
        name = lambda node_id: escape(self.graph.nodes[node_id].name)  # noqa: E731
        lines = [f"[dim]{escape(node.type)} · {escape(node.id)}[/dim]"]
        if node.notes:
            lines += ["", escape(node.notes.rstrip())]
        facts = self.graph.facts(node.id, self.at)
        if facts:
            lines += ["", "[bold]Facts[/bold]"]
            lines += [f"{escape(f.key)}: {escape(str(f.value))}{_span(f)}" for f in facts]
        outgoing = self.graph.out_edges(node.id, self.at)
        incoming = self.graph.in_edges(node.id, self.at)
        if outgoing or incoming:
            lines += ["", "[bold]Edges[/bold]"]
            lines += [f"→ {escape(e.rel)} {name(e.to)}{_span(e)}" for e in outgoing]
            lines += [f"← {name(other.id)} {escape(e.rel)}{_span(e)}" for other, e in incoming]
        self._set_sidebar(node.name, "\n".join(lines))

    def _show_edge(self, i: int) -> None:
        a, b = self.scene.edges[i]
        edge = self.scene.edge_records[i]
        src, dst = self.scene.nodes[a], self.scene.nodes[b]
        lines = [
            f"{escape(src.name)} → {escape(dst.name)}",
            "",
            f"valid   : {edge.valid_from or '…'} → {edge.valid_to or '…'}",
            f"known   : {edge.known_at}",
            f"changed : {edge.changed_at}",
        ]
        self._set_sidebar(edge.rel, "\n".join(lines))

    @on(PlotWidget.ElementHovered)
    def _hovered(self, message: PlotWidget.ElementHovered) -> None:
        widget = self._widget()
        if self.handle is None or widget is None:
            return
        element = message.element
        if element is not None and element[0] == "node":
            self.sub_title = self.scene.nodes[element[1]].name
            if self.focus:
                self._light(element[1])
        else:
            if self.focus:
                self._light(self.pinned)  # back to the pinned node, or to everything
            if element is not None and self.edge_valid[element[1]]:
                a, b = self.scene.edges[element[1]]
                edge = self.scene.edge_records[element[1]]
                self.sub_title = f"{self.scene.nodes[a].name} {edge.rel} {self.scene.nodes[b].name}"
            else:
                self.sub_title = self._subtitle()

    def _light(self, i: int | None) -> None:
        """Focus mode: node ``i`` and its neighbourhood in full colour, the
        rest dimmed; ``None`` lights everything again."""
        widget = self._widget()
        if widget is None:
            return
        if i is None:
            widget.set_graph_colors(self.handle, list(self.base), self._resting_edges())
            return
        near = {i} | {b if a == i else a for (a, b), valid in zip(self.scene.edges, self.edge_valid) if valid and i in (a, b)}
        node_colors = [c if j in near else _dim(c) for j, c in enumerate(self.base)]
        edge_colors = [
            self._faded if not valid else self.look.edge if i in pair else self.look.edge_dim
            for pair, valid in zip(self.scene.edges, self.edge_valid)
        ]
        widget.set_graph_colors(self.handle, node_colors, edge_colors)

    def action_focus(self) -> None:
        """Toggle focus mode. Off (the default), hovering only names a node;
        on, it dims everything but the node's neighbourhood, and a click
        pins that until `esc`."""
        self.focus = not self.focus
        if not self.focus:
            self.pinned = None
            self._light(None)
        self.sub_title = self._subtitle()

    @property
    def _faded(self) -> RGB:
        return faded(self.look)

    def _resting_edges(self) -> list[RGB] | None:
        return resting_edges(self.look, self.scene, self.edge_valid, self.base)

    def action_help(self) -> None:
        """The key list lives in the details panel's overview."""
        self.plot.set_selected(None)
        self._show_overview()
        self._show_sidebar(True)

    @on(PlotWidget.LegendToggled)
    def _legend_toggled(self, message: PlotWidget.LegendToggled) -> None:
        """A legend click hides or shows every node of that category."""
        if self.handle is None or message.entry is None:
            return
        category = list(self.scene.legend)[message.entry]
        if message.visible:
            self.hidden_categories.discard(category)
        else:
            self.hidden_categories.add(category)
        self._apply_visibility()

    @on(PlotWidget.ElementPicked)
    def _picked(self, message: PlotWidget.ElementPicked) -> None:
        """A click on a node or edge opens the details panel on it; the
        graph has the whole screen until then."""
        element = message.element
        if element is None:
            self._show_overview()
            if self.focus:
                self.pinned = None
                self._light(None)
        elif element[0] == "node":
            self._show_node(element[1])
            self._show_sidebar(True)
            if self.focus:
                self.pinned = element[1]
                self._light(self.pinned)
        else:
            self._show_edge(element[1])
            self._show_sidebar(True)

    def _show_sidebar(self, visible: bool) -> None:
        self.query_one("#sidebar").display = visible
        try:
            self.query_one("#plot", PlotWidget).invalidate()
        except NoMatches:
            pass

    def action_toggle_sidebar(self) -> None:
        self._show_sidebar(not self.query_one("#sidebar").display)

    def action_deselect(self) -> None:
        """Back to the clean view: nothing selected or pinned, details panel away."""
        self.plot.set_selected(None)
        self.pinned = None
        if self.focus:
            self._light(None)
        self._show_overview()
        self._show_sidebar(False)

    def action_reset_view(self) -> None:
        self.plot.reset()
        if widget := self._widget():
            widget.invalidate()


def _brief(stamp: str) -> str:
    """A time value for the header: a timestamp shows as its day."""
    return stamp[:10] if "T" in stamp else stamp


def _span(record) -> str:
    if not record.valid_from and not record.valid_to:
        return ""
    return f" [dim]({record.valid_from or '…'} → {record.valid_to or '…'})[/dim]"
