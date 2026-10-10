"""The `bib` command line: the one interface for people and agents."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
import webbrowser
from pathlib import Path
from typing import Annotated, Any, Callable, Optional

import typer
import typer.rich_utils
import yaml
from rich.console import Console
from rich.markup import escape
from rich.text import Text

from bibliotheke.terminal import Table  # one row per line, long cells end in an ellipsis

from bibliotheke import analysis
from bibliotheke import palette
from bibliotheke import gitops
from bibliotheke import modeledit
from bibliotheke import diff as differ
from bibliotheke import export as exporter
from bibliotheke import store
from bibliotheke import timeutil
from bibliotheke.datamodel import DataModel, ModelError
from bibliotheke.graph import At, Graph, GraphError
from bibliotheke.model import Node, Record

def _one_line_help(help_text: str, markup_mode: Any = None) -> Text:
    """A command's row in `bib --help`: its first sentence on one line, cut
    with … when the terminal is narrower. `bib <command> --help` still
    shows the whole docstring."""
    paragraph = " ".join(help_text.strip().split("\n\n")[0].split())
    sentence = re.split(r"\.\s+(?=[A-Z`])", paragraph)[0].rstrip(".")  # not at "e.g. a"
    return Text(sentence, no_wrap=True, overflow="ellipsis")


typer.rich_utils._make_command_help = _one_line_help  # the command list only; per-command help is untouched

app = typer.Typer(
    help="A git-versioned, files-only knowledge graph with three time axes: valid, knowledge and change time.",
    no_args_is_help=True,
    add_completion=False,
)


def _print_version(value: bool) -> None:
    if value:
        from importlib.metadata import version

        typer.echo(f"bib {version('bibliotheke')}")
        raise typer.Exit()


@app.callback()
def _main(
    version: Annotated[
        bool,
        typer.Option("--version", "-V", callback=_print_version, is_eager=True, help="Print the version and exit."),
    ] = False,
) -> None:
    """A git-versioned, files-only knowledge graph with three time axes: valid, knowledge and change time."""
add_app = typer.Typer(help="Add a node, a fact or an edge.", no_args_is_help=True)
app.add_typer(add_app, name="add")
model_app = typer.Typer(
    help="Show the data model, or change it: add, rename and drop types, facts and edges; diff it between commits; edit it.",
    invoke_without_command=True,
)
app.add_typer(model_app, name="model")
model_add = typer.Typer(help="Add a node type, a fact, an edge or a vocabulary to the model.", no_args_is_help=True)
model_rename = typer.Typer(help="Rename a node type, a fact or an edge, and rewrite the records that use it.", no_args_is_help=True)
model_drop = typer.Typer(help="Drop a node type, a fact or an edge from the model.", no_args_is_help=True)
model_app.add_typer(model_add, name="add")
model_app.add_typer(model_rename, name="rename")
model_app.add_typer(model_drop, name="drop")
hook_app = typer.Typer(help="The pre-commit hook that runs `bib check` before every commit.", no_args_is_help=True)
app.add_typer(hook_app, name="hook")

# The repository scanner lives in its own package and only uses the public API;
# this is the one place the rest of bibliotheke refers to it.
from bibliotheke.repo.cli import repo  # noqa: E402

app.command("repo")(repo)

# So does the fetcher: `bib fetch <url>` and the model a source kind needs.
from bibliotheke import fetch as fetcher  # noqa: E402
from bibliotheke.fetch.cli import fetch  # noqa: E402

app.command("fetch")(fetch)

# Colours are hex from one palette, picked for the terminal's background, so
# "Data OK" is the same green in every terminal (a named "green" is whatever
# the terminal's own palette says, olive in a stock Ghostty).
_theme = palette.theme()
out = Console(theme=_theme)
err = Console(stderr=True, theme=_theme)

Root = Annotated[
    Optional[Path],
    typer.Option("--root", help="Graph directory. Default: the nearest parent with bibliotheke.yaml."),
]
ValidAt = Annotated[Optional[str], typer.Option("--valid-at", help="Only what was true at this time.")]
KnownAt = Annotated[Optional[str], typer.Option("--known-at", help="Only what had been learned by this time.")]
ChangedAt = Annotated[
    Optional[str], typer.Option("--changed-at", help="The graph as it was recorded at this time.")
]
AsJson = Annotated[bool, typer.Option("--json", help="Print JSON instead of tables.")]
ValidFrom = Annotated[Optional[str], typer.Option("--valid-from", help="True in the world from (YYYY, YYYY-MM, ...).")]
ValidTo = Annotated[Optional[str], typer.Option("--valid-to", help="True in the world through (inclusive).")]
Known = Annotated[Optional[str], typer.Option("--known-at", help="When this was learned. Default: now.")]
Rels = Annotated[Optional[list[str]], typer.Option("--rel", help="Only edges of this relation (repeatable).")]
Types = Annotated[Optional[list[str]], typer.Option("--type", help="Only nodes of this type (repeatable).")]
Directed = Annotated[bool, typer.Option("--directed", help="Follow edges in their direction only. Default: either way.")]
AtCommit = Annotated[
    Optional[str],
    typer.Option("--at-commit", help="The graph as committed at this git revision, read without checking it out."),
]


def _open(root: Path | None, at_commit: str | None = None) -> Graph:
    return Graph.open(root, at_commit=at_commit)


def _settings(graph: Graph, section: str) -> dict[str, Any]:
    """An optional display section of bibliotheke.yaml (``view:``, ``globe:``)."""
    settings = graph.marker.get(section) or {}
    return settings if isinstance(settings, dict) else {}


def _at(valid_at: str | None, known_at: str | None, changed_at: str | None) -> At:
    try:
        return At(valid_at=valid_at, known_at=known_at, changed_at=changed_at)
    except ValueError as error:
        raise GraphError(str(error)) from error


def _echo_json(data: Any) -> None:
    typer.echo(json.dumps(data, indent=2, ensure_ascii=False))


def _span(record: Record, event: bool = False) -> str:
    """A record's valid time for a table: a range, or for a record of an
    event type just when (nothing after it)."""
    if not record.valid_from and not record.valid_to:
        return "always"
    if event and not record.valid_to:
        return record.valid_from or "always"
    return f"{record.valid_from or '…'} → {record.valid_to or '…'}"


def _short(stamp: str) -> str:
    """A timestamp to the minute, for tables."""
    return stamp[:16].replace("T", " ")


def _record_table(title: str, columns: list[str], history: bool) -> Table:
    table = Table(title=title, title_justify="left", title_style="bold", box=None, pad_edge=False)
    for column in ("id", *columns, "valid", "known", "changed"):
        table.add_column(column)
    if history:
        table.add_column("superseded")
    return table


def _row(record: Record, cells: list[str], history: bool, event: bool = False) -> list[str]:
    row = [record.id, *cells, _span(record, event), _short(record.known_at), _short(record.changed_at)]
    if history:
        row.append(_short(record.superseded_at or ""))
    return row


def _node_json(graph: Graph, node: Node, at: At | None) -> dict[str, Any]:
    """A node's records in view; ``at=None`` means every version."""
    sees = (lambda r: True) if at is None else at.sees
    return {
        "id": node.id,
        "type": node.type,
        "name": node.name,
        "notes": node.notes,
        "body": store.body_rel(node) if graph.has_body(node) else None,
        "facts": [f.to_dict() for f in node.facts if sees(f)],
        "out_edges": [e.to_dict() for e in node.edges if sees(e)],
        "in_edges": [{"from": other.id, **e.to_dict()} for other, e in graph.in_edges(node.id, at)],
    }


