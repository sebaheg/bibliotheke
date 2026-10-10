"""The graph as a static website: ``bib site`` writes a folder any static
host (GitHub Pages, Cloudflare Pages, a bucket) can serve, with no server
behind it.

The site has two layers. Every node gets a page of its own,
``n/<id>.html``, which reads like ``bib show`` and needs no JavaScript, and
the same as ``n/<id>.json`` (``bib show --json``, every version), so a
person can link a claim and an agent can walk the graph over HTTP. Every
type gets a listing, ``t/<type>.html``, and ``types.html`` lists the types.

``index.html`` is the explorer: the graph's lenses as ``bib view`` draws
them, with the time bar. Everything is computed here, once: each lens's
scene, its layout run to rest, colours, sizes and labels, and the time
windows of every record, so the browser only filters by time and draws
(``graph.json``). A globe lens ships its places and arcs as of now; the
time bar then shows a place while anything placed there is in view.

All links are relative, so the folder works at a domain's root, under a
project path (``user.github.io/repo/``) and from disk.
"""

from __future__ import annotations

import html
import json
import re
import shutil
import time
from collections import Counter
from dataclasses import dataclass, replace
from importlib import resources
from pathlib import Path
from typing import Any

from bibliotheke import export, store, timeutil
from bibliotheke.graph import NOW, At, Graph
from bibliotheke.model import Node, Record

ASSETS = ("explorer.js", "site.css")
LAYOUT_STEPS = 3000  # force layout steps at most; the view settles live, the site at build time
LAYOUT_SECONDS = 60.0  # per lens
DAY = 86400.0


@dataclass
class Built:
    """What ``build`` wrote: for ``bib site`` to report."""

    out: Path
    nodes: int
    lenses: list[str]
    seconds: float


