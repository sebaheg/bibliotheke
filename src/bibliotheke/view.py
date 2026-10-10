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

from bibliotheke import timeutil, tui
from bibliotheke.graph import At, Graph
from bibliotheke.lenses import DEFAULT_THEME
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
    # The chrome (what is not data): legend box fill and border, grid, tick
    # ink and legend text. None keeps plotui's own, lifted from ``canvas``.
    legend_bg: RGB | None = None
    frame: RGB | None = None
    grid: RGB | None = None
    ink: RGB | None = None
    ink_bright: RGB | None = None
    swatch: str = "disc"  # legend swatch shape: square, disc, ring, dot…
    globe: str = "dark"  # the Earth on a globe lens: dark, earth, light, blueprint or wire
    chrome: str = "plotui"  # the window around the plot: a name in tui.THEMES


def _rgb(hex: str) -> RGB:
    return (int(hex[1:3], 16), int(hex[3:5], 16), int(hex[5:7], 16))


THEMES = {
    # plotui's own design, as on plotui.xyz: a near-black blue-grey canvas,
    # the pink / cyan / orange trio leading an eight-colour way, flat dots a
    # little bigger than the map theme's, quiet translucent edges, and a
    # legend that is a rounded panel with square chips, its border a
    # hairline barely lighter than the canvas.
    "plotui": Theme(
        palette=[
            (230, 60, 120),  # pink
            (69, 200, 209),  # cyan
            (240, 161, 60),  # orange
            (144, 133, 233),  # violet
            (25, 158, 112),  # green
            (201, 133, 0),  # gold
            (57, 135, 229),  # blue
            (230, 103, 103),  # red
        ],
        edge=_rgb(tui.BODY),
        edge_dim=_rgb(tui.GRID),
        canvas=_rgb(tui.CANVAS),
        styled=True,
        edge_alpha=0.32,
        edge_width=0.6,
        fog=0.25,
        halo=0.0,
        size_min=2.4,
        size_step=1.0,
        size_max=8.0,
        labels=16,
        label_size=0.72,
        legend_bg=(14, 18, 22),
        frame=_rgb(tui.GRID),
        grid=_rgb(tui.GRID),
        ink=_rgb(tui.INK),
        ink_bright=_rgb(tui.BODY),
        swatch="square",
    ),
    # A network map: a near-black blue canvas, flat nodes in pastel neons
    # (soft but luminous against the dark, the way a protein viewer or a
    # dark-mode code editor colours things), thin translucent edges in their
    # endpoints' colours, no glow, nodes always on top.
    "map": Theme(
        palette=[
            (255, 184, 108),  # orange
            (124, 227, 139),  # green
            (111, 211, 255),  # cyan
            (255, 126, 182),  # pink
            (200, 162, 255),  # lavender
            (255, 230, 102),  # yellow
            (120, 240, 220),  # mint
            (255, 140, 120),  # coral
            (160, 200, 255),  # periwinkle
            (225, 225, 235),  # chalk
        ],
        edge=(235, 235, 245),
        edge_dim=(30, 33, 40),
        canvas=(13, 16, 22),
        styled=True,
        edge_alpha=0.38,
        edge_width=0.6,
        fog=0.25,
        halo=0.0,
        size_min=1.8,
        size_step=1.1,
        size_max=8.0,
        labels=16,
        label_size=0.72,
    ),
    # The Dracula theme (draculatheme.com) on a plotui-dark canvas tinted
    # Dracula's purple (its own backgrounds read as grey behind a graph), its seven accents leading the colour way, comment blue last,
    # and the window chrome in the same colours.
    "dracula": Theme(
        palette=[
            _rgb(tui.DRACULA_COLORS.PINK),
            _rgb(tui.DRACULA_COLORS.CYAN),
            _rgb(tui.DRACULA_COLORS.ORANGE),
            _rgb(tui.DRACULA_COLORS.PURPLE),
            _rgb(tui.DRACULA_COLORS.GREEN),
            _rgb(tui.DRACULA_COLORS.YELLOW),
            _rgb(tui.DRACULA_COLORS.RED),
            _rgb(tui.DRACULA_COLORS.COMMENT),
        ],
        edge=_rgb(tui.DRACULA_COLORS.FOREGROUND),
        edge_dim=_rgb(tui.DRACULA_COLORS.GRID),
        canvas=_rgb(tui.DRACULA_COLORS.CANVAS),
        styled=True,
        edge_alpha=0.32,
        edge_width=0.6,
        fog=0.25,
        halo=0.0,
        size_min=2.4,
        size_step=1.0,
        size_max=8.0,
        labels=16,
        label_size=0.72,
        legend_bg=_rgb(tui.DRACULA_COLORS.PANEL),
        frame=_rgb(tui.DRACULA_COLORS.FRAME),
        grid=_rgb(tui.DRACULA_COLORS.GRID),
        ink=_rgb(tui.DRACULA_COLORS.COMMENT),
        ink_bright=_rgb(tui.DRACULA_COLORS.FOREGROUND),
        swatch="square",
        chrome="dracula",
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
TICK_FPS = 60  # layout steps (and frames) per second while the graph settles
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
    hide: list[str] | None = None,
    linked_only: bool = False,
) -> Scene:
    """What to draw. ``rels`` keeps only edges of those relations, and then
    only nodes that take part in one of them (or in the ``tree`` relation,
    or whose type does, so a file at the top of a repository stays although
    no directory holds it): an author is not drawn unless their edges are.
    ``group`` maps a raw category (a type, a fact value) to the one shown,
    so a long tail can fold into one legend row; ``weight`` gives a node's
    size (and its claim to a label), by default how many distinct nodes it
    is drawn linked to; ``label`` gives a node's label text, by default its
    short name. ``hide`` leaves out whole node types (a graph where one
    type is most of the nodes, such as claims, is easier read without them);
    ``linked_only`` leaves out every node that ends up with no edge drawn."""
    nodes = sorted(graph.list_nodes(at), key=lambda n: n.id)
    if hide:
        nodes = [node for node in nodes if node.type not in set(hide)]
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
    if min_parallel > 1 or linked_only:
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
    # Colours come from every category this lens could ever draw (whatever
    # the time, so a category keeps its colour as the time bar moves), and
    # only those: a hidden type or another type's facts take no colour.
    hidden = set(hide or ())
    if rels:
        touching = {n.id for n in graph.nodes.values() for e in n.edges if e.rel in shown}
        touching |= {e.to for n in graph.nodes.values() for e in n.edges if e.rel in shown}
        drawable = {t for t in {n.type for n in graph.nodes.values() if n.id in touching} | tree_types if t not in hidden}
    else:
        drawable = {n.type for n in graph.nodes.values()} - hidden
    pool = [n for n in graph.nodes.values() if n.type in drawable]
    colors = category_colors(graph, pool, color_by, theme, group)
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

    Every node that is the parent end of a ``rel`` edge is a *branch* (a
    directory); every other node with a parent is a *leaf* (a file). The
    parent end is the source when the relation fans out (a directory
    ``contains`` files) and the target when it fans in (claims are each
    ``made_in`` one source, a source holds many). A
    branch keeps its leaves on a small sphere around itself and its child
    branches on a larger one, each child given a cap of that sphere big
    enough for its whole subtree, so subtrees never overlap. A child branch
    sits on the hemisphere facing away from its own parent, which makes the
    tree radiate from the centre. Branches with no parent hang off a virtual
    root at the origin. Nodes outside the hierarchy sit near the nodes they
    are linked to, spread out where several would share a place, or on the
    rim when linked to nothing.
    """
    index = {node.id: i for i, node in enumerate(scene.nodes)}
    n = len(scene.nodes)
    pairs = [
        (index[node.id], index[e.to], e)
        for node in scene.nodes
        for e in graph.out_edges(node.id, at=None)
        if e.rel == rel and e.to in index and e.to != node.id
    ]
    # Which end is the parent: the one that has many of the other.
    fan_out = Counter(a for a, _, _ in pairs)
    fan_in = Counter(b for _, b, _ in pairs)
    upward = sum(c > 1 for c in fan_in.values()) > sum(c > 1 for c in fan_out.values())
    parent: dict[int, int] = {}
    for a, b, edge in pairs:
        up, child = (b, a) if upward else (a, b)
        if child not in parent or edge.valid_to is None:
            parent[child] = up
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
    # Loose nodes linked to the same nodes land on one spot: fan them out
    # on a small sphere around it, so each can be seen and clicked.
    spots: dict[tuple[float, float, float], list[int]] = {}
    for i in loose:
        if pos[i] is not None:
            spots.setdefault(pos[i], []).append(i)
    for spot, group in spots.items():
        if len(group) > 1:
            r = max(FILE_SHELL_MIN, FILE_SPACING * math.sqrt(len(group)))
            for k, i in enumerate(group):
                d = _fibonacci(k, len(group), True)
                pos[i] = (spot[0] + d[0] * r, spot[1] + d[1] * r, spot[2] + d[2] * r)
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


def category_colors(
    graph: Graph, pool: list[Node], color_by: str, theme: Theme, group: Callable[[str], str] | None = None
) -> dict[str, RGB]:
    """A colour for every category the ``pool`` of nodes could ever show,
    whatever the time (so a category keeps its colour as the time bar
    moves): the palette in category order, then the theme's overrides."""
    group = group or (lambda category: category)
    scope, key = split_color_by(graph, color_by)
    known = {node.type for node in pool if node.type != scope}
    if key != "type":
        within = [n for n in pool if scope is None or n.type == scope]
        known |= {str(f.value) for n in within for f in n.facts if f.key == key}
        known |= {e.to for n in within for e in n.edges if e.rel == key}
        if scope is not None:  # a scoped node without the key falls back to its type
            known |= {n.type for n in within if not any(f.key == key for f in n.facts)}
    known = {group(category) for category in known}
    palette = theme.palette
    colors = {category: palette[i % len(palette)] for i, category in enumerate(sorted(known))}
    colors.update({k: v for k, v in theme.overrides.items() if k in colors})
    return colors


def split_color_by(graph: Graph, color_by: str) -> tuple[str | None, str]:
    """``claim.kind`` is ``("claim", "kind")``: colour claims by kind and
    every other node by its type (a paper's ``kind`` means something else).
    A plain ``kind`` applies to every type: ``(None, "kind")``."""
    scope, dot, key = color_by.partition(".")
    if dot and key and scope in {node.type for node in graph.nodes.values()}:
        return scope, key
    return None, color_by


def _category(graph: Graph, node: Node, at: At, color_by: str) -> str:
    """A fact's value, or for an edge its target's id, so that a target (a
    word class, say) shares the colour of the nodes that point to it. Nodes
    with neither fall back to their type, as do nodes outside the type a
    ``type.key`` colouring names."""
    scope, color_by = split_color_by(graph, color_by)
    if scope is not None and node.type != scope:
        return node.type
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
    """Short names for the ``count`` best-connected nodes, '' for the rest.
    A node whose text is '' (a type the lens does not label) is passed over
    rather than counted."""
    texts = texts or [short_name(node.name) for node in nodes]
    candidates = [i for i in range(len(nodes)) if texts[i] and degree[i] > 0]
    chosen = set(sorted(candidates, key=lambda i: (-degree[i], nodes[i].id))[:count])
    return [texts[i] if i in chosen else "" for i in range(len(nodes))]


def node_size(theme: Theme, degree: float) -> float:
    if theme.styled:
        return min(theme.size_max, theme.size_min + theme.size_step * math.sqrt(degree))
    return theme.size_min + min(degree, 8) * theme.size_step


TIME_AXES = ("valid", "known")  # what the time bar can move: valid time (default) or knowledge time


def time_span(records: list[Record], axis: str = "valid") -> tuple[datetime, datetime] | None:
    """The first and last instants the records' valid times name (``axis``
    "valid"), or when they were learned ("known"); None when none is dated."""
    if axis == "known":
        starts = [timeutil.start(r.known_at) for r in records if r.known_at]
        ends: list[datetime] = []
    else:
        starts = [timeutil.start(r.valid_from) for r in records if r.valid_from]
        ends = [timeutil.end(r.valid_to) for r in records if r.valid_to]
    if not starts and not ends:
        return None
    first = min(starts) if starts else min(ends)
    last = max([*starts, *ends])
    return first, last if last > first else first + timedelta(days=1)


class TimeSlider(Widget):
    """One line: the axis it moves (valid or known time), the span's ends,
    a bar with a handle, and the time under the handle. Drag or click the
    bar, or step it with ←/→ (shift: jumps) while it has focus; every change
    posts :class:`Changed`."""

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

    def __init__(
        self, first: datetime, last: datetime, when: datetime | None = None, id: str | None = None, axis: str = "valid"
    ) -> None:
        super().__init__(id=id)
        self.first, self.last = first, last
        self.axis = axis
        self.fraction = 1.0 if when is None else self.fraction_of(when)
        self._dragging = False

    def retarget(self, first: datetime, last: datetime, axis: str) -> None:
        """Move another axis over another span, the handle at its end (now)."""
        self.first, self.last, self.axis = first, last, axis
        self.fraction = 1.0
        self.refresh()

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
        left = len(self.axis) + 1 + len(_day(self.first)) + 1
        width = max(3, self.size.width - 2 - left - len(_day(self.last)) - 1 - 12)
        return left, width

    def render(self) -> Text:
        left, width = self._bar()
        at = round(self.fraction * (width - 1))
        text = Text(self.axis + " ", style="bold")
        text.append(_day(self.first) + " ", style="dim")
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
    theme: Theme = THEMES[DEFAULT_THEME]
    group: Callable[[str], str] | None = None
    weight: Callable[[Node], float] | None = None
    label: Callable[[Node], str] | None = None
    # Draw a pair of nodes only when at least this many edges join them, and
    # leave out nodes left with nothing: a coupling that happened once is noise.
    min_parallel: int = 1
    hide: list[str] | None = None  # node types left out altogether
    # Legend rows: None for every category drawn, a list for just those
    # (a row left out is still drawn, only unlisted), empty for no legend.
    legend: list[str] | None = None
    linked_only: bool = False  # leave out nodes with no edge drawn
    # A globe lens: see bibliotheke.lenses.Spec and bibliotheke.geo.
    globe: bool = False
    place: list[str] | None = None
    via: list[str] | None = None
    arcs: str | list[str] = "shared"


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
            graph, at, lens.color_by, theme, lens.rels, lens.group, lens.weight, lens.label, lens.tree, lens.min_parallel, lens.hide,
            lens.linked_only,
        )
        # The legend's rows, in order: what a legend click's entry index means.
        self.legend_rows = [c for c in self.scene.legend if lens.legend is None or c in lens.legend]
        self.plot = Plot()
        self.handle: int | None = None
        self.energy = float("inf")
        self.layout: ForceLayout | None = None
        self.still: list[tuple[float, float, float]] = []
        self.base: list[RGB] = []
        self.labels: list[str] | None = None  # node labels, for when they are shown
        self.labels_shown = False
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
                self.energy = self.layout.step()
                if self.energy < WARMUP_ENERGY or time.monotonic() - started > WARMUP_SECONDS:
                    break
        self.plot.set_show_box(False)
        reach = 0.82 if theme.styled else 1.15
        self.plot.set_bounds((-reach, -reach, -reach), (reach, reach, reach))
        if theme.canvas:
            lift = tuple(min(255, c + 14) for c in theme.canvas)
            self.plot.set_chrome(
                canvas=theme.canvas,
                bg=theme.legend_bg or lift,
                frame=theme.frame or tuple(min(255, c + 40) for c in theme.canvas),
                grid=theme.grid,
                ink=theme.ink,
                ink_bright=theme.ink_bright,
            )
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
            if theme.labels:  # drawn only while the view's labels are on (t)
                self.labels = label_texts(scene.nodes, scene.weights, theme.labels, scene.labels)
        self.plot.set_legend_entries(
            [(category, theme.swatch, scene.legend[category], None, True) for category in self.legend_rows]
        )
        self.plot.set_legend_corner("top-left")
        if hasattr(Plot, "legend_keys"):
            self.plot.legend_keys = True  # 1–9 show and hide the rows

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