def _print_node(graph: Graph, node: Node, at: At | None) -> None:
    history = at is None
    data = _node_json(graph, node, at)
    out.print(f"[bold]{node.name}[/bold]  [dim]{node.type} · {node.id}[/dim]")
    if node.notes:
        out.print(node.notes.rstrip())
    if data["body"]:
        out.print(f"[dim]Body: {data['body']}[/dim]")
    # The other end's name, with its id so the reader can `bib show` it next.
    name = lambda node_id: f"{graph.nodes[node_id].name} [dim]{node_id}[/dim]" if node_id in graph.nodes else node_id  # noqa: E731
    sections = (
        ("Facts", ["key", "value"], node.facts, lambda f: [f.key, str(f.value)]),
        ("Edges out", ["rel", "to"], node.edges, lambda e: [e.rel, name(e.to)]),
    )
    for title, columns, records, cells in sections:
        visible = [r for r in records if history or at.sees(r)]
        if visible:
            table = _record_table(title, columns, history)
            for record in visible:
                table.add_row(*_row(record, cells(record), history, _is_event(graph, node.type)), style="dim" if record.superseded_at else None)
            out.print()
            out.print(table)
    if data["in_edges"]:
        table = _record_table("Edges in", ["from", "rel"], history)
        for other, edge in graph.in_edges(node.id, at):
            # An edge in belongs to the other node, so its kind decides.
            table.add_row(*_row(edge, [name(other.id), edge.rel], history, _is_event(graph, other.type)), style="dim" if edge.superseded_at else None)
        out.print()
        out.print(table)


def _print_nodes(graph: Graph, nodes: list[Node], at: At, as_json: bool, sort: str | None = None) -> None:
    """Nodes with when they are in the world (see ``Graph.span``), sorted by
    ``type`` (then id), ``name``, ``id`` or ``valid`` (earliest first,
    undated last). Without ``--sort``, the order is bibliotheke.yaml's
    ``list: sort:``, else ``type``."""
    configured = _settings(graph, "list").get("sort")
    sort = sort or configured or "type"
    spans = {node.id: graph.span(node.id, at) for node in nodes}
    if as_json:
        _echo_json(
            [{"id": n.id, "type": n.type, "name": n.name, "valid_from": spans[n.id][0], "valid_to": spans[n.id][1]} for n in nodes]
        )
        return
    keys = {
        "type": lambda n: (n.type, n.id),
        "name": lambda n: n.name.casefold(),
        "id": lambda n: n.id,
        "valid": lambda n: (spans[n.id][0] is None, timeutil.start(spans[n.id][0]) if spans[n.id][0] else None, n.id),
    }
    if sort not in keys:
        where = "--sort" if sort != configured else "list: sort: in bibliotheke.yaml"
        raise GraphError(f"{where} must be one of {', '.join(keys)}, not {sort!r}")
    table = Table(box=None, pad_edge=False)
    for column in ("id", "type", "name", "valid"):
        table.add_column(column)
    for node in sorted(nodes, key=keys[sort]):
        table.add_row(node.id, node.type, node.name, f"[dim]{_when(graph, node.type, *spans[node.id])}[/dim]")
    out.print(table)


def _is_event(graph: Graph, type: str) -> bool:
    """Whether the model says nodes of this type happen at an instant."""
    spec = graph.model.nodes.get(type) if graph.model else None
    return bool(spec and spec.event)


def _when(graph: Graph, type: str, start: str | None, end: str | None) -> str:
    """A valid span for display: a date for an event type, else a range."""
    if start is None and end is None:
        return ""
    if _is_event(graph, type):
        return start or ""
    return f"{start or '…'} → {end or '…'}"


Sort = Annotated[
    Optional[str],
    typer.Option("--sort", help="Order: type, name, id or valid (earliest first). Default: list: sort: in bibliotheke.yaml, else type."),
]


@app.command()
def init(path: Annotated[Path, typer.Argument(help="Directory to hold the graph.")] = Path(".")) -> None:
    """Create a graph: a bibliotheke.yaml marker and a nodes/ directory."""
    graph = Graph.init(path)
    out.print(f"Initialised graph at {graph.root}")


@add_app.command("node")
def add_node(
    type: Annotated[str, typer.Argument(help="Any type you like, e.g. person.")],
    name: Annotated[str, typer.Argument(help="Display name.")],
    id: Annotated[Optional[str], typer.Option("--id", help="Node id. Default: a slug of the name.")] = None,
    notes: Annotated[str, typer.Option("--notes", help="Free-form notes.")] = "",
    root: Root = None,
) -> None:
    """Add a node."""
    node = _open(root).add_node(type, name, id=id, notes=notes)
    typer.echo(node.id)


@add_app.command("fact")
def add_fact(
    node: Annotated[str, typer.Argument(help="Node id.")],
    key: str,
    value: str,
    valid_from: ValidFrom = None,
    valid_to: ValidTo = None,
    known_at: Known = None,
    root: Root = None,
) -> None:
    """Add a fact to a node. Prints the record id."""
    fact = _open(root).add_fact(node, key, value, valid_from=valid_from, valid_to=valid_to, known_at=known_at)
    typer.echo(fact.id)


@add_app.command("edge")
def add_edge(
    src: Annotated[str, typer.Argument(metavar="FROM", help="Source node id.")],
    rel: Annotated[str, typer.Argument(help="Relation, e.g. works_at.")],
    dst: Annotated[str, typer.Argument(metavar="TO", help="Target node id.")],
    valid_from: ValidFrom = None,
    valid_to: ValidTo = None,
    known_at: Known = None,
    root: Root = None,
) -> None:
    """Add an edge between two nodes. Prints the record id."""
    edge = _open(root).add_edge(src, rel, dst, valid_from=valid_from, valid_to=valid_to, known_at=known_at)
    typer.echo(edge.id)


@app.command()
def update(
    record: Annotated[str, typer.Argument(help="Record id of a fact or edge.")],
    value: Annotated[Optional[str], typer.Option("--value", help="New value (facts only).")] = None,
    valid_from: ValidFrom = None,
    valid_to: ValidTo = None,
    known_at: Known = None,
    root: Root = None,
) -> None:
    """Change a record by appending a new version; the old one stays in history."""
    if value is None and valid_from is None and valid_to is None and known_at is None:
        raise GraphError("nothing to update: pass --value, --valid-from, --valid-to or --known-at")
    new = _open(root).update(record, value=value, valid_from=valid_from, valid_to=valid_to, known_at=known_at)
    out.print(f"{new.id} updated at {new.changed_at}")


@app.command()
def retract(
    record: Annotated[str, typer.Argument(help="Record id of a fact or edge.")],
    root: Root = None,
) -> None:
    """Remove a record from the current graph; it stays in history."""
    old = _open(root).retract(record)
    out.print(f"{old.id} retracted at {old.superseded_at}")


@app.command()
def show(
    node: Annotated[str, typer.Argument(help="Node id.")],
    valid_at: ValidAt = None,
    known_at: KnownAt = None,
    changed_at: ChangedAt = None,
    as_json: AsJson = False,
    at_commit: AtCommit = None,
    root: Root = None,
) -> None:
    """Show a node with its facts and edges."""
    graph = _open(root, at_commit)
    at = _at(valid_at, known_at, changed_at)
    found = graph.node(node)
    if as_json:
        _echo_json(_node_json(graph, found, at))
    else:
        _print_node(graph, found, at)


@app.command()
def history(
    node: Annotated[str, typer.Argument(help="Node id.")],
    as_json: AsJson = False,
    at_commit: AtCommit = None,
    root: Root = None,
) -> None:
    """Show every version of a node's facts and edges, including superseded ones."""
    graph = _open(root, at_commit)
    found = graph.node(node)
    if as_json:
        _echo_json(_node_json(graph, found, None))
    else:
        _print_node(graph, found, None)


@app.command("list")
def list_nodes(
    type: Annotated[Optional[str], typer.Option("--type", help="Only nodes of this type.")] = None,
    sort: Sort = None,
    valid_at: ValidAt = None,
    known_at: KnownAt = None,
    changed_at: ChangedAt = None,
    as_json: AsJson = False,
    at_commit: AtCommit = None,
    root: Root = None,
) -> None:
    """List nodes, with when each is in the world (its earliest valid_from
    and latest valid_to: a paper from its publication date on)."""
    graph, at = _open(root, at_commit), _at(valid_at, known_at, changed_at)
    _print_nodes(graph, graph.list_nodes(at, type=type), at, as_json, sort)


