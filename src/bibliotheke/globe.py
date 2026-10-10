"""A globe lens: located nodes on a plotui globe, one picture in the graph view.

Each place is a point sized by how many located nodes it holds, coloured by
what most of them are (the lens's ``color_by``), labelled with its name and
that count. Arcs join linked places (see `bibliotheke.geo`): quiet at rest,
a little stronger the more links they carry, rising with their length; the
place under the pointer, or the one clicked, lights its own. A lens declares ``layout: globe``; a graph
with coordinates and no globe lens gets one (`bibliotheke.lenses`).
"""

from __future__ import annotations

import math

from collections import Counter
from typing import TYPE_CHECKING

from plotui import Plot
from rich.markup import escape

from bibliotheke.geo import VIA, GeoScene, Place, build_geo, infer
from bibliotheke.graph import At, Graph

if TYPE_CHECKING:
    from bibliotheke.view import Lens

# (fewest linking nodes, legend label, opacity, width): one arc trace per
# tier, so a legend click can switch the faint ones off. Arcs are one quiet
# colour; the places carry the picture, and a hovered or clicked place
# lights its own arcs (`GlobePicture.light`).
TIERS = [
    (1, "linked once", 60, 1.0),
    (2, "linked 2-3 times", 105, 1.3),
    (4, "linked 4+ times", 150, 1.6),
]
ARC = (120, 200, 230)  # an arc at rest
LIT = (225, 245, 255)  # an arc of the place under the pointer
DIM = 16  # the opacity of every other arc meanwhile
# An arc rises in proportion to its length (globe radii per radian of arc),
# up to a cap: a hop between neighbouring cities hugs the surface and only
# the intercontinental ones lift off it.
RISE = 0.1
MAX_RISE = 0.2
NEAR_KM = 150  # places closer than this share one label
HINT = "Drag to turn the globe, scroll to zoom.\nHover a place to light its links; click it to see what is there."


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


def arc_height(a: tuple[float, float], b: tuple[float, float]) -> float:
    """How high the arc from `a` to `b` rises at its middle, in globe radii."""
    return min(MAX_RISE, RISE * distance_km(a, b) / 6371)


def arc_tier(count: int) -> int:
    return max(i for i, (least, *_) in enumerate(TIERS) if count >= least)


