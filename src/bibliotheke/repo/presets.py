"""How a repository graph is drawn: a grey skeleton and coloured files.

Directories and the ``contains`` edges between them are the structure, so
they are quiet greys; files are the content, coloured by top-level
directory with a short palette, the long tail folded into one grey; the
``imports`` edges between files are a light tone, so dependencies read as
different from the tree. A file's size is how many files import it.
"""

from __future__ import annotations

from collections import Counter
from typing import Callable

from bibliotheke.graph import Graph
from bibliotheke.model import Node
from bibliotheke.view import RGB, Lens, Theme, theme_for

# Six hues far apart on the wheel, softened for a near-black canvas.
HUES: list[RGB] = [
    (96, 156, 232),  # blue
    (236, 164, 86),  # orange
    (92, 198, 180),  # teal
    (226, 116, 172),  # pink
    (224, 206, 112),  # yellow
    (164, 136, 236),  # purple
]
OTHER: RGB = (118, 122, 134)
DIR: RGB = (84, 88, 98)
AUTHOR: RGB = (240, 240, 244)
# Quiet enough that a bundle of a hundred edges reads as a band, not a beam.
EDGES: dict[str, RGB] = {
    "contains": (74, 77, 86),
    "imports": (142, 146, 162),
    "touched": (110, 96, 140),
    "co_changed": (90, 92, 104),
}
TOP = 6  # directories that get a colour of their own
LABELS = 6
COUPLED = 3  # commits two files must have changed in together to count as coupled


def top_dirs(graph: Graph, count: int = TOP) -> list[str]:
    """The top-level directories with the most files, ever."""
    files: Counter[str] = Counter()
    for node in graph.nodes.values():
        if node.type == "file":
            for fact in node.facts:
                if fact.key == "dir" and fact.superseded_at is None:
                    files[str(fact.value)] += 1
                    break
    return [name for name, _ in files.most_common(count) if name != "/"]


def grouping(top: list[str]) -> Callable[[str], str]:
    """Raw category to legend row: a top directory keeps its name, other
    node types keep theirs, every other directory is ``other``."""
    keep = set(top) | {"dir", "author"}
    return lambda category: category if category in keep else "other"


def colors(top: list[str]) -> dict[str, str]:
    """Legend row to ``#rrggbb``."""
    chosen = {name: HUES[i % len(HUES)] for i, name in enumerate(top)}
    chosen.update({"other": OTHER, "dir": DIR, "author": AUTHOR})
    return {name: "#%02x%02x%02x" % rgb for name, rgb in chosen.items()}


def importers(graph: Graph) -> Callable[[Node], float]:
    """A file weighs one plus the number of files that ever imported it;
    directories and authors weigh nothing, so they draw small and unlabelled."""
    counts: Counter[str] = Counter()
    for node in graph.nodes.values():
        for edge in node.edges:
            if edge.rel == "imports":
                counts[edge.to] += 1
    return lambda node: 1.0 + counts[node.id] if node.type == "file" else 0.0


def label(node: Node) -> str:
    """A file's last two path parts (``harness/budget.py``), a directory's
    name, an author's name: short enough to sit beside a node."""
    if node.type == "file":
        return "/".join(node.name.split("/")[-2:])
    return node.name.rstrip("/").split("/")[-1] if node.type == "dir" else node.name


def theme(graph: Graph, name: str = "map", top: list[str] | None = None) -> Theme:
    top = top_dirs(graph) if top is None else top
    return theme_for(name, colors(top), labels=LABELS, halo=0.0, edge_colors=dict(EDGES))


def lenses(graph: Graph, name: str = "map", color_by: str = "dir") -> list[Lens]:
    """The ways to look at a repository, each answering a different question.
    The first is the default; the number keys switch between them."""
    top = top_dirs(graph)
    look = theme(graph, name, top)
    group = grouping(top) if color_by == "dir" else None
    common = dict(color_by=color_by, theme=look, group=group, label=label)
    by_importers = importers(graph)
    return [
        Lens(
            "Structure",
            "the directory tree, and what imports what across it",
            rels=["contains", "imports"], tree="contains", weight=by_importers, **common,
        ),
        Lens(
            "Map",
            "the same, as a force layout: clusters find their own place",
            rels=["contains", "imports"], weight=by_importers, **common,
        ),
        Lens(
            "Dependencies",
            "files alone, held together only by what imports what",
            rels=["imports"], weight=by_importers, **common,
        ),
        Lens(
            "Coupling",
            "files that keep changing in the same commits, however far apart they live",
            rels=["co_changed"], min_parallel=COUPLED, **common,
        ),
        Lens(
            "People",
            "who has touched which files",
            rels=["touched"], **common,
        ),
    ]