def _show_labels(picture: "Picture", on: bool) -> bool:
    """Draw a picture's node labels or clear them; whether anything changed."""
    want = on and picture.labels is not None and picture.handle is not None
    if want == picture.labels_shown:
        return False
    if picture.handle is not None:
        picture.plot.set_graph_labels(picture.handle, picture.labels if want else None)
    picture.labels_shown = want
    return True

class GraphView(App):
    ENABLE_COMMAND_PALETTE = False  # no ^p palette: the keys are the whole interface
    CSS = """
    HeaderIcon { display: none; }
    #body { height: 1fr; }
    #plot { width: 1fr; height: 1fr; }
    #sidebar { width: 44; height: 1fr; background: $panel; border-left: thick $accent; padding: 0 1; display: none; }
    #sb-title { color: $accent; text-style: bold; padding: 1 0; }
    """
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("i", "toggle_sidebar", "Details"),
        ("f", "focus", "Focus"),
        ("t", "toggle_labels", "Labels"),
        ("space", "play", "Play"),
        ("a", "time_axis", "Valid/known"),
        ("question_mark", "help", "Keys"),
        # Priority, or the screen's own tab bindings (focus cycling) win.
        Binding("tab", "next_lens", "Next lens", priority=True),
        Binding("shift+tab", "previous_lens", "Previous lens", priority=True, show=False),
        Binding("j", "next_lens", "Next lens", show=False),
        Binding("k", "previous_lens", "Previous lens", show=False),
        Binding("r", "reset_view", "Reset view", show=False),
        Binding("escape", "deselect", "Deselect", show=False),
        Binding("comma", "step_time(-1)", "Earlier", show=False),
        Binding("full_stop", "step_time(1)", "Later", show=False),
        Binding("less_than_sign", "step_time(-10)", "Much earlier", show=False),
        Binding("greater_than_sign", "step_time(10)", "Much later", show=False),
        # After a click on the time bar the arrows move it, in every lens,
        # until a click on the plot hands them back (see check_action).
        Binding("left", "arrow_time(-1)", "Earlier", show=False, priority=True),
        Binding("right", "arrow_time(1)", "Later", show=False, priority=True),
        Binding("shift+left", "arrow_time(-10)", "Much earlier", show=False, priority=True),
        Binding("shift+right", "arrow_time(10)", "Much later", show=False, priority=True),
    ]

    def __init__(
        self,
        graph: Graph,
        at: At,
        color_by: str = "type",
        theme: Theme = THEMES[DEFAULT_THEME],
        rels: list[str] | None = None,
        group: Callable[[str], str] | None = None,
        weight: Callable[[Node], float] | None = None,
        label: Callable[[Node], str] | None = None,
        tree: str | None = None,
        lenses: list[Lens] | None = None,
        hide: list[str] | None = None,
    ) -> None:
        """One lens from the arguments, or several given as ``lenses`` (the
        first is shown; j/k and tab switch). ``tree`` names a hierarchy
        relation: the layout is then the still balloon tree of
        :func:`tree_layout` rather than a force simulation; ``hide`` leaves
        out node types."""
        super().__init__()
        self.graph = graph
        self.at = at
        self.lenses = lenses or [Lens("Graph", "", rels, tree, color_by, theme, group, weight, label, hide=hide)]
        tui.style(self, self.lenses[0].theme.chrome)
        self.pictures: dict[int, Picture] = {}
        self.lens_index = 0
        self.switching = False
        self.focus = False  # hovering dims everything but a node's neighbourhood
        self.labels_on = False  # node names drawn on the graph; t toggles
        self.time_keys = False  # the arrows step the time bar: set by a click on it, cleared by one on the plot
        self.pinned: int | None = None  # the node focus is held on, after a click
        records = [r for node in graph.nodes.values() for r in (*node.facts, *node.edges) if r.superseded_at is None]
        # The time bar moves valid time by default; `a` flips it to knowledge time.
        self.spans = {axis: time_span(records, axis) for axis in TIME_AXES}
        self.time_axis = "valid" if self.spans["valid"] or not self.spans["known"] else "known"
        self.span = self.spans[self.time_axis]
        self.title = f"bibliotheke — {graph.root.name}"
        self.filters = ""
        self.sub_title = ""
        self.playing: bool = False
        self._adopt(self._picture(0))
        self._set_at(at)

    # -- lenses -------------------------------------------------------------

    def _picture(self, i: int) -> Picture:
        """The lens built, once; the scene holds everything that was ever
        valid or known (changed time still applies), laid out once, so the
        time slider, on either axis, only hides nodes and fades edges and
        nothing jumps."""
        if i not in self.pictures:
            lens = self.lenses[i]
            if lens.globe:  # placed as of the time shown; a new time builds it again
                from bibliotheke.globe import GlobePicture

                self.pictures[i] = GlobePicture(self.graph, self.at, lens)
            else:
                self.pictures[i] = Picture(self.graph, replace(self.at, valid_at=None, known_at=None), lens)
        return self.pictures[i]

    def _adopt(self, picture: Picture) -> None:
        """Make a picture the one shown."""
        self.picture = picture
        self.look = picture.look
        if tui.THEMES.get(self.look.chrome, tui.THEME).name != self.theme:
            tui.style(self, self.look.chrome)  # a lens in another theme brings its chrome
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
        _show_labels(picture, self.labels_on)

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
            picture = self._picture(i)
            if getattr(picture, "globe", False) and picture.at != self.at:
                del self.pictures[i]  # built for another time
                picture = self._picture(i)
            await self._show_picture(picture)
        finally:
            self.switching = False

    async def _show_picture(self, picture: Picture) -> None:
        """Put a picture on screen: a new plot widget in place of the old.
        Focus goes to the new plot unless the time bar holds it (a globe is
        rebuilt as the bar moves; the bar keeps the keys)."""
        self._adopt(picture)
        old = self.query_one("#plot", PlotWidget)
        body = self.query_one("#body")
        await old.remove()
        widget = PlotWidget(self.plot, id="plot", pickable=True)
        await body.mount(widget, before=0)
        if self.time_keys:
            try:
                self.query_one("#time", TimeSlider).focus()
            except NoMatches:
                widget.focus()
        else:
            widget.focus()
        self._set_at(self.at)
        self._show_overview()

    async def _rebuild_globe(self) -> None:
        """The time moved on a globe lens: place everything again as of now."""
        self._globe_timer = None
        if not getattr(self.picture, "globe", False) or self.picture.at == self.at or self.switching:
            return
        self.switching = True
        try:
            self.pictures.pop(self.lens_index, None)
            await self._show_picture(self._picture(self.lens_index))
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
                shown = getattr(self.at, f"{self.time_axis}_at")
                when = timeutil.start(shown) if shown else None
                yield TimeSlider(first, last, when, id="time", axis=self.time_axis)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#plot", PlotWidget).focus()
        self._show_overview()
        # One layout step per frame: the unfolding reads as motion only
        # when the frames come fast, and plotui draws them small while the
        # nodes move, so 60 Hz is affordable. (A globe lens has nothing to
        # step; the tick stands down for it.)
        self.set_interval(1 / TICK_FPS, self._tick)
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
        if getattr(self.picture, "globe", False):
            if self.picture.at != at and self.is_running:
                # Placing is cheap but a new plot is a new widget: wait for
                # the slider to rest a moment rather than rebuild per step.
                if getattr(self, "_globe_timer", None) is not None:
                    self._globe_timer.stop()
                self._globe_timer = self.set_timer(0.15, self._rebuild_globe)
            return
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

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        """The arrow bindings apply only in time mode; otherwise the key goes
        on to the focused widget (the plot turns)."""
        if action == "arrow_time":
            return self.time_keys
        return True

    def action_arrow_time(self, steps: int) -> None:
        self.action_step_time(steps)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        """A click on the time bar hands it the arrows; one anywhere else
        (the plot, the legend, the sidebar) gives them back."""
        widget = event.widget if hasattr(event, "widget") else None
        on_bar = widget is not None and (isinstance(widget, TimeSlider) or any(isinstance(a, TimeSlider) for a in widget.ancestors))
        self.time_keys = on_bar

    @on(TimeSlider.Changed)
    def _time_changed(self, message: TimeSlider.Changed) -> None:
        if message.by_user:
            self.playing = False
        self._set_at(replace(self.at, **{f"{self.time_axis}_at": timeutil.canonical(message.when)}))
        self._show_overview()

    def action_time_axis(self) -> None:
        """Flip the time bar between valid time and knowledge time. The axis
        left behind stops filtering; the new one starts at its end (now)."""
        other = "known" if self.time_axis == "valid" else "valid"
        if not self.spans[other]:
            self.notify(f"Nothing in this graph has a {other} time.", timeout=3)
            return
        try:
            slider = self.query_one("#time", TimeSlider)
        except NoMatches:
            return
        self.playing = False
        old, self.time_axis = self.time_axis, other
        self.span = self.spans[other]
        first, last = self.span
        slider.retarget(first, last, other)
        self._set_at(replace(self.at, **{f"{old}_at": None, f"{other}_at": timeutil.canonical(last)}))
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
        if getattr(self.picture, "globe", False):
            lines = self.picture.overview()
        else:
            nodes = sum(self.node_visible)
            edges = sum(1 for (a, b), valid in zip(self.scene.edges, self.edge_valid) if valid and self.node_visible[a] and self.node_visible[b])
            lines = [f"{nodes} nodes, {edges} edges"]
        if self.filters:
            lines.append(escape(self.filters))
        if len(self.lenses) > 1:
            lines += ["", "[bold]Lenses[/bold]  j / k or tab / shift+tab to cycle"]
            for i, lens in enumerate(self.lenses, 1):
                mark = "●" if i - 1 == self.lens_index else "○"
                lines.append(f"{mark} {i} [bold]{escape(lens.name)}[/bold]  [dim]{escape(lens.description)}[/dim]")
        lines += ["", HINT]
        lines += [
            "",
            "[bold]Keys[/bold]",
            "drag: rotate   shift+drag: pan   scroll: zoom",
            "1–9: show or hide a legend row (or click it)",
            "f: focus mode (hover dims the rest; click pins)",
            "t: show or hide node names",
            "r: reset view   i: details   esc: clear   q: quit",
        ]
        if self.span:
            lines += ["", "Drag the time bar, or , and . to step,", "< and > to jump, space to play;",
                      "after a click on the bar, ←/→ step it (shift: jump)", "until you click the plot;",
                      f"a to move {'known' if self.time_axis == 'valid' else 'valid'} time instead of {self.time_axis}."]
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
        if widget is None:
            return
        element = message.element
        if getattr(self.picture, "globe", False):
            # The place under the pointer lights its arcs; off every place,
            # the clicked one (if any) keeps them lit.
            if element is not None and element[0] == "node" and element[1] < len(self.picture.places):
                place = self.picture.places[element[1]]
                self.sub_title = f"{place.node.name} · {len(place.members)}"
                lit = element[1]
            else:
                self.sub_title = self._subtitle()
                lit = self.pinned
            if self.picture.light(lit):
                widget.invalidate()
            return
        if self.handle is None:
            return
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
        if widget is None or self.handle is None:
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

    def action_toggle_labels(self) -> None:
        """Show or hide the node names drawn on the graph (off by default)."""
        self.labels_on = not self.labels_on
        if _show_labels(self.picture, self.labels_on):
            self.query_one("#plot", PlotWidget).invalidate()

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
        if self.handle is None or message.entry is None or message.entry >= len(self.picture.legend_rows):
            return
        category = self.picture.legend_rows[message.entry]
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
        globe = getattr(self.picture, "globe", False)
        if element is None:
            self._show_overview()
            if globe:
                self.pinned = None
                self._light_globe(None)
            elif self.focus:
                self.pinned = None
                self._light(None)
        elif globe:
            if element[0] == "node" and element[1] < len(self.picture.places):
                place = self.picture.places[element[1]]
                self._set_sidebar(place.node.name, "\n".join(self.picture.place_lines(element[1], self.at)))
                self._show_sidebar(True)
                self.pinned = element[1]
                self._light_globe(self.pinned)
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

    def _light_globe(self, i: int | None) -> None:
        """Light the arcs of place `i` on a globe lens (`None`: all at rest)."""
        if self.picture.light(i) and (widget := self._widget()):
            widget.invalidate()

    def action_deselect(self) -> None:
        """Back to the clean view: nothing selected or pinned, details panel away."""
        self.plot.set_selected(None)
        self.pinned = None
        if getattr(self.picture, "globe", False):
            self._light_globe(None)
        elif self.focus:
            self._light(None)
        self._show_overview()
        self._show_sidebar(False)

    def action_reset_view(self) -> None:
        self.plot.reset()
        if getattr(self.picture, "globe", False):
            self.picture.look_home()
        if widget := self._widget():
            widget.invalidate()


def _brief(stamp: str) -> str:
    """A time value for the header: a timestamp shows as its day."""
    return stamp[:10] if "T" in stamp else stamp


def _span(record) -> str:
    if not record.valid_from and not record.valid_to:
        return ""
    return f" [dim]({record.valid_from or '…'} → {record.valid_to or '…'})[/dim]"