class GlobePicture:
    """A globe lens applied to a graph at one time: the places, their plot,
    and a :class:`~bibliotheke.view.Scene` whose nodes are the places in the
    order the plot numbers its points, so the graph view's hover, click and
    sidebar work on it as on any picture. Nothing is laid out; a new time
    builds a new picture."""

    globe = True

    def __init__(self, graph: Graph, at: At, lens: Lens) -> None:
        from bibliotheke.view import THEMES, Scene, _category, category_colors, node_size

        theme = lens.theme
        self.lens = lens
        self.look = theme
        self.graph = graph
        self.at = at
        self.labels = None  # place names are drawn by the plot itself
        self.labels_shown = False
        inferred_who, inferred_via = infer(graph)
        self.who = tuple(lens.place or inferred_who)
        self.via = tuple(lens.via or inferred_via or VIA)
        arcs = lens.arcs if isinstance(lens.arcs, str) else tuple(lens.arcs)
        geo: GeoScene = build_geo(graph, at, self.who, self.via, arcs, tuple(lens.hide or ()))
        self.geo = geo
        group = lens.group or (lambda category: category)
        # A place is coloured by what most of what it holds is.
        raw = []
        for place in geo.places:
            counts = Counter(group(_category(graph, graph.nodes[m], at, lens.color_by)) for m in place.members)
            raw.append(min(counts, key=lambda c: (-counts[c], c)) if counts else group(place.node.type))
        pool = [n for n in graph.nodes.values() if n.type in self.who]
        colors = category_colors(graph, pool, lens.color_by, theme, group)
        for category in raw:
            colors.setdefault(category, theme.palette[len(colors) % len(theme.palette)])
        rows = sorted(set(raw))
        # Points in legend order: the plot numbers them trace by trace.
        self.order = [i for row in rows for i, c in enumerate(raw) if c == row]
        at_order = {geo_i: k for k, geo_i in enumerate(self.order)}
        self.places: list[Place] = [geo.places[i] for i in self.order]
        self.links = {
            tuple(sorted((at_order[a], at_order[b]))): nodes for (a, b), nodes in geo.links.items()
        }
        categories = [raw[i] for i in self.order]
        self.scene = Scene(
            [p.node for p in self.places],
            [],
            [],
            categories,
            {row: colors[row] for row in rows},
            [float(len(p.members)) for p in self.places],
            [p.node.name for p in self.places],
        )
        self.legend_rows = [row for row in rows if lens.legend is None or row in lens.legend]
        self.handle = None  # not a graph trace: the view's graph-only calls stand down
        self.layout = None
        self.still: list = []
        self.energy = 0.0
        self.base: list = []
        self.arcs: list[tuple[int, int, list[tuple[int, int]]]] = []  # (trace, opacity, place pairs)
        self.lit: int | None = None
        self.plot = Plot()
        canvas = theme.canvas or THEMES["plotui"].canvas
        lift = tuple(min(255, c + 14) for c in canvas)
        self.plot.set_chrome(
            canvas=canvas,
            bg=theme.legend_bg or lift,
            frame=theme.frame or tuple(min(255, c + 40) for c in canvas),
            grid=theme.grid,
            ink=theme.ink,
            ink_bright=theme.ink_bright,
        )
        self.plot.add_globe(theme=theme.globe)
        self.plot.set_legend_corner("top-left")
        if hasattr(Plot, "legend_keys"):
            self.plot.legend_keys = True
        if not self.places:
            return
        for row in rows:
            members = [p for p, c in zip(self.places, categories) if c == row]
            self.plot.add_geo_points(
                [p.lat for p in members],
                [p.lon for p in members],
                color=colors[row],
                sizes=[node_size(theme, len(p.members)) for p in members],
                name=row if row in self.legend_rows else None,
            )
        for tier, (_, label, alpha, width) in enumerate(TIERS):
            pairs = [pair for pair, nodes in self.links.items() if arc_tier(len(nodes)) == tier]
            if not pairs:
                continue
            ends = [((self.places[a].lat, self.places[a].lon), (self.places[b].lat, self.places[b].lon)) for a, b in pairs]
            handle = self.plot.add_geo_arcs(
                [a[0] for a, _ in ends],
                [a[1] for a, _ in ends],
                [b[0] for _, b in ends],
                [b[1] for _, b in ends],
                # the legend swatch: the tier's colour at rest, on the canvas
                color=tuple(round(c * alpha / 255 + k * (1 - alpha / 255)) for c, k in zip(ARC, canvas)),
                width=width,
                height=[arc_height(a, b) for a, b in ends],
                name=label if lens.legend is None else None,
            )
            self.arcs.append((handle, alpha, pairs))
        self.light(None)
        groups = label_groups(self.places)[: max(1, theme.labels)]
        self.plot.add_geo_labels(
            [self.places[g[0]].lat for g in groups],
            [self.places[g[0]].lon for g in groups],
            [", ".join(f"{self.places[i].node.name} · {len(self.places[i].members)}" for i in g) for g in groups],
            size=0.9,
        )
        self.look_home()

    def look_home(self) -> None:
        """Turn the globe to the busiest place."""
        if self.places:
            busiest = max(self.places, key=lambda p: len(p.members))
            self.plot.look_at(busiest.lat, busiest.lon)

    def light(self, i: int | None) -> bool:
        """Light the arcs of place `i` and dim every other one, or put them
        all back at rest (`None`); True when that changed the picture."""
        if i == self.lit and self.arcs and i is not None:
            return False
        changed = i != self.lit
        self.lit = i
        for handle, alpha, pairs in self.arcs:
            if i is None:
                runs = [(*ARC, alpha)] * len(pairs)
            else:
                runs = [(*LIT, 255) if i in pair else (*ARC, DIM) for pair in pairs]
            self.plot.set_line_run_rgba(handle, runs)
        return changed

    def positions(self) -> tuple[list[float], ...]:
        return ([], [], [])

    # -- what the sidebar says -------------------------------------------------

    def overview(self) -> list[str]:
        placed = {m for p in self.places for m in p.members}
        lines = [
            f"{len(self.places)} places, {len(placed)} placed, {len(self.links)} links",
            f"[dim]placing {escape(', '.join(self.who) or 'nothing')} via {escape(', '.join(self.via))}[/dim]",
        ]
        if self.geo.unplaced:
            lines.append(f"[dim]{len(self.geo.unplaced)} with no place known[/dim]")
        return lines

    def place_lines(self, i: int, at: At) -> list[str]:
        """A place: where it is, what is there by type, the places it links to."""
        graph = self.graph
        place = self.places[i]
        lines = [f"[dim]{escape(place.node.type)} · {place.lat:.2f}, {place.lon:.2f}[/dim]"]
        by_type: dict[str, list[str]] = {}
        for member in place.members:
            by_type.setdefault(graph.nodes[member].type, []).append(member)
        for type, members in sorted(by_type.items()):
            lines += ["", f"[bold]{escape(type)}[/bold] ({len(members)})"]
            for member in sorted(members, key=lambda m: graph.nodes[m].name):
                through = [
                    escape(graph.nodes[e.to].name)
                    for e in graph.out_edges(member, at)
                    if e.rel in self.via and e.to in graph.nodes and e.to != place.node.id
                ]
                lines.append(escape(graph.nodes[member].name) + (f" [dim]· {', '.join(through)}[/dim]" if through else ""))
        linked = sorted(
            ((b if a == i else a, nodes) for (a, b), nodes in self.links.items() if i in (a, b)),
            key=lambda kv: -len(kv[1]),
        )
        if linked:
            lines += ["", "[bold]Linked places[/bold]"]
            lines += [f"{escape(self.places[j].node.name)} [dim]· {len(nodes)}[/dim]" for j, nodes in linked]
        return lines