@app.command()
def search(
    text: str,
    type: Annotated[Optional[str], typer.Option("--type", help="Only nodes of this type.")] = None,
    sort: Sort = None,
    valid_at: ValidAt = None,
    known_at: KnownAt = None,
    changed_at: ChangedAt = None,
    as_json: AsJson = False,
    at_commit: AtCommit = None,
    root: Root = None,
) -> None:
    """Find nodes whose name, notes, facts, edges or body contain the text,
    ignoring case and accents."""
    graph, at = _open(root, at_commit), _at(valid_at, known_at, changed_at)
    _print_nodes(graph, graph.search(text, at, type=type), at, as_json, sort)


@app.command()
def types(
    valid_at: ValidAt = None,
    known_at: KnownAt = None,
    changed_at: ChangedAt = None,
    as_json: AsJson = False,
    at_commit: AtCommit = None,
    root: Root = None,
) -> None:
    """What the graph holds: each node type with how many nodes it has and
    what the model says it is. The place to start on an unfamiliar graph;
    `bib list --type X` then lists one type, `bib browse` walks them all."""
    from bibliotheke import browse

    graph = _open(root, at_commit)
    counts = browse.type_counts(graph, _at(valid_at, known_at, changed_at))
    if as_json:
        _echo_json([{"type": t, "nodes": n, "description": browse.type_description(graph, t)} for t, n in counts])
        return
    if graph.model and graph.model.description:
        out.print(graph.model.description.strip())
        out.print()
    table = Table(box=None, pad_edge=False)
    for column in ("type", "nodes", "description"):
        table.add_column(column, justify="right" if column == "nodes" else "left")
    for t, n in counts:
        table.add_row(t, str(n), browse.type_description(graph, t))
    out.print(table)
    out.print(f"\n[dim]{sum(n for _, n in counts)} nodes in {len(counts)} types[/dim]")


@app.command()
def browse(
    tui: Annotated[
        bool, typer.Option("--tui", help="A full-screen two-pane view (tree of types and nodes, details beside it) instead of the prompt.")
    ] = False,
    valid_at: ValidAt = None,
    known_at: KnownAt = None,
    changed_at: ChangedAt = None,
    at_commit: AtCommit = None,
    root: Root = None,
) -> None:
    """Walk the graph at a prompt: the types, then a type's nodes, then a
    node's facts, edges and body, with every step left in the terminal.
    A number picks from the last list or follows an edge, /text filters,
    .. goes back, q quits. `--tui` opens a full-screen view instead."""
    from bibliotheke.browse import Browser, Walk

    graph, at = _open(root, at_commit), _at(valid_at, known_at, changed_at)
    if tui:
        Browser(graph, at).run()
    else:
        Walk(graph, at, out).run()


@app.command("import")
def import_batch(
    files: Annotated[list[Path], typer.Argument(help="Batch files (YAML or JSON).", exists=True, dir_okay=False)],
    as_json: AsJson = False,
    root: Root = None,
) -> None:
    """Merge batch files of nodes, facts, edges and bodies; safe to re-run.

    A batch is a mapping with optional `known_at` and lists `nodes` and `edges`.
    Each node has `type`, `name` and optionally `id`, `notes`, `body` (Markdown),
    `valid_from`/`valid_to`/`known_at` (defaults for its facts and edges),
    `facts` ({key: value, key: [values], key: {value, valid_from, ...}}) and
    `edges` ([{rel, to, valid_from, ...}]). Top-level edges also take `from`.
    """
    graph = _open(root)
    totals: dict[str, int] = {}
    for file in files:
        try:
            batch = yaml.safe_load(file.read_text())
        except yaml.YAMLError as error:
            raise GraphError(f"{file}: {error}") from error
        if not isinstance(batch, dict):
            raise GraphError(f"{file}: a batch is a mapping with `nodes` and `edges`")
        try:
            counts = graph.apply(batch)
        except GraphError as error:
            raise GraphError(f"{file}: {error}") from error
        for key, n in counts.items():
            totals[key] = totals.get(key, 0) + n
    if as_json:
        _echo_json(totals)
    else:
        typer.echo(", ".join(f"{n} {key}" for key, n in sorted(totals.items())) or "nothing to import")


@app.command("export")
def export_table(
    columns: Annotated[
        str,
        typer.Option(
            "--columns",
            help="Comma-separated: id, name, type, a fact key, >rel (out-edge targets) or <rel (in-edge sources).",
        ),
    ] = "name,type",
    type: Annotated[Optional[str], typer.Option("--type", help="Only nodes of this type.")] = None,
    sort: Annotated[Optional[str], typer.Option("--sort", help="Column to sort by; prefix '-' for descending.")] = None,
    valid_at: ValidAt = None,
    known_at: KnownAt = None,
    changed_at: ChangedAt = None,
    at_commit: AtCommit = None,
    root: Root = None,
) -> None:
    """Print nodes as a Markdown table, e.g. a catalogue."""
    graph = _open(root, at_commit)
    names = [c.strip() for c in columns.split(",") if c.strip()]
    typer.echo(exporter.markdown_table(graph, names, at=_at(valid_at, known_at, changed_at), type=type, sort=sort), nl=False)


@app.command()
def path(
    src: Annotated[str, typer.Argument(help="Node id to start from.")],
    dst: Annotated[str, typer.Argument(help="Node id to reach.")],
    rels: Rels = None,
    directed: Directed = False,
    valid_at: ValidAt = None,
    known_at: KnownAt = None,
    changed_at: ChangedAt = None,
    as_json: AsJson = False,
    at_commit: AtCommit = None,
    root: Root = None,
) -> None:
    """The shortest chain of edges between two nodes."""
    graph = _open(root, at_commit)
    steps = analysis.shortest_path(graph, src, dst, at=_at(valid_at, known_at, changed_at), rels=rels, directed=directed)
    if as_json:
        _echo_json(
            {
                "from": src,
                "to": dst,
                "length": len(steps),
                "nodes": [src, *(s.dst for s in steps)],
                "edges": [{"id": s.id, "from": s.src if s.forward else s.dst, "rel": s.rel, "to": s.dst if s.forward else s.src} for s in steps],
            }
        )
        return
    name = lambda node_id: graph.nodes[node_id].name  # noqa: E731
    table = Table(title=f"{len(steps)} hop{'s' if len(steps) != 1 else ''}", title_justify="left", title_style="bold", box=None, pad_edge=False)
    for column in ("from", "rel", "to"):
        table.add_column(column)
    for step in steps:
        table.add_row(name(step.src), f"{'→' if step.forward else '←'} {step.rel}", name(step.dst))
    out.print(table)


@app.command()
def rank(
    by: Annotated[str, typer.Option("--by", help="degree (edges either way), pagerank or betweenness.")] = "degree",
    top: Annotated[int, typer.Option("--top", help="How many nodes to show; 0 for all.")] = 20,
    types: Types = None,
    rels: Rels = None,
    directed: Directed = False,
    valid_at: ValidAt = None,
    known_at: KnownAt = None,
    changed_at: ChangedAt = None,
    as_json: AsJson = False,
    at_commit: AtCommit = None,
    root: Root = None,
) -> None:
    """The best-connected nodes, by a centrality measure."""
    graph = _open(root, at_commit)
    ranked = analysis.rank(
        graph, by, at=_at(valid_at, known_at, changed_at), rels=rels, types=types, directed=directed, top=top or None
    )
    if as_json:
        _echo_json([{"id": n.id, "type": n.type, "name": n.name, "score": s} for n, s in ranked])
        return
    table = Table(box=None, pad_edge=False)
    for column in ("#", "id", "type", "name", by):
        table.add_column(column, justify="right" if column in ("#", by) else "left")
    for i, (node, score) in enumerate(ranked, 1):
        table.add_row(str(i), node.id, node.type, node.name, f"{score:.0f}" if by == "degree" else f"{score:.4f}")
    out.print(table)


