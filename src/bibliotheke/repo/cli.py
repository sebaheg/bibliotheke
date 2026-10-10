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
        typer.Option("--lens", help="Start on this lens: structure (default), map, dependencies, coupling or people; j/k and tab switch."),
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
    from bibliotheke import lenses as declared
    from bibliotheke.repo import presets
    from bibliotheke.view import GraphView

    # A lens of the user's own (--rel): the given relations, as a tree when
    # `contains` is among them and asked for, else a force layout.
    tree = "contains" if rels and layout != "force" and "contains" in rels else None
    try:
        lenses = presets.lenses(graph, theme, color_by, rels=rels, tree=tree, lens=lens)
        # Without a time the repository is shown as it is now (the marker's
        # `valid_at: now`): deleted files gone, a moved file under its
        # current path only.
        at = At(valid_at=valid_at or declared.valid_at(graph) or timeutil.now())
    except ValueError as error:
        raise GraphError(str(error)) from error
    GraphView(graph, at, lenses=lenses).run()
