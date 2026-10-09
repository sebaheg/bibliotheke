"""The interactive graph view: a plotui 3D force-directed graph in a Textual app."""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, field

from plotui import ForceLayout, Plot
from plotui.textual import PlotWidget
from rich.markup import escape
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Footer, Header, Static

from bibliotheke.graph import At, Graph
from bibliotheke.model import Edge, Node

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
    labels: int = 0  # label this many of the best-connected nodes
    label_size: float = 1.0
    overrides: dict[str, RGB] = field(default_factory=dict)


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
HINT = "Hover a node to see its neighbourhood.\nClick a node or an edge to inspect it."


@dataclass
class Scene:
    """What the view draws: the nodes and edges in view, as index lists."""

    nodes: list[Node]
    edges: list[tuple[int, int]]
    edge_records: list[Edge]
    categories: list[str]
    legend: dict[str, tuple[int, int, int]]


def build_scene(graph: Graph, at: At, color_by: str = "type", theme: Theme = THEMES["classic"]) -> Scene:
    nodes = sorted(graph.list_nodes(at), key=lambda n: n.id)
    index = {node.id: i for i, node in enumerate(nodes)}
    edges, edge_records = [], []
    for node in nodes:
        for edge in graph.out_edges(node.id, at):
            if edge.to in index and edge.to != node.id:
                edges.append((index[node.id], index[edge.to]))
                edge_records.append(edge)
    categories = [_category(graph, node, at, color_by) for node in nodes]
    # Colours come from every category the graph has ever had, so a category
    # keeps its colour whichever point in time is being viewed.
    known = {node.type for node in graph.nodes.values()}
    if color_by != "type":
        known |= {str(f.value) for n in graph.nodes.values() for f in n.facts if f.key == color_by}
        known |= {e.to for n in graph.nodes.values() for e in n.edges if e.rel == color_by}
    palette = theme.palette
    colors = {category: palette[i % len(palette)] for i, category in enumerate(sorted(known))}
    colors.update({k: v for k, v in theme.overrides.items() if k in colors})
    legend = {category: colors[category] for category in sorted(set(categories))}
    return Scene(nodes, edges, edge_records, categories, legend)


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


def theme_for(name: str, colors: dict[str, str] | None = None, labels: int | None = None) -> Theme:
    """A built-in theme, with per-category colour overrides (``#rrggbb``) and
    the number of nodes to label."""
    if name not in THEMES:
        raise ValueError(f"unknown theme {name!r}; choose from {', '.join(THEMES)}")
    changes: dict = {}
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


def label_texts(nodes: list[Node], degree: list[int], count: int) -> list[str]:
    """Short names for the ``count`` best-connected nodes, '' for the rest."""
    ranked = sorted(range(len(nodes)), key=lambda i: (-degree[i], nodes[i].id))[:count]
    chosen = {i for i in ranked if degree[i] > 0}
    return [short_name(node.name) if i in chosen else "" for i, node in enumerate(nodes)]


def node_size(theme: Theme, degree: int) -> float:
    if theme.styled:
        return min(theme.size_max, theme.size_min + theme.size_step * math.sqrt(degree))
    return theme.size_min + min(degree, 8) * theme.size_step