@app.command()
def communities(
    seed: Annotated[int, typer.Option("--seed", help="Random seed; the same seed gives the same split.")] = 0,
    min_size: Annotated[int, typer.Option("--min-size", help="Hide communities smaller than this.")] = 1,
    types: Types = None,
    rels: Rels = None,
    valid_at: ValidAt = None,
    known_at: KnownAt = None,
    changed_at: ChangedAt = None,
    as_json: AsJson = False,
    at_commit: AtCommit = None,
    root: Root = None,
) -> None:
    """Split the graph into tightly linked groups (Louvain), largest first."""
    graph = _open(root, at_commit)
    groups = analysis.communities(
        graph, at=_at(valid_at, known_at, changed_at), rels=rels, types=types, seed=seed, min_size=min_size
    )
    if as_json:
        _echo_json([{"community": i, "size": len(g), "members": [n.id for n in g]} for i, g in enumerate(groups)])
        return
    table = Table(box=None, pad_edge=False)
    for column in ("#", "size", "members"):
        table.add_column(column, justify="right" if column != "members" else "left")
    for i, group in enumerate(groups):
        table.add_row(str(i), str(len(group)), ", ".join(n.id for n in group))
    out.print(table)


@app.command()
def diff(
    rev1: Annotated[str, typer.Argument(help="Git revision to compare from.")],
    rev2: Annotated[Optional[str], typer.Argument(help="Git revision to compare to. Default: the working tree.")] = None,
    as_json: AsJson = False,
    root: Root = None,
) -> None:
    """What changed between two git revisions of the graph.

    Nodes added, removed or renamed; facts and edges added, changed or
    retracted, compared on their current versions.
    """
    old = _open(root, rev1)
    new = _open(root, rev2)
    found = differ.diff_graphs(old, new)
    if as_json:
        _echo_json({"from": old.source.commit, "to": new.source.commit if rev2 else "working tree", **found})
        return
    typer.echo(differ.summary(found))
    if not any(entries for changes in found.values() for entries in changes.values()):
        return
    table = Table(box=None, pad_edge=False)
    for column in ("kind", "change", "node", "record", "detail"):
        table.add_column(column)
    for kind in differ.KINDS:
        for change, entries in found[kind].items():
            for entry in entries:
                if kind == "nodes":
                    table.add_row("node", change, entry["id"], "", f"{entry['type']}: {entry['name']}")
                elif kind == "facts":
                    table.add_row("fact", change, entry["node"], entry["id"], f"{entry['key']} = {entry['value']!r}")
                else:
                    table.add_row("edge", change, entry["node"], entry["id"], f"{entry['rel']} {entry['to']}")
    out.print(table)


@model_app.callback()
def model(ctx: typer.Context, as_json: AsJson = False, at_commit: AtCommit = None, root: Root = None) -> None:
    """Show the graph's data model: its node types, their facts, and the edges between them.

    The model lives in bibliotheke.yaml as `description`, `time`,
    `vocabularies`, `nodes` and `edges`. Writes that break it are refused.
    The subcommands change it; each one rewrites bibliotheke.yaml (comments
    are not kept) and then checks the data against the new model.
    """
    if ctx.invoked_subcommand is not None:
        return
    graph = _open(root, at_commit)
    if graph.model is None:
        if as_json:
            _echo_json(None)
        else:
            out.print(f"No data model: add a `nodes:` section to {store.ROOT_MARKER} to describe this graph.")
        return
    data = graph.model.to_dict()
    if as_json:
        _echo_json(data)
        return
    if data["description"]:
        out.print(data["description"])
    if graph.marker.get(gitops.VERSION_KEY):
        out.print(f"[dim]Version {graph.marker[gitops.VERSION_KEY]}[/dim]")
    if data["time"] == "ignored":
        out.print("[dim]Valid time is not used in this graph.[/dim]")
    for type, spec in data["nodes"].items():
        out.print()
        out.print(f"[bold]{type}[/bold]  [dim]{spec['description']}[/dim]")
        out.print(f"  name: {spec['name']}")
        if spec.get("time") == "event":
            out.print("  time: event  [dim](happens at an instant; valid_from is when)[/dim]")
        if spec["id"]:
            out.print(f"  id: {spec['id']}")
        if spec["body"]:
            out.print(f"  body: {spec['body']}")
        if spec["facts"]:
            table = Table(box=None, pad_edge=False, show_header=False)
            for column in ("key", "type", "flags", "description"):
                table.add_column(column, style="dim" if column == "description" else None)
            for key, fact in spec["facts"].items():
                kind = fact["type"]
                if "values" in fact:
                    values = fact["values"]
                    kind += f" ({values if isinstance(values, str) else ', '.join(values)})"
                flags = " ".join(flag for flag in ("required", "many") if fact.get(flag))
                table.add_row(f"  {key}", kind, flags, fact.get("description", ""))
            out.print(table)
    if data["edges"]:
        out.print()
        out.print("[bold]Edges[/bold]")
        table = Table(box=None, pad_edge=False, show_header=False)
        for column in ("rel", "ends", "flags", "description"):
            table.add_column(column, style="dim" if column == "description" else None)
        for rel, spec in data["edges"].items():
            arrow = "↔" if spec["symmetric"] else "→"
            ends = f"{'/'.join(spec['from'])} {arrow} {'/'.join(spec['to'])}"
            flags = [name for name in ("required", "symmetric") if spec[name]]
            if not spec["many"]:
                flags.append("one target")
            table.add_row(f"  {rel}", ends, " ".join(flags), spec["description"])
        out.print(table)
    if data["vocabularies"]:
        out.print()
        out.print("[bold]Vocabularies[/bold]")
        for name, values in data["vocabularies"].items():
            out.print(f"  {name}: {', '.join(values)}")


Force = Annotated[bool, typer.Option("--force", help="Go ahead even though current records break.")]
Description = Annotated[str, typer.Option("--description", help="What this is, for readers of the model.")]


def _model_write(root: Path | None, change: Callable[[dict[str, Any]], dict[str, Any]], done: str) -> Graph:
    """Apply a change to the model, write it in the graph's layout (a new
    model starts the model/ folder), say what was done, and return the
    reopened graph."""
    graph = _open(root)
    graph._mutable()
    try:
        marker = change(graph.marker)
    except ModelError as error:
        raise GraphError(str(error)) from None
    store.save_model(graph.root, marker, graph.layout or store.FOLDER)
    if done:
        out.print(done)
    return _open(graph.root)


def _model_report(graph: Graph) -> None:
    """The data against the (new) model; exit 1 when something breaks."""
    problems = graph.model.problems(graph.nodes, graph.root) if graph.model else []
    _print_problems(problems)
    if problems:
        raise typer.Exit(1)


def _print_problems(problems: list[str]) -> None:
    if not problems:
        out.print("[ok]Data OK[/ok]")
        return
    out.print(f"[bad]{len(problems)} problem{'s' if len(problems) != 1 else ''} in the data:[/bad]")
    for problem in problems:
        err.print(f"  {problem}")


def _print_changes(title: str, changes: list[modeledit.Change]) -> None:
    out.print(f"[bold]{title}[/bold]")
    if not changes:
        out.print("  no change")
        return
    # The mark and the kind share a colour: green for what the model gains,
    # red for what it loses, dim for a change of wording.
    styles = {modeledit.ADDITIVE: "ok", modeledit.DESTRUCTIVE: "bad", modeledit.NEUTRAL: "dim"}
    marks = {modeledit.ADDITIVE: "+", modeledit.DESTRUCTIVE: "-", modeledit.NEUTRAL: "~"}
    table = Table(box=None, pad_edge=False, show_header=False)
    for column in ("mark", "where", "what", "kind"):
        table.add_column(column)
    for kind in modeledit.KINDS:
        for change in changes:
            if change.kind == kind:
                style = styles[kind]
                table.add_row(f"  [{style}]{marks[kind]}[/{style}]", change.where, change.what, f"[{style}]{kind}[/{style}]")
    out.print(table)


