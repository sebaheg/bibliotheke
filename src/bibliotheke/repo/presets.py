"""What a repository's lenses need beyond what ``bibliotheke.yaml`` can say.

The lenses themselves (which relations, which layout, what sizes and labels
a node) are declared in the scanner's marker like any graph's, under
``view:``. Two things depend on the repository at hand and are computed
here: which top-level directories get a hue of their own (the largest few,
the long tail folded into one grey), and how a file's label is cut.
"""

from __future__ import annotations

from collections import Counter
from typing import Callable

from bibliotheke import lenses as declared
from bibliotheke.graph import Graph
from bibliotheke.model import Node
from bibliotheke.view import RGB, Lens

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
TOP = 6  # directories that get a colour of their own


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


def label(node: Node) -> str:
    """A file's last two path parts (``harness/budget.py``), a directory's
    name, an author's name: short enough to sit beside a node."""
    if node.type == "file":
        return "/".join(node.name.split("/")[-2:])
    return node.name.rstrip("/").split("/")[-1] if node.type == "dir" else node.name


def lenses(graph: Graph, theme: str | None = None, color_by: str | None = None, **options) -> list[Lens]:
    """The repository's declared lenses, with the directory colours and
    file labels filled in. ``options`` are the command-line overrides
    :func:`bibliotheke.lenses.lenses` takes (``rels``, ``tree``, ``lens``…)."""
    top = top_dirs(graph)
    by_dir = (color_by or declared.declared(graph)[0].color_by) == "dir"
    return declared.lenses(
        graph,
        theme=theme,
        color_by=color_by,
        group=grouping(top) if by_dir else None,
        label=label,
        colors=colors(top),
        **options,
    )