class GraphView(App):
    CSS = """
    #body { height: 1fr; }
    #plot { width: 1fr; height: 1fr; }
    #sidebar { width: 44; height: 1fr; background: $panel; border-left: thick $accent; padding: 0 1; }
    #sb-title { color: $accent; text-style: bold; padding: 1 0; }
    """
    BINDINGS = [("q", "quit", "Quit"), ("r", "reset_view", "Reset view"), ("escape", "deselect", "Deselect")]

    def __init__(self, graph: Graph, at: At, color_by: str = "type", theme: Theme = THEMES["map"]) -> None:
        super().__init__()
        if theme.styled and not hasattr(Plot, "set_graph_style"):
            theme = THEMES["classic"]  # a plotui without graph styling
        self.graph = graph
        self.at = at
        self.look = theme
        self.scene = build_scene(graph, at, color_by, theme)
        self.title = f"bibliotheke — {graph.root.name}"
        self.filters = "  ".join(
            f"{label} {value}"
            for label, value in (("valid", at.valid_at), ("known", at.known_at), ("changed", at.changed_at))
            if value
        )
        self.sub_title = self.filters
        self.plot = Plot()
        self.handle: int | None = None
        self.energy = float("inf")
        scene = self.scene
        if not scene.nodes:
            return
        self.base = [scene.legend[c] for c in scene.categories]
        degree = [0] * len(scene.nodes)
        for a, b in scene.edges:
            degree[a] += 1
            degree[b] += 1
        self.adjacent = [{i} for i in range(len(scene.nodes))]
        for a, b in scene.edges:
            self.adjacent[a].add(b)
            self.adjacent[b].add(a)
        n = len(scene.nodes)
        force_edges = layout_edges(n, scene.edges)
        self.layout = ForceLayout(n + (len(force_edges) > len(scene.edges)), force_edges, seed=SEED)
        for _ in range(30):  # warm up past the initial explosion
            self.layout.step()
        self.plot.set_show_box(False)
        reach = 0.82 if theme.styled else 1.15
        self.plot.set_bounds((-reach, -reach, -reach), (reach, reach, reach))
        if theme.canvas:
            lift = tuple(min(255, c + 14) for c in theme.canvas)
            self.plot.set_chrome(canvas=theme.canvas, bg=lift, frame=tuple(min(255, c + 40) for c in theme.canvas))
        xs, ys, zs = self._positions()
        self.handle = self.plot.add_graph3d(
            xs, ys, zs,
            edges=scene.edges,
            node_colors=self.base,
            node_sizes=[node_size(theme, d) for d in degree],
            edge_colors=None if theme.styled else [theme.edge] * len(scene.edges),
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
                self.plot.set_graph_labels(self.handle, label_texts(scene.nodes, degree, theme.labels))
        self.plot.set_legend_entries(
            [(category, "disc", color, None, True) for category, color in scene.legend.items()]
        )
        self.plot.set_legend_corner("top-left")
        self.hidden_categories: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            yield PlotWidget(self.plot, id="plot", pickable=True)
            with VerticalScroll(id="sidebar"):
                yield Static("", id="sb-title")
                yield Static("", id="sb-body")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#plot", PlotWidget).focus()
        self._show_overview()
        if self.handle is not None:
            self.set_interval(1 / 30, self._tick)

    def _tick(self) -> None:
        if self.energy < SETTLED:
            return
        try:
            widget = self.query_one("#plot", PlotWidget)
        except NoMatches:  # a tick that lands while the app is closing
            return
        self.energy = self.layout.step()
        widget.set_graph_positions(self.handle, *self._positions())

    def _positions(self) -> tuple[list[float], ...]:
        n = len(self.scene.nodes)
        return fit(*(axis[:n] for axis in self.layout.positions()))

    def _set_sidebar(self, title: str, body: str) -> None:
        self.query_one("#sb-title", Static).update(escape(title))
        self.query_one("#sb-body", Static).update(body)

    def _show_overview(self) -> None:
        scene = self.scene
        lines = [f"{len(scene.nodes)} nodes, {len(scene.edges)} edges"]
        if self.filters:
            lines.append(escape(self.filters))
        lines += ["", HINT]
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
        if self.handle is None:
            return
        widget = self.query_one("#plot", PlotWidget)
        element = message.element
        if element is not None and element[0] == "node":
            near = self.adjacent[element[1]]
            node_colors = [c if j in near else _dim(c) for j, c in enumerate(self.base)]
            edge_colors = [self.look.edge if element[1] in pair else self.look.edge_dim for pair in self.scene.edges]
            widget.set_graph_colors(self.handle, node_colors, edge_colors)
            self.sub_title = self.scene.nodes[element[1]].name
        else:
            widget.set_graph_colors(self.handle, list(self.base), self._resting_edges())
            if element is not None:
                a, b = self.scene.edges[element[1]]
                edge = self.scene.edge_records[element[1]]
                self.sub_title = f"{self.scene.nodes[a].name} {edge.rel} {self.scene.nodes[b].name}"
            else:
                self.sub_title = self.filters

    def _resting_edges(self) -> list[RGB] | None:
        """Edge colours when nothing is hovered: their endpoints' (None) when styled."""
        return None if self.look.styled else [self.look.edge] * len(self.scene.edges)

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
        hidden = [c in self.hidden_categories for c in self.scene.categories]
        self.plot.set_graph_hidden(self.handle, hidden if any(hidden) else None)
        self.query_one("#plot", PlotWidget).invalidate()

    @on(PlotWidget.ElementPicked)
    def _picked(self, message: PlotWidget.ElementPicked) -> None:
        element = message.element
        if element is None:
            self._show_overview()
        elif element[0] == "node":
            self._show_node(element[1])
        else:
            self._show_edge(element[1])

    def action_deselect(self) -> None:
        self.plot.set_selected(None)
        self.query_one("#plot", PlotWidget).invalidate()
        self._show_overview()

    def action_reset_view(self) -> None:
        self.plot.reset()
        self.query_one("#plot", PlotWidget).invalidate()


def _span(record) -> str:
    if not record.valid_from and not record.valid_to:
        return ""
    return f" [dim]({record.valid_from or '…'} → {record.valid_to or '…'})[/dim]"
