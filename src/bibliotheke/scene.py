"""What the graph view draws, without drawing it: the themes, the scene a
lens picks out of a graph at a time, the layouts (a force simulation's
edges, the balloon tree), colours, sizes and labels, and :class:`Lens`.

``bib view`` puts these on a plotui plot in a Textual app (`bibliotheke.view`);
``bib site`` writes them for a browser (`bibliotheke.site`). Nothing here
imports plotui, so the site builds with any plotui release."""

from __future__ import annotations

import math
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

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
