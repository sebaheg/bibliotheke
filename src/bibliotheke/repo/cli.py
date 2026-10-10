"""``bib repo``: a repository's history as an interactive graph."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer

from bibliotheke import timeutil
from bibliotheke.graph import At, GraphError
from bibliotheke.repo.scan import scan


def repo(
    path: Annotated[Path, typer.Argument(help="Repository directory.")] = Path("."),
    rev: Annotated[str, typer.Option("--rev", help="Branch or commit to read history from.")] = "HEAD",
    since: Annotated[Optional[str], typer.Option("--since", help="Only commits after this, e.g. 2.years or 2024-01-01.")] = None,
    max_commits: Annotated[Optional[int], typer.Option("--max-commits", help="Only the newest commits.")] = None,
    imports: Annotated[
        bool, typer.Option("--imports/--no-imports", help="Read file contents for import edges (default: yes).")
    ] = True,
    color_by: Annotated[
        Optional[str], typer.Option("--color-by", help="'dir' (default), 'ext', 'type', or any fact key.")
    ] = None,
    theme: Annotated[Optional[str], typer.Option("--theme", help="'map' (default) or 'classic'.")] = None,
    lens: Annotated[
        Optional[str],
        typer.Option("--lens", help="Start on this lens: structure (default), map, dependencies, coupling or people; the number keys switch."),
    ] = None,
    rels: Annotated[
        Optional[list[str]],
        typer.Option("--rel", help="A lens of your own: draw only these relations (repeatable): contains, imports, touched, co_changed."),
    ] = None,
    layout: Annotated[
        str, typer.Option("--layout", help="With --rel: 'tree' (when contains is drawn) or 'force'.")
    ] = "tree",
    valid_at: Annotated[
        Optional[str], typer.Option("--valid-at", help="The repository as it was at this time. Default: now.")
    ] = None,
    save: Annotated[
        Optional[Path], typer.Option("--save", help="Write the graph here as bibliotheke files instead of viewing it.")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print what was read, as JSON, instead of viewing.")] = False,
) -> None:
    """Show a git repository as a graph of its files, directories and authors.

    Every commit is an edge, so `--valid-at` shows the repository, its
    ownership and which files change together as they were at that time.
    Needs a Kitty-graphics terminal to view; `--save` writes the graph as
    files to explore with the other commands.
    """
    found = scan(path, rev=rev, since=since, max_commits=max_commits, imports=imports)
    graph = found.graph
    edges = [e for n in graph.nodes.values() for e in n.edges]
    summary = {
        "commits": found.commits,
        "first": found.first,
        "last": found.last,
        "nodes": {t: sum(1 for n in graph.nodes.values() if n.type == t) for t in ("dir", "file", "author")},
        "edges": {rel: sum(1 for e in edges if e.rel == rel) for rel in ("contains", "touched", "co_changed", "imports")},
        "files_parsed": found.parsed,
        "wide_commits": found.wide_commits,
    }
    if save is not None:
        graph.save_as(save)
        typer.echo(f"Wrote {sum(summary['nodes'].values())} nodes to {save}")
        return
    if as_json:
        import json

        typer.echo(json.dumps(summary, indent=2))
        return
    from bibliotheke.repo import presets
    from bibliotheke.view import GraphView, Lens

    settings = graph.marker.get("view") or {}
    color_by = color_by or settings.get("color_by") or "dir"
    try:
        lenses = presets.lenses(graph, theme or settings.get("theme") or "map", color_by)
    except ValueError as error:
        raise GraphError(str(error)) from error
    if rels:
        # A lens of the user's own: the given relations, as a tree when the
        # tree relation is among them and asked for, else a force layout.
        own = lenses[0]
        tree = "contains" if layout != "force" and "contains" in rels else None
        lenses = [Lens("Custom", ", ".join(rels), list(rels), tree, own.color_by, own.theme, own.group, own.weight, own.label)]
    elif lens:
        names = [each.name.lower() for each in lenses]
        matches = [i for i, name in enumerate(names) if name.startswith(lens.lower())]
        if len(matches) != 1:
            raise GraphError(f"--lens must be one of {', '.join(names)}")
        lenses = lenses[matches[0] :] + lenses[: matches[0]]
    # Without a time the repository is shown as it is now: deleted files gone,
    # a moved file under its current path only. (A graph read with no filter
    # shows every version that ever was valid, which suits history, not this.)
    try:
        at = At(valid_at=valid_at or timeutil.now())
    except ValueError as error:
        raise GraphError(str(error)) from error
    GraphView(graph, at, lenses=lenses).run()