def build(graph: Graph, out: Path, *, title: str | None = None, base_url: str | None = None) -> Built:
    """Write the site for ``graph`` into ``out``, replacing what is there."""
    started = time.monotonic()
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    (out / "n").mkdir(parents=True)
    (out / "t").mkdir()
    title = title or graph.root.resolve().name
    data = explorer_data(graph, title)
    (out / "graph.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    for name in ASSETS:
        (out / name).write_text(_asset(name))
    (out / "index.html").write_text(_asset("explorer.html").replace("__TITLE__", _e(title)))
    (out / ".nojekyll").write_text("")  # Pages serves the folder as it is
    page = Pages(graph, title, data["chrome"])
    for node in graph.nodes.values():
        (out / "n" / f"{node.id}.html").write_text(page.node(node))
        (out / "n" / f"{node.id}.json").write_text(json.dumps(export.node_json(graph, node, None), ensure_ascii=False, indent=1, default=str))
    types = Counter(node.type for node in graph.nodes.values())
    for type in types:
        (out / "t" / f"{type}.html").write_text(page.type_listing(type))
    (out / "types.html").write_text(page.types(types))
    if base_url:
        urls = [f"{base_url.rstrip('/')}/{path}" for path in ("index.html", "types.html")]
        urls += [f"{base_url.rstrip('/')}/n/{node_id}.html" for node_id in sorted(graph.nodes)]
        (out / "sitemap.txt").write_text("\n".join(urls) + "\n")
    return Built(out, len(graph.nodes), [lens["name"] for lens in data["lenses"]], time.monotonic() - started)


def _asset(name: str) -> str:
    return resources.files("bibliotheke").joinpath("site_assets", name).read_text()


# -- the explorer's data ------------------------------------------------------


def explorer_data(graph: Graph, title: str) -> dict[str, Any]:
    """Everything ``index.html`` draws: the nodes with the time windows that
    decide when each is in view, and per lens what it draws and where."""
    from bibliotheke import lenses as declared

    specs = declared.declared(graph)
    lenses = declared.build(graph, specs)
    ids = sorted(graph.nodes)
    index = {node_id: i for i, node_id in enumerate(ids)}
    records = [r for node in graph.nodes.values() for r in (*node.facts, *node.edges) if r.superseded_at is None]
    from bibliotheke.scene import TIME_AXES, time_span

    spans = {}
    for axis in TIME_AXES:
        span = time_span(records, axis)
        spans[axis] = [round(span[0].timestamp() / DAY, 4), round(span[1].timestamp() / DAY, 4)] if span else None
    look = lenses[0].theme if lenses else None
    return {
        "title": title,
        "description": str(graph.marker.get("description") or ""),
        "chrome": chrome(look.chrome if look else "plotui"),
        "spans": spans,
        "nodes": [_node_entry(graph, graph.nodes[node_id]) for node_id in ids],
        "lenses": [
            _globe_lens(graph, lens, index) if lens.globe else _graph_lens(graph, lens, index) for lens in lenses
        ],
    }


def chrome(name: str) -> dict[str, str]:
    """The page's colours: the window around the plot in ``bib view``."""
    from bibliotheke import tui

    if name == "dracula":
        d = tui.DRACULA_COLORS
        return {
            "canvas": d.CANVAS, "surface": d.SURFACE, "panel": d.PANEL, "grid": d.GRID, "frame": d.FRAME,
            "ink": d.COMMENT, "body": d.FOREGROUND, "accent": d.PURPLE, "link": d.CYAN, "mark": d.PINK,
        }
    return {
        "canvas": tui.CANVAS, "surface": tui.SURFACE, "panel": tui.PANEL, "grid": tui.GRID, "frame": tui.FRAME,
        "ink": tui.INK, "body": tui.BODY, "accent": tui.CYAN, "link": tui.CYAN, "mark": tui.PINK,
    }


def _node_entry(graph: Graph, node: Node) -> dict[str, Any]:
    """A node's name and type, and when it is in view: ``w`` holds the time
    window of each current record at either end of it (``[valid from, valid
    to, known at]`` in days since 1970, null for open; absent when it has
    no records at all, so it is always in view), and ``b`` when its
    type's ``starts`` rule has it begin (``-1``: the rule names nothing, so
    under a valid time it never shows). The browser's test is
    ``Graph.list_nodes``."""
    windows = {_window(r) for r in (*graph.facts(node.id), *graph.out_edges(node.id))}
    windows |= {_window(e) for _, e in graph.in_edges(node.id)}
    entry: dict[str, Any] = {"id": node.id, "name": node.name, "type": node.type}
    if node.facts or node.edges or graph.in_edges(node.id, at=None):  # a node with no records is always in view
        entry["w"] = sorted(windows, key=lambda w: [x if x is not None else -1e9 for x in w])
    spec = graph.model.nodes.get(node.type) if graph.model else None
    if spec and spec.starts:
        begins = graph.begins(node.id)
        entry["b"] = _day(timeutil.start(begins)) if begins else -1
    return entry


def _window(record: Record) -> tuple[float | None, float | None, float | None]:
    return (
        _day(timeutil.start(record.valid_from)) if record.valid_from else None,
        _day(timeutil.end(record.valid_to)) if record.valid_to else None,
        _day(timeutil.start(record.known_at)) if record.known_at else None,
    )


def _day(when) -> float:
    return round(when.timestamp() / DAY, 4)


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % tuple(rgb)


def _graph_lens(graph: Graph, lens, index: dict[str, int]) -> dict[str, Any]:
    """A force or tree lens as ``Picture`` builds it, laid out to rest."""
    from plotui import ForceLayout

    from bibliotheke.scene import SEED, SETTLED, build_scene, fit, label_texts, layout_edges, node_size, tree_layout

    theme = lens.theme
    scene = build_scene(
        graph, replace(NOW, valid_at=None, known_at=None), lens.color_by, theme, lens.rels, lens.group, lens.weight,
        lens.label, lens.tree, lens.min_parallel, lens.hide, lens.linked_only,
    )
    n = len(scene.nodes)
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    if n and lens.tree:
        still = tree_layout(graph, scene, lens.tree)
        xs, ys, zs = fit([p[0] for p in still], [p[1] for p in still], [p[2] for p in still])
    elif n:
        springs = sorted({(min(a, b), max(a, b)) for a, b in scene.edges})
        force_edges = layout_edges(n, springs)
        layout = ForceLayout(n + (len(force_edges) > len(springs)), force_edges, seed=SEED)
        started = time.monotonic()
        for _ in range(LAYOUT_STEPS):
            if layout.step() < SETTLED or time.monotonic() - started > LAYOUT_SECONDS:
                break
        xs, ys, zs = fit(*(axis[:n] for axis in layout.positions()))
    rows = list(scene.legend)
    legend = [row for row in rows if lens.legend is None or row in lens.legend]
    labels = label_texts(scene.nodes, scene.weights, theme.labels, scene.labels) if theme.labels else [""] * n
    rels = sorted({e.rel for e in scene.edge_records})
    return {
        "name": lens.name,
        "description": lens.description,
        "layout": "tree" if lens.tree else "force",
        "theme": _theme(theme),
        "categories": [{"name": row, "color": _hex(scene.legend[row]), "legend": row in legend} for row in rows],
        "edgeColors": {rel: _hex(color) for rel, color in theme.edge_colors.items()},
        "rels": rels,
        "nodes": [
            {
                "i": index[node.id],
                "p": [round(xs[k], 4), round(ys[k], 4), round(zs[k], 4)],
                "c": rows.index(scene.categories[k]),
                "s": round(node_size(theme, scene.weights[k]), 2),
                **({"l": labels[k]} if labels[k] else {}),
            }
            for k, node in enumerate(scene.nodes)
        ],
        "edges": [[a, b, rels.index(e.rel), *_window(e)] for (a, b), e in zip(scene.edges, scene.edge_records)],
    }


def _globe_lens(graph: Graph, lens, index: dict[str, int]) -> dict[str, Any]:
    """A globe lens as ``GlobePicture`` builds it, as of now."""
    from bibliotheke.geo import VIA, build_geo, infer
    from bibliotheke.globe import arc_height, label_groups
    from bibliotheke.scene import _category, category_colors, node_size

    theme = lens.theme
    who_inferred, via_inferred = infer(graph)
    who = tuple(lens.place or who_inferred)
    via = tuple(lens.via or via_inferred or VIA)
    arcs = lens.arcs if isinstance(lens.arcs, str) else tuple(lens.arcs)
    geo = build_geo(graph, NOW, who, via, arcs, tuple(lens.hide or ()))
    group = lens.group or (lambda category: category)
    raw = []
    for place in geo.places:
        counts = Counter(group(_category(graph, graph.nodes[m], NOW, lens.color_by)) for m in place.members)
        raw.append(min(counts, key=lambda c: (-counts[c], c)) if counts else group(place.node.type))
    pool = [node for node in graph.nodes.values() if node.type in who]
    colors = category_colors(graph, pool, lens.color_by, theme, group)
    for category in raw:
        colors.setdefault(category, theme.palette[len(colors) % len(theme.palette)])
    rows = sorted(set(raw))
    labelled = {group_[0] for group_ in label_groups(geo.places)[: max(1, theme.labels)]}

    def edge_ids(ids: list[str]) -> list[int]:
        # A link is made by nodes (shared) or by edges (an edge's source node).
        found = []
        for item in ids:
            if item in index:
                found.append(index[item])
            else:
                for node in graph.nodes.values():
                    if any(e.id == item for e in node.edges):
                        found.append(index[node.id])
                        break
        return found

    return {
        "name": lens.name,
        "description": lens.description,
        "layout": "globe",
        "theme": _theme(theme),
        "categories": [{"name": row, "color": _hex(colors[row]), "legend": lens.legend is None or row in lens.legend} for row in rows],
        "placing": {"who": list(who), "via": list(via), "unplaced": len(geo.unplaced)},
        "places": [
            {
                "i": index[place.node.id],
                "lat": place.lat,
                "lon": place.lon,
                "c": rows.index(raw[k]),
                "s": round(node_size(theme, len(place.members)), 2),
                "m": [index[m] for m in place.members],
                **({"l": True} if k in labelled else {}),
            }
            for k, place in enumerate(geo.places)
        ],
        "arcs": [
            {
                "a": a,
                "b": b,
                "h": round(arc_height((geo.places[a].lat, geo.places[a].lon), (geo.places[b].lat, geo.places[b].lon)), 4),
                "by": edge_ids(ids),
            }
            for (a, b), ids in geo.links.items()
        ],
    }


def _theme(theme) -> dict[str, Any]:
    return {
        "canvas": _hex(theme.canvas) if theme.canvas else None,
        "edgeAlpha": theme.edge_alpha,
        "edgeWidth": theme.edge_width,
        "globe": theme.globe,
    }


# -- the pages ----------------------------------------------------------------


def _e(text: Any) -> str:
    return html.escape(str(text), quote=True)


URL = re.compile(r"^https?://\S+$")


class Pages:
    """The plain HTML pages: one per node, one per type, and the types."""

    def __init__(self, graph: Graph, title: str, colors: dict[str, str]) -> None:
        from markdown_it import MarkdownIt

        self.graph = graph
        self.title = title
        self.colors = colors
        self.markdown = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")

    def _page(self, heading: str, body: str, depth: int, active: str = "") -> str:
        up = "../" * depth
        style = ";".join(f"--{key}:{value}" for key, value in self.colors.items())
        nav = "".join(
            f'<a href="{up}{href}"{" class=on" if active == key else ""}>{label}</a>'
            for key, href, label in (("explore", "index.html", "Explore"), ("browse", "types.html", "Browse"))
        )
        return (
            "<!doctype html>\n"
            f'<html lang="en" style="{style}"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{_e(heading)} · {_e(self.title)}</title>"
            f'<link rel="stylesheet" href="{up}site.css"><link rel="icon" href="data:,"></head>'
            f'<body class="page"><header><a class="home" href="{up}index.html">{_e(self.title)}</a><nav>{nav}</nav></header>'
            f"<main>{body}</main></body></html>\n"
        )

    def _link(self, node_id: str, depth: int) -> str:
        node = self.graph.nodes.get(node_id)
        if node is None:
            return _e(node_id)
        return f'<a href="{"../" * depth}n/{_e(node_id)}.html">{_e(node.name)}</a>'

    def node(self, node: Node) -> str:
        graph = self.graph
        start, end = graph.span(node.id)
        when = " – ".join(part for part in (start or "", end or "") if part) if start or end else ""
        parts = [
            f'<p class="meta"><a class="type" href="../t/{_e(node.type)}.html">{_e(node.type)}</a>'
            f' · <code>{_e(node.id)}</code>{f" · {_e(when)}" if when else ""}'
            f' · <a href="../index.html#{_e(node.id)}">in the graph</a> · <a href="{_e(node.id)}.json">json</a></p>',
            f"<h1>{_e(node.name)}</h1>",
        ]
        if node.notes:
            parts.append(f'<div class="notes">{self.markdown.render(node.notes)}</div>')
        facts = graph.facts(node.id)
        if facts:
            rows = "".join(
                f"<tr><th>{_e(f.key)}</th><td>{self._value(f.value)}</td><td class=t>{_e(_span(f))}</td></tr>" for f in facts
            )
            parts.append(f'<h2>Facts</h2><table class="records">{rows}</table>')
        for heading, pairs in (
            ("Edges out", [(e.rel, e.to, e) for e in graph.out_edges(node.id)]),
            ("Edges in", [(e.rel, other.id, e) for other, e in graph.in_edges(node.id)]),
        ):
            if not pairs:
                continue
            by_rel: dict[str, list] = {}
            for rel, other, edge in pairs:
                by_rel.setdefault(rel, []).append((other, edge))
            rows = []
            for rel, items in sorted(by_rel.items()):
                items.sort(key=lambda item: graph.nodes[item[0]].name.casefold() if item[0] in graph.nodes else item[0])
                cells = "".join(
                    f"<li>{self._link(other, 1)}{f' <span class=t>{_e(_span(edge))}</span>' if _span(edge) else ''}</li>"
                    for other, edge in items
                )
                rows.append(f"<tr><th>{_e(rel)}</th><td><ul>{cells}</ul></td></tr>")
            parts.append(f'<h2>{heading}</h2><table class="records">{"".join(rows)}</table>')
        old = [r for r in (*node.facts, *node.edges) if r.superseded_at]
        if old:
            rows = "".join(
                f"<tr><td><code>{_e(r.id)}</code></td><td>{_e(getattr(r, 'key', None) or getattr(r, 'rel', ''))}</td>"
                f"<td>{self._value(r.value) if hasattr(r, 'value') else self._link(r.to, 1)}</td>"
                f"<td class=t>{_e(_span(r))}</td><td class=t>replaced {_e(_short(r.superseded_at))}</td></tr>"
                for r in old
            )
            parts.append(f'<details><summary>{len(old)} earlier versions</summary><table class="records">{rows}</table></details>')
        if graph.has_body(node):
            parts.append(f'<article class="body">{self.markdown.render(graph.body(node.id))}</article>')
        return self._page(node.name, "".join(parts), 1)

    def _value(self, value: Any) -> str:
        text = str(value)
        if URL.match(text):
            return f'<a href="{_e(text)}" rel="nofollow">{_e(text)}</a>'
        return _e(text)

    def type_listing(self, type: str) -> str:
        from bibliotheke.browse import type_description

        graph = self.graph
        nodes = sorted(graph.list_nodes(NOW, type=type), key=lambda n: n.name.casefold())
        rows = "".join(
            f"<tr><td>{self._link(n.id, 1)}</td><td class=t>{_e(graph.span(n.id)[0] or '')}</td></tr>" for n in nodes
        )
        description = type_description(graph, type)
        body = (
            f"<h1>{_e(type)} <span class=count>{len(nodes)}</span></h1>"
            + (f"<p>{_e(description)}</p>" if description else "")
            + f'<input class="filter" placeholder="Filter" oninput="for(const r of document.querySelectorAll(\'tbody tr\'))'
            f'r.hidden=!r.textContent.toLowerCase().includes(this.value.toLowerCase())">'
            + f'<table class="listing"><tbody>{rows}</tbody></table>'
        )
        return self._page(type, body, 1, "browse")

    def types(self, counts: Counter[str]) -> str:
        from bibliotheke.browse import type_description

        rows = "".join(
            f'<tr><td><a href="t/{_e(t)}.html">{_e(t)}</a></td><td class=n>{n}</td><td>{_e(type_description(self.graph, t))}</td></tr>'
            for t, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        )
        description = str(self.graph.marker.get("description") or "")
        body = (
            f"<h1>{_e(self.title)}</h1>"
            + (f"<p>{_e(description)}</p>" if description else "")
            + f'<table class="listing">{rows}</table>'
        )
        return self._page("Browse", body, 0, "browse")


def _short(value: str | None) -> str:
    return (value or "")[:10]


def _span(record: Record) -> str:
    if record.valid_from and record.valid_to:
        return f"{record.valid_from} – {record.valid_to}"
    if record.valid_from:
        return f"from {record.valid_from}"
    if record.valid_to:
        return f"until {record.valid_to}"
    return ""
