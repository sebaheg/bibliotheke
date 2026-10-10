"""The globe view: located nodes on a plotui globe in a Textual app.

Each place is a point sized by how many located nodes it holds, labelled with
its name and that count. Arcs join places linked through a placeless node
(see `bibliotheke.geo`), brighter and wider the more nodes link them.
"""

from __future__ import annotations

import math

from plotui import Plot
from plotui.textual import PlotWidget
from rich.markup import escape
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Header, Static

from bibliotheke.geo import VIA, WHO, GeoScene, Place, build_geo
from bibliotheke.graph import At, Graph

CANVAS = (17, 18, 21)
POINT = (255, 209, 102)
# (fewest linking nodes, legend label, colour, width, height): one arc trace per
# tier, so a legend click can switch the faint ones off
TIERS = [
    (1, "linked once", (52, 120, 160), 1.0, 0.05),
    (2, "linked 2-3 times", (79, 195, 247), 1.6, 0.08),
    (4, "linked 4+ times", (190, 236, 255), 2.4, 0.11),
]
NEAR_KM = 150  # places closer than this share one label
HINT = "Drag to turn the globe, scroll to zoom.\nClick a place to see who is there."


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance between two `(lat, lon)` points."""
    (la1, lo1), (la2, lo2) = (tuple(map(math.radians, p)) for p in (a, b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(min(1.0, math.sqrt(h)))


def label_groups(places: list[Place]) -> list[list[int]]:
    """Places close enough that their labels would collide, grouped; the
    busiest place of a group comes first and carries the label."""
    order = sorted(range(len(places)), key=lambda i: (-len(places[i].members), places[i].node.name))
    groups: list[list[int]] = []
    for i in order:
        here = (places[i].lat, places[i].lon)
        for group in groups:
            lead = places[group[0]]
            if distance_km(here, (lead.lat, lead.lon)) < NEAR_KM:
                group.append(i)
                break
        else:
            groups.append([i])
    return groups


def arc_tier(count: int) -> int:
    return max(i for i, (least, *_) in enumerate(TIERS) if count >= least)


class GlobeView(App):
    CSS = """
    HeaderIcon { display: none; }  /* the command-palette button; ^p still opens it */
    #body { height: 1fr; }
    #plot { width: 1fr; height: 1fr; }
    #sidebar { width: 44; height: 1fr; background: $panel; border-left: thick $accent; padding: 0 1; }
    #sb-title { color: $accent; text-style: bold; padding: 1 0; }
    """
    BINDINGS = [("q", "quit", "Quit"), ("r", "reset_view", "Reset view"), ("escape", "deselect", "Deselect")]

    def __init__(
        self,
        graph: Graph,
        at: At,
        who: tuple[str, ...] = WHO,
        via: tuple[str, ...] = VIA,
        theme: str = "dark",
    ) -> None:
        super().__init__()
        self.graph = graph
        self.at = at
        self.who = who
        self.scene: GeoScene = build_geo(graph, at, who, via)
        self.title = f"bibliotheke — {graph.root.name}"
        self.filters = "  ".join(
            f"{label} {value}"
            for label, value in (("valid", at.valid_at), ("known", at.known_at), ("changed", at.changed_at))
            if value
        )
        self.sub_title = self.filters
        self.plot = Plot()
        self.plot.set_chrome(canvas=CANVAS, bg=(31, 32, 36), frame=(57, 58, 62))
        self.plot.add_globe(theme=theme)
        self.plot.set_legend_corner("top-left")
        places = self.scene.places
        if not places:
            return
        lats = [p.lat for p in places]
        lons = [p.lon for p in places]
        self.plot.add_geo_points(
            lats,
            lons,
            color=POINT,
            sizes=[2.2 + 1.3 * math.sqrt(len(p.members)) for p in places],
            name=f"{' / '.join(who)} by place",
        )
        for tier, (_, label, color, width, height) in enumerate(TIERS):
            pairs = [pair for pair, via_nodes in self.scene.links.items() if arc_tier(len(via_nodes)) == tier]
            if not pairs:
                continue
            self.plot.add_geo_arcs(
                [places[a].lat for a, _ in pairs],
                [places[a].lon for a, _ in pairs],
                [places[b].lat for _, b in pairs],
                [places[b].lon for _, b in pairs],
                color=color,
                width=width,
                height=height,
                name=label,
            )
        groups = label_groups(places)
        self.plot.add_geo_labels(
            [places[g[0]].lat for g in groups],
            [places[g[0]].lon for g in groups],
            [", ".join(f"{places[i].node.name} · {len(places[i].members)}" for i in g) for g in groups],
            size=0.9,
        )
        busiest = max(places, key=lambda p: len(p.members))
        self.plot.look_at(busiest.lat, busiest.lon)

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

    def _set_sidebar(self, title: str, body: str) -> None:
        self.query_one("#sb-title", Static).update(escape(title))
        self.query_one("#sb-body", Static).update(body)

    def _label(self, node_id: str) -> str:
        return escape(self.graph.nodes[node_id].name)

    def _show_overview(self) -> None:
        scene = self.scene
        people = {m for p in scene.places for m in p.members}
        lines = [f"{len(scene.places)} places, {len(people)} located, {len(scene.links)} links"]
        if self.filters:
            lines.append(escape(self.filters))
        lines += ["", HINT]
        if scene.unplaced:
            lines += ["", f"[bold]No place known[/bold] ({len(scene.unplaced)})"]
            lines += [f"[dim]{self._label(n)}[/dim]" for n in scene.unplaced]
        self._set_sidebar(self.graph.root.name, "\n".join(lines))

    def _show_place(self, i: int) -> None:
        place = self.scene.places[i]
        country = next((f.value for f in self.graph.facts(place.node.id, self.at) if f.key == "country"), "")
        lines = [f"[dim]{escape(str(country))}  {place.lat:.2f}, {place.lon:.2f}[/dim]", ""]
        lines.append(f"[bold]{' / '.join(self.who).capitalize()}[/bold] ({len(place.members)})")
        for member in place.members:
            via = [
                self._label(e.to)
                for e in self.graph.out_edges(member, self.at)
                if e.rel in ("based_in", "affiliated_with") and e.to in self.graph.nodes
            ]
            lines.append(f"{self._label(member)}" + (f" [dim]· {', '.join(via)}[/dim]" if via else ""))
        work = sorted(
            {other.id for m in place.members for other, e in self.graph.in_edges(m, self.at)}
            | {e.to for m in place.members for e in self.graph.out_edges(m, self.at) if e.rel == "authored"},
            key=lambda n: self.graph.nodes[n].name,
        )
        work = [n for n in work if self.graph.nodes[n].type not in ("city", "organisation")]
        if work:
            lines += ["", f"[bold]Their work[/bold] ({len(work)})"] + [self._label(n) for n in work]
        linked = sorted(
            ((b if a == i else a, nodes) for (a, b), nodes in self.scene.links.items() if i in (a, b)),
            key=lambda kv: -len(kv[1]),
        )
        if linked:
            lines += ["", "[bold]Linked places[/bold]"]
            lines += [f"{escape(self.scene.places[j].node.name)} [dim]· {len(nodes)}[/dim]" for j, nodes in linked]
        self._set_sidebar(place.node.name, "\n".join(lines))

    @on(PlotWidget.ElementHovered)
    def _hovered(self, message: PlotWidget.ElementHovered) -> None:
        element = message.element
        if element is not None and element[0] == "node" and element[1] < len(self.scene.places):
            place = self.scene.places[element[1]]
            self.sub_title = f"{place.node.name} · {len(place.members)}"
        else:
            self.sub_title = self.filters

    @on(PlotWidget.ElementPicked)
    def _picked(self, message: PlotWidget.ElementPicked) -> None:
        element = message.element
        if element is not None and element[0] == "node" and element[1] < len(self.scene.places):
            self._show_place(element[1])
        else:
            self._show_overview()

    def action_deselect(self) -> None:
        self.plot.set_selected(None)
        self.query_one("#plot", PlotWidget).invalidate()
        self._show_overview()

    def action_reset_view(self) -> None:
        self.plot.reset()
        if self.scene.places:
            busiest = max(self.scene.places, key=lambda p: len(p.members))
            self.plot.look_at(busiest.lat, busiest.lon)
        self.query_one("#plot", PlotWidget).invalidate()