def _csv(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


@model_add.command("node")
def model_add_node(
    type: Annotated[str, typer.Argument(help="Node type, lowercase with hyphens, e.g. part-of-speech.")],
    description: Description = "",
    name: Annotated[str, typer.Option("--name", help="What a node's name is for this type, e.g. 'Full name as printed'.")] = "",
    id: Annotated[Optional[str], typer.Option("--id", help="Id template, e.g. '<name>-<lang>'.")] = None,
    body: Annotated[str, typer.Option("--body", help="What the Markdown body holds.")] = "",
    event: Annotated[
        bool,
        typer.Option("--event", help="Nodes of this type happen at an instant (a paper, a claim): valid_from is when, there is no valid_to."),
    ] = False,
    root: Root = None,
) -> None:
    """Add a node type. On a graph without a model, this starts one."""
    graph = _model_write(
        root,
        lambda m: modeledit.add_node_type(m, type, description=description, name=name, id=id, body=body, event=event),
        f"Added node type {type}",
    )
    _model_report(graph)


@model_add.command("fact")
def model_add_fact(
    type: Annotated[str, typer.Argument(help="Node type the fact belongs to.")],
    key: Annotated[str, typer.Argument(help="Fact key, e.g. lang.")],
    kind: Annotated[str, typer.Argument(metavar="FACT_TYPE", help="text, int, number, bool, time, url, file or enum.")] = "text",
    values: Annotated[
        Optional[str], typer.Option("--values", help="For an enum: comma-separated values, or the name of a vocabulary.")
    ] = None,
    required: Annotated[bool, typer.Option("--required", help="Every node of the type must have it.")] = False,
    many: Annotated[bool, typer.Option("--many", help="Several values at once, not one.")] = False,
    description: Description = "",
    root: Root = None,
) -> None:
    """Add a fact to a node type, e.g. `bib model add fact word lang enum --values en,sv --required`."""

    def change(marker: dict[str, Any]) -> dict[str, Any]:
        given: list[str] | str | None = None
        if values is not None:
            given = values if values in (marker.get("vocabularies") or {}) else _csv(values)
        return modeledit.add_fact(
            marker, type, key, kind=kind, required=required, many=many, values=given, description=description
        )

    _model_report(_model_write(root, change, f"Added fact {type}.{key} ({kind})"))


@model_add.command("edge")
def model_add_edge(
    rel: Annotated[str, typer.Argument(help="Relation, e.g. works_at.")],
    src: Annotated[str, typer.Argument(metavar="FROM", help="Source node type(s), comma-separated.")],
    dst: Annotated[str, typer.Argument(metavar="TO", help="Target node type(s), comma-separated.")],
    symmetric: Annotated[bool, typer.Option("--symmetric", help="Reads the same in either direction; stored once.")] = False,
    required: Annotated[bool, typer.Option("--required", help="Every source node must have one.")] = False,
    many: Annotated[bool, typer.Option("--many/--one", help="Any number of targets at once (default), or one.")] = True,
    description: Description = "",
    root: Root = None,
) -> None:
    """Add an edge between node types, e.g. `bib model add edge translates_to word word --symmetric`."""
    graph = _model_write(
        root,
        lambda m: modeledit.add_edge(
            m, rel, _csv(src), _csv(dst), symmetric=symmetric, required=required, many=many, description=description
        ),
        f"Added edge {rel}: {src} {'↔' if symmetric else '→'} {dst}",
    )
    _model_report(graph)


@model_add.command("source")
def model_add_source(
    kind: Annotated[str, typer.Argument(help="A source kind `bib fetch` knows: youtube, arxiv, github or web.")],
    root: Root = None,
) -> None:
    """Add the node types, facts and edges that fetching from a source kind uses, e.g. `bib model add source youtube`."""
    added: list[str] = []

    def change(marker: dict[str, Any]) -> dict[str, Any]:
        marker, found = fetcher.install_model(marker, kind)
        added.extend(found)
        return marker

    graph = _model_write(root, change, "")
    out.print(f"Added {', '.join(added)}" if added else f"The model already has what a {kind} fetch uses")
    _model_report(graph)


@model_add.command("vocabulary")
def model_add_vocabulary(
    name: Annotated[str, typer.Argument(help="Vocabulary name, e.g. lang.")],
    values: Annotated[list[str], typer.Argument(help="Its values.")],
    root: Root = None,
) -> None:
    """Add a named list of values that enum facts can share."""
    _model_report(_model_write(root, lambda m: modeledit.add_vocabulary(m, name, values), f"Added vocabulary {name}"))


@model_rename.command("node")
def model_rename_node(
    old: Annotated[str, typer.Argument(help="Current node type.")],
    new: Annotated[str, typer.Argument(help="New node type.")],
    root: Root = None,
) -> None:
    """Rename a node type and move its nodes to nodes/<new>/."""
    graph = _open(root)
    graph._mutable()
    try:
        marker = modeledit.rename_node_type(graph.marker, old, new)
    except ModelError as error:
        raise GraphError(str(error)) from None
    moved = modeledit.rewrite_node_type(graph.nodes, graph.root, old, new)
    store.save_model(graph.root, marker, graph.layout or store.FOLDER)
    out.print(f"Renamed node type {old} to {new}; moved {moved} node{'s' if moved != 1 else ''}")
    _model_report(_open(graph.root))


@model_rename.command("fact")
def model_rename_fact(
    type: Annotated[str, typer.Argument(help="Node type the fact belongs to.")],
    old: Annotated[str, typer.Argument(help="Current key.")],
    new: Annotated[str, typer.Argument(help="New key.")],
    root: Root = None,
) -> None:
    """Rename a fact key and rewrite every version of the facts that use it."""
    graph = _open(root)
    graph._mutable()
    try:
        marker = modeledit.rename_fact(graph.marker, type, old, new)
    except ModelError as error:
        raise GraphError(str(error)) from None
    count = modeledit.rewrite_fact_key(graph.nodes, graph.root, type, old, new)
    store.save_model(graph.root, marker, graph.layout or store.FOLDER)
    out.print(f"Renamed fact {type}.{old} to {type}.{new} in {count} record{'s' if count != 1 else ''}")
    _model_report(_open(graph.root))


@model_rename.command("edge")
def model_rename_edge(
    old: Annotated[str, typer.Argument(help="Current relation.")],
    new: Annotated[str, typer.Argument(help="New relation.")],
    root: Root = None,
) -> None:
    """Rename a relation and rewrite every version of the edges that use it."""
    graph = _open(root)
    graph._mutable()
    try:
        marker = modeledit.rename_edge(graph.marker, old, new)
    except ModelError as error:
        raise GraphError(str(error)) from None
    count = modeledit.rewrite_rel(graph.nodes, graph.root, old, new)
    store.save_model(graph.root, marker, graph.layout or store.FOLDER)
    out.print(f"Renamed edge {old} to {new} in {count} record{'s' if count != 1 else ''}")
    _model_report(_open(graph.root))


@model_drop.command("node")
def model_drop_node(
    type: Annotated[str, typer.Argument(help="Node type to drop.")],
    root: Root = None,
) -> None:
    """Drop a node type. Refused while nodes of the type exist: delete or rename them first."""
    graph = _open(root)
    if count := modeledit.uses(graph.nodes, type=type):
        raise GraphError(f"{count} node{'s are' if count != 1 else ' is'} of type {type!r}; delete or `bib model rename node` them first")
    _model_report(_model_write(root, lambda m: modeledit.drop_node_type(m, type), f"Dropped node type {type}"))


@model_drop.command("fact")
def model_drop_fact(
    type: Annotated[str, typer.Argument(help="Node type the fact belongs to.")],
    key: Annotated[str, typer.Argument(help="Fact key to drop.")],
    force: Force = False,
    root: Root = None,
) -> None:
    """Drop a fact from a node type. With --force, current facts with the key are retracted (they stay in history)."""
    graph = _open(root)
    count = modeledit.uses(graph.nodes, type=type, key=key)
    if count and not force:
        raise GraphError(f"{count} current record{'s' if count != 1 else ''} use{'' if count != 1 else 's'} {type}.{key}; --force retracts them")
    graph = _model_write(root, lambda m: modeledit.drop_fact(m, type, key), f"Dropped fact {type}.{key}")
    if count:
        retracted = modeledit.retract_all(graph.nodes, graph.root, type=type, key=key)
        out.print(f"Retracted {retracted} record{'s' if retracted != 1 else ''}")
    _model_report(_open(graph.root))


@model_drop.command("edge")
def model_drop_edge(
    rel: Annotated[str, typer.Argument(help="Relation to drop.")],
    force: Force = False,
    root: Root = None,
) -> None:
    """Drop an edge from the model. With --force, current edges of the relation are retracted (they stay in history)."""
    graph = _open(root)
    count = modeledit.uses(graph.nodes, rel=rel)
    if count and not force:
        raise GraphError(f"{count} current record{'s' if count != 1 else ''} use{'' if count != 1 else 's'} {rel}; --force retracts them")
    graph = _model_write(root, lambda m: modeledit.drop_edge(m, rel), f"Dropped edge {rel}")
    if count:
        retracted = modeledit.retract_all(graph.nodes, graph.root, rel=rel)
        out.print(f"Retracted {retracted} record{'s' if retracted != 1 else ''}")
    _model_report(_open(graph.root))


@model_app.command("diff")
def model_diff(
    rev1: Annotated[str, typer.Argument(help="Git revision to compare from.")] = "HEAD",
    rev2: Annotated[Optional[str], typer.Argument(help="Git revision to compare to. Default: the working tree.")] = None,
    as_json: AsJson = False,
    markdown: Annotated[bool, typer.Option("--markdown", help="Report as Markdown, for a pull request.")] = False,
    no_destructive: Annotated[
        bool, typer.Option("--no-destructive", help="Exit 1 on a destructive change too, as the pre-commit hook does.")
    ] = False,
    root: Root = None,
) -> None:
    """How the model changed between two git revisions, and what the change breaks.

    Each change is marked additive (+), destructive (-) or neutral (~), then
    the data is checked against the newer model. Exits 1 when something breaks,
    or with --no-destructive on any destructive change.
    """
    old = _open(root, rev1)
    new = _open(root, rev2)
    changes = modeledit.diff_models(old.model, new.model)
    problems = new.model.problems(new.nodes, new.root) if new.model else []
    destructive = any(c.kind == modeledit.DESTRUCTIVE for c in changes)
    if as_json:
        _echo_json(
            {
                "from": old.source.commit,
                "to": new.source.commit if rev2 else "working tree",
                "changes": [c.to_dict() for c in changes],
                "problems": problems,
            }
        )
    elif markdown:
        typer.echo(modeledit.markdown_report(f"Model changes, {rev1} → {rev2 or 'working tree'}", changes, problems, len(new.nodes)), nl=False)
    else:
        _print_changes(f"Model changes, {rev1} → {rev2 or 'working tree'}", changes)
        _print_problems(problems)
    if no_destructive and destructive:
        err.print("[bad]destructive model change[/bad]; set BIB_ALLOW_DESTRUCTIVE=1 to commit it anyway")
        raise typer.Exit(1)
    if problems:
        raise typer.Exit(1)


@model_app.command("log")
def model_log(
    max_count: Annotated[int, typer.Option("--max", help="How many model-touching commits to look at.")] = 20,
    as_json: AsJson = False,
    root: Root = None,
) -> None:
    """The commits that changed the model, newest first, each with what the change did."""
    graph = _open(root)
    try:
        entries = gitops.model_log(graph.root, max_count)
    except ValueError as error:
        raise GraphError(str(error)) from None
    if as_json:
        _echo_json([e.to_dict() for e in entries])
        return
    if not entries:
        out.print("No commits changed the model")
        return
    table = Table(box=None, pad_edge=False)
    for column in ("date", "commit", "subject", "model change"):
        table.add_column(column)
    for entry in entries:
        table.add_row(entry.date, entry.sha[:7], entry.subject, _coloured_summary(entry.changes))
    out.print(table)
    out.print("[ok]+ additive[/ok]  [bad]- destructive[/bad]  [dim]~ neutral[/dim]")


def _coloured_summary(changes: list[modeledit.Change]) -> str:
    """One line: for each kind of change present, its count and what it
    touched, in the kind's colour: green gains, red losses, dim rewording."""
    styles = {modeledit.ADDITIVE: "ok", modeledit.DESTRUCTIVE: "bad", modeledit.NEUTRAL: "dim"}
    marks = {modeledit.ADDITIVE: "+", modeledit.DESTRUCTIVE: "-", modeledit.NEUTRAL: "~"}
    parts = []
    for kind in (modeledit.DESTRUCTIVE, modeledit.ADDITIVE, modeledit.NEUTRAL):
        where: list[str] = []
        for change in changes:
            # The model itself coming or going reads better said than as a name.
            label = {("model", modeledit.DESTRUCTIVE): "model added", ("model", modeledit.ADDITIVE): "model removed"}.get(
                (change.where, change.kind), change.where
            )
            if change.kind == kind and label not in where:
                where.append(label)
        if where:
            shown = ", ".join(where[:4]) + (f" +{len(where) - 4}" if len(where) > 4 else "")
            parts.append(f"[{styles[kind]}]{marks[kind]}{len(where)} {shown}[/{styles[kind]}]")
    return "   ".join(parts)


@model_app.command("version")
def model_version(
    bump: Annotated[bool, typer.Option("--bump", help="Set the next version from the changes since the last model tag.")] = False,
    root: Root = None,
) -> None:
    """The model's version, or with --bump the next one: destructive changes bump
    the major number, additive the minor, neutral the patch. Tag it with `bib model tag`."""
    graph = _open(root)
    current = graph.marker.get(gitops.VERSION_KEY)
    if not bump:
        typer.echo(str(current) if current else "no version; `bib model version --bump` sets one")
        return
    graph._mutable()
    try:
        version, changes, tag = gitops.next_version(graph.root, graph.marker)
    except ValueError as error:
        raise GraphError(str(error)) from None
    if changes:
        _print_changes(f"Model changes since {tag}", changes)
    graph.marker[gitops.VERSION_KEY] = version
    store.save_model(graph.root, graph.marker, graph.layout or store.FILE)
    out.print(f"Version {version}" + (f" (was {current})" if current and str(current) != version else "") + "; commit, then `bib model tag`")


@model_app.command("tag")
def model_tag(root: Root = None) -> None:
    """Tag the committed model with its version, as model-v<version>. Push tags with `git push --tags`."""
    graph = _open(root)
    try:
        name = gitops.tag_version(graph.root, graph.marker)
    except ValueError as error:
        raise GraphError(str(error)) from None
    out.print(f"Tagged HEAD as {name}; `git push --tags` publishes it")


def _edit_text(text: str) -> str:
    """The text after the user edits it in $VISUAL or $EDITOR (default: vi)."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as handle:
        handle.write(text)
        path = Path(handle.name)
    try:
        done = subprocess.run([*shlex.split(editor), str(path)], check=False)
        if done.returncode:
            raise GraphError(f"{editor} exited with status {done.returncode}; nothing saved")
        return path.read_text(encoding="utf-8")
    finally:
        path.unlink(missing_ok=True)


def _git(root: Path, *args: str) -> str | None:
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    return done.stdout.strip() if done.returncode == 0 else None


def model_url(root: Path, target: str = store.ROOT_MARKER) -> tuple[str, list[str]]:
    """The GitHub URL of a path in the graph (``bibliotheke.yaml``, the
    ``model/`` folder, or one of its files) on the current branch, and
    warnings when what GitHub shows is not what is on disk."""
    top = _git(root, "rev-parse", "--show-toplevel", "--show-prefix")
    if top is None:
        raise GraphError(f"{root} is not in a git repository")
    prefix = top.split("\n")[1] if "\n" in top else ""
    remote = _git(root, "remote", "get-url", "origin")
    if remote is None:
        raise GraphError("the repository has no remote named origin; push it to GitHub first")
    match = re.match(r"^(?:https?://|ssh://)?(?:[\w.-]+@)?(?P<host>[\w.-]+)[:/](?P<path>.+?)(?:\.git)?/?$", remote)
    if match is None:
        raise GraphError(f"cannot turn the remote {remote!r} into a web address")
    branch = _git(root, "symbolic-ref", "--short", "HEAD") or "HEAD"
    kind = "tree" if target.endswith("/") else "blob"
    url = f"https://{match['host']}/{match['path']}/{kind}/{branch}/{prefix}{target}"
    warnings = []
    if _git(root, "status", "--porcelain", "--", target.rstrip("/")):
        warnings.append(f"{target} has uncommitted changes; GitHub shows the committed version")
    ahead = _git(root, "rev-list", "--count", "@{upstream}..HEAD")
    if ahead is None:
        warnings.append(f"branch {branch} has not been pushed")
    elif ahead != "0":
        warnings.append(f"branch {branch} is {ahead} commit{'s' if ahead != '1' else ''} ahead of its remote")
    return url, warnings


ModelFile = Annotated[
    Optional[str],
    typer.Argument(help="In the model/ layout: a node type, or 'vocabularies', for that one file."),
]


def _model_target(graph: Graph, name: str | None) -> str:
    """The path that holds the model, or one of its files."""
    if graph.layout != store.FOLDER:
        if name:
            raise GraphError(f"the model is in {store.ROOT_MARKER}, not in {store.MODEL_DIR}/; `bib model split` moves it there")
        return store.ROOT_MARKER
    if name is None:
        return f"{store.MODEL_DIR}/"
    rel = f"{store.MODEL_DIR}/{name}.yaml"
    if not (graph.root / rel).is_file():
        known = sorted(p.stem for p in (graph.root / store.MODEL_DIR).glob("*.yaml"))
        raise GraphError(f"no {rel} (there: {', '.join(known)})")
    return rel


@model_app.command("open")
def model_open(
    name: ModelFile = None,
    print_only: Annotated[bool, typer.Option("--print", help="Print the address instead of opening it.")] = False,
    root: Root = None,
) -> None:
    """Open the model on GitHub, on the current branch: the model/ folder, one of its files, or bibliotheke.yaml."""
    graph = _open(root)
    url, warnings = model_url(graph.root, _model_target(graph, name))
    for warning in warnings:
        err.print(f"[warn]warning:[/warn] {warning}")
    if print_only:
        typer.echo(url)
    else:
        webbrowser.open(url)
        out.print(f"Opened {url}")


@model_app.command("edit")
def model_edit(name: ModelFile = None, force: Force = False, root: Root = None) -> None:
    """Edit the model in $EDITOR, with the result checked before it is saved.

    With the model/ layout, name the file to edit: a node type, or
    'vocabularies'. The edited file is parsed, diffed against the current
    model and checked against the data. A change that breaks current
    records is not saved unless --force.
    """
    graph = _open(root)
    graph._mutable()
    if graph.layout == store.FOLDER and name is None:
        known = sorted(p.stem for p in (graph.root / store.MODEL_DIR).glob("*.yaml"))
        raise GraphError(f"name the file to edit: one of {', '.join(known)}")
    target = _model_target(graph, name)
    path = graph.root / target
    before = path.read_text()
    after = _edit_text(before)
    if after is None or after == before:
        out.print("No change")
        return
    try:
        if graph.layout == store.FOLDER:
            texts = {p.relative_to(graph.root).as_posix(): p.read_text() for p in (graph.root / store.MODEL_DIR).glob("*.yaml")}
            texts[target] = after
            marker = store.assemble_model(store.load_marker(graph.root), texts)
        else:
            marker = yaml.safe_load(after)
            if not isinstance(marker, dict):
                raise ModelError("must be a mapping")
        model = DataModel.from_marker(marker)
    except (yaml.YAMLError, ValueError) as error:  # ModelError is a ValueError
        raise GraphError(f"not saved: {error}") from None
    changes = modeledit.diff_models(graph.model, model)
    problems = model.problems(graph.nodes, graph.root) if model else []
    _print_changes("Model changes", changes)
    _print_problems(problems)
    if problems and not force:
        raise GraphError("not saved; fix the data or the model, or pass --force to save anyway")
    path.write_text(after)
    out.print(f"Saved {target}")


@model_app.command("split")
def model_split(root: Root = None) -> None:
    """Move the model out of bibliotheke.yaml into a model/ folder: one file per
    node type, with the edges that go out from it, plus vocabularies.yaml."""
    graph = _open(root)
    graph._mutable()
    if graph.layout == store.FOLDER:
        raise GraphError(f"the model is already in {store.MODEL_DIR}/")
    if graph.layout is None:
        raise GraphError("this graph has no data model; `bib model add node <type>` starts one, in model/")
    written = store.save_model(graph.root, graph.marker, store.FOLDER)
    for rel in written:
        out.print(f"  {rel}")
    out.print(f"Model moved to {store.MODEL_DIR}/ ({len(written) - 1} files); {store.ROOT_MARKER} keeps description, time and view settings")
    _model_report(_open(graph.root))


@app.command()
def check(
    markdown: Annotated[bool, typer.Option("--markdown", help="Report as Markdown, for a pull request.")] = False,
    as_json: AsJson = False,
    root: Root = None,
) -> None:
    """Validate the graph files: dangling edges, misplaced files, duplicate records,
    orphaned bodies, `file` facts that name missing files, and anything the data
    model does not allow. Shows, per node type, how many nodes are consistent
    with the model. Exits 1 on a problem. (How the model changed is
    `bib model diff`'s question.)"""
    graph = _open(root)
    problems = graph.check()
    rows = _check_breakdown(graph, problems)
    if markdown:
        typer.echo(modeledit.markdown_report("", None, problems, len(graph.nodes)), nl=False)
    elif as_json:
        _echo_json({
            "ok": not problems,
            "nodes": len(graph.nodes),
            "types": [{"type": t, "nodes": n, "ok": n - bad, "facts": f, "edges": e, "problems": bad} for t, n, f, e, bad in rows],
            "problems": problems,
        })
    else:
        _print_check(graph, rows, problems)
    if problems:
        raise typer.Exit(1)


def _check_breakdown(graph: Graph, problems: list[str]) -> list[tuple[str, int, int, int, int]]:
    """Per node type: nodes, current facts, current edges, and nodes with a
    problem. A problem names its node as `<id>: ...`; one that names no node
    (an orphaned body, a duplicate record id) counts under the type ``""``.
    Types the model declares are listed even with no nodes."""
    types = sorted({n.type for n in graph.nodes.values()} | set(graph.model.nodes if graph.model else ()))
    bad_by_type: dict[str, set[str]] = {}
    other = 0
    for problem in problems:
        node_id = problem.split(":", 1)[0]
        if node_id in graph.nodes:
            bad_by_type.setdefault(graph.nodes[node_id].type, set()).add(node_id)
        else:
            other += 1
    rows = []
    for type in types:
        nodes = [n for n in graph.nodes.values() if n.type == type]
        facts = sum(1 for n in nodes for f in n.facts if f.superseded_at is None)
        edges = sum(1 for n in nodes for e in n.edges if e.superseded_at is None)
        rows.append((type, len(nodes), facts, edges, len(bad_by_type.get(type, ()))))
    if other:
        rows.append(("", 0, 0, 0, other))
    return rows


def _print_check(graph: Graph, rows: list[tuple[str, int, int, int, int]], problems: list[str]) -> None:
    """The breakdown as a table, every problem, then the verdict."""
    table = Table(box=None, pad_edge=False)
    for column, justify in (("type", "left"), ("nodes", "right"), ("facts", "right"), ("edges", "right"), ("ok", "right"), ("problems", "right")):
        table.add_column(column, justify=justify, style="dim" if column in ("facts", "edges") else None)
    for type, nodes, facts, edges, bad in rows:
        ok = nodes - bad
        table.add_row(
            type or "[dim](files)[/dim]",
            str(nodes) if type else "",
            str(facts) if type else "",
            str(edges) if type else "",
            f"[ok]{ok}[/ok]" if nodes and ok == nodes else (str(ok) if type else ""),
            f"[bad]{bad}[/bad]" if bad else "[dim]-[/dim]",
        )
    out.print(table)
    if problems:
        out.print()
        for problem in problems:
            err.print(problem, highlight=False)
        broken = {p.split(":", 1)[0] for p in problems if p.split(":", 1)[0] in graph.nodes}
        err.print(f"[bad]{len(problems)} problem{'s' if len(problems) != 1 else ''}[/bad] in {len(broken)} of {len(graph.nodes)} nodes")
    else:
        out.print(f"[ok]OK:[/ok] {len(graph.nodes)} nodes", highlight=False)


# The same check from the model's side: `bib model check` reads as "is the
# model consistent with the data", `bib check` as "are the files sound".
model_app.command("check")(check)


@hook_app.command("install")
def hook_install(root: Root = None) -> None:
    """Install a pre-commit hook that runs `bib check --since HEAD` and refuses
    destructive model changes unless BIB_ALLOW_DESTRUCTIVE is set."""
    graph = _open(root)
    try:
        path = gitops.install_hook(graph.root)
    except ValueError as error:
        raise GraphError(str(error)) from None
    typer.echo(f"Installed {path}")


@hook_app.command("remove")
def hook_remove(root: Root = None) -> None:
    """Remove the hook `bib hook install` wrote."""
    graph = _open(root)
    try:
        path = gitops.remove_hook(graph.root)
    except ValueError as error:
        raise GraphError(str(error)) from None
    typer.echo(f"Removed {path}" if path else "No hook installed")


@app.command()
def ci(
    install: Annotated[
        str, typer.Option("--install", help="The shell command that installs bib on the runner.")
    ] = "pip install bibliotheke",
    root: Root = None,
) -> None:
    """Print a GitHub Actions workflow that checks the graph on every pull request,
    shows what a model change does, and posts the report on the PR.
    Save it as .github/workflows/bibliotheke.yml at the repository root."""
    graph = _open(root)
    try:
        typer.echo(gitops.workflow(graph.root, install), nl=False)
    except ValueError as error:
        raise GraphError(str(error)) from None


@app.command()
def view(
    name: Annotated[
        Optional[str],
        typer.Argument(
            help="Start on this lens, by name or the start of it: `bib view globe`, `bib view people`. `bib view list` prints the lenses.",
            show_default=False,
        ),
    ] = None,
    color_by: Annotated[
        Optional[str], typer.Option("--color-by", help="Colour nodes by 'type', a fact key, or an edge (a node takes its target's colour).")
    ] = None,
    theme: Annotated[Optional[str], typer.Option("--theme", help="plotui (default), dracula, map or classic.")] = None,
    rels: Rels = None,
    tree: Annotated[
        Optional[str],
        typer.Option("--tree", help="A hierarchy relation (e.g. contains): lay the graph out as a still tree instead of a force simulation."),
    ] = None,
    hide: Annotated[
        Optional[list[str]], typer.Option("--hide", help="Leave out nodes of this type (repeatable), e.g. --hide claim.")
    ] = None,
    lens: Annotated[
        Optional[str], typer.Option("--lens", help="The same as the LENS argument.", hidden=True)
    ] = None,
    list_lenses: Annotated[
        bool, typer.Option("--list", help="Print the graph's lenses and exit, without opening the view.")
    ] = False,
    details: Annotated[
        bool, typer.Option("--details", help="With list: what each lens draws, hides and colours by.")
    ] = False,
    valid_at: ValidAt = None,
    known_at: KnownAt = None,
    changed_at: ChangedAt = None,
    root: Root = None,
) -> None:
    """Open the interactive graph view, on a lens by name (needs a Kitty-graphics terminal).

    A graph says how it wants to be looked at in the `view:` section of
    bibliotheke.yaml: `theme`, `color_by`, `colors`, `labels`, `size_by`,
    `rels`, `hide`/`show`, `legend`, `tree` and, under `lenses:`, any
    number of named lenses with the same keys, each one way of drawing the
    graph; j/k and tab switch between them. A lens with `layout: globe`
    puts the graph on the Earth (`place`, `via`, `arcs`), and a graph with
    `lat`/`lon` facts that declares none gets a Globe lens anyway, so
    `bib view globe` works on it. `--rel`, `--tree` and `--hide` make a
    lens of your own instead.
    """
    from bibliotheke import lenses as declared

    graph = _open(root)
    try:
        specs = declared.declared(graph)
        # `bib view list` is `--list`, unless the graph has a lens of that name.
        if list_lenses or (name == "list" and not any(s.name.lower().startswith("list") for s in specs)):
            _print_lenses(specs, details)
            return
        chosen = declared.lenses(graph, theme=theme, color_by=color_by, rels=rels, tree=tree, hide=hide, lens=name or lens)
        default = valid_at or (None if known_at or changed_at else declared.valid_at(graph))
    except ValueError as error:
        raise GraphError(str(error)) from error
    from bibliotheke.view import GraphView

    GraphView(graph, _at(default, known_at, changed_at), lenses=chosen).run()


def _lens_command(spec) -> str:
    """What to type to open a lens: its name, lower case, quoted if it has a space."""
    name = spec.name.lower()
    return f"bib view {name}" if name.replace("-", "").replace("_", "").isalnum() else f"bib view '{name}'"


def _print_lenses(specs: list, details: bool = False) -> None:
    """One line per lens: the command that opens it and what it shows.
    ``details`` adds what each draws, hides and colours by."""
    if details:
        _print_lens_details(specs)
        return
    plural = "es" if len(specs) != 1 else ""
    first = f"; `bib view` opens {escape(specs[0].name)}" if specs else ""
    out.print(f"[bold]{len(specs)} lens{plural}[/bold][dim]{first}; `bib view list --details` for more[/dim]")
    table = Table(box=None, pad_edge=False, show_header=False, padding=(0, 2, 0, 0))
    commands = [_lens_command(spec) for spec in specs]
    # The commands are what you type: never cut. Descriptions give way.
    table.add_column(style="bold cyan", no_wrap=True, min_width=max(map(len, commands), default=0))
    table.add_column(style="dim", no_wrap=True, overflow="ellipsis")
    for command, spec in zip(commands, specs):
        table.add_row(escape(command), escape(spec.description))
    out.print(table)


def _print_lens_details(specs: list) -> None:
    """One block per lens: the lists are long, so a table would not fit."""
    plural = "es" if len(specs) != 1 else ""
    out.print(f"[bold]{len(specs)} lens{plural}[/bold]")
    for spec in specs:
        labels = str(spec.labels) if spec.labels is not None else "theme default"
        if spec.label_types:
            labels += f" ({', '.join(spec.label_types)})"
        if spec.globe:
            arcs = spec.arcs if isinstance(spec.arcs, str) else ", ".join(spec.arcs)
            look = [
                "globe",
                f"places {', '.join(spec.place) if spec.place else 'inferred'}",
                f"via {', '.join(spec.via) if spec.via else 'inferred'}",
                f"arcs {arcs}",
                f"colour by {spec.color_by}",
            ]
        else:
            look = [
                f"tree ({spec.tree})" if spec.tree else "force",
                f"colour by {spec.color_by}",
                f"size by {spec.size_by or 'degree'}",
                f"labels {labels}",
            ]
        if spec.min_parallel > 1:
            look.append(f"pairs joined {spec.min_parallel}+ times")
        if spec.hide_unlinked:
            look.append("unlinked nodes left out")
        if spec.legend is not None:
            look.append("no legend" if not spec.legend else f"legend {', '.join(spec.legend)}")
        out.print(f"\n[bold cyan]{escape(_lens_command(spec))}[/bold cyan]  [dim]{escape(spec.description)}[/dim]")
        if not spec.globe:
            out.print(f"   draws   {escape(', '.join(spec.rels)) if spec.rels else 'every relation'}")
        out.print(f"   hides   {escape(', '.join(spec.hide)) if spec.hide else 'nothing'}")
        out.print(f"   [dim]{escape(' · '.join(look))}[/dim]")


@app.command(hidden=True)
def globe(
    valid_at: ValidAt = None,
    known_at: KnownAt = None,
    changed_at: ChangedAt = None,
    root: Root = None,
) -> None:
    """The same as `bib view globe`."""
    view(name="globe", color_by=None, theme=None, rels=None, tree=None, hide=None, lens=None, list_lenses=False, details=False,
         valid_at=valid_at, known_at=known_at, changed_at=changed_at, root=root)


def main() -> None:
    try:
        app()
    except GraphError as error:
        err.print(f"[bad]error:[/bad] {error}")
        raise SystemExit(1) from None
