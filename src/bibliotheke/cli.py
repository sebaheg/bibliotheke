"""The `bib` command line: the one interface for people and agents."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated, Any, Callable, Optional

import typer
import yaml
from rich.console import Console
from rich.table import Table

from bibliotheke import analysis
from bibliotheke import modeledit
from bibliotheke import diff as differ
from bibliotheke import export as exporter
from bibliotheke import store
from bibliotheke.datamodel import DataModel, ModelError
from bibliotheke.graph import At, Graph, GraphError
from bibliotheke.model import Node, Record

app = typer.Typer(
    help="A files-only knowledge graph with three time axes: valid, knowledge and change time.",
    no_args_is_help=True,
    add_completion=False,
)
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

# The repository scanner lives in its own package and only uses the public API;
# this is the one place the rest of bibliotheke refers to it.
from bibliotheke.repo.cli import repo  # noqa: E402

app.command("repo")(repo)

out = Console()
err = Console(stderr=True)

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


def _span(record: Record) -> str:
    if not record.valid_from and not record.valid_to:
        return "always"
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


def _row(record: Record, cells: list[str], history: bool) -> list[str]:
    row = [record.id, *cells, _span(record), _short(record.known_at), _short(record.changed_at)]
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
    name = lambda node_id: graph.nodes[node_id].name if node_id in graph.nodes else node_id  # noqa: E731
    sections = (
        ("Facts", ["key", "value"], node.facts, lambda f: [f.key, str(f.value)]),
        ("Edges out", ["rel", "to"], node.edges, lambda e: [e.rel, name(e.to)]),
    )
    for title, columns, records, cells in sections:
        visible = [r for r in records if history or at.sees(r)]
        if visible:
            table = _record_table(title, columns, history)
            for record in visible:
                table.add_row(*_row(record, cells(record), history), style="dim" if record.superseded_at else None)
            out.print()
            out.print(table)
    if data["in_edges"]:
        table = _record_table("Edges in", ["from", "rel"], history)
        for other, edge in graph.in_edges(node.id, at):
            table.add_row(*_row(edge, [other.name, edge.rel], history), style="dim" if edge.superseded_at else None)
        out.print()
        out.print(table)


def _print_nodes(nodes: list[Node], as_json: bool) -> None:
    if as_json:
        _echo_json([{"id": n.id, "type": n.type, "name": n.name} for n in nodes])
        return
    table = Table(box=None, pad_edge=False)
    for column in ("id", "type", "name"):
        table.add_column(column)
    for node in sorted(nodes, key=lambda n: (n.type, n.id)):
        table.add_row(node.id, node.type, node.name)
    out.print(table)


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
    valid_at: ValidAt = None,
    known_at: KnownAt = None,
    changed_at: ChangedAt = None,
    as_json: AsJson = False,
    at_commit: AtCommit = None,
    root: Root = None,
) -> None:
    """List nodes."""
    _print_nodes(_open(root, at_commit).list_nodes(_at(valid_at, known_at, changed_at), type=type), as_json)


@app.command()
def search(
    text: str,
    valid_at: ValidAt = None,
    known_at: KnownAt = None,
    changed_at: ChangedAt = None,
    as_json: AsJson = False,
    at_commit: AtCommit = None,
    root: Root = None,
) -> None:
    """Find nodes whose name, notes, facts or edges contain the text."""
    _print_nodes(_open(root, at_commit).search(text, _at(valid_at, known_at, changed_at)), as_json)


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
    if data["time"] == "ignored":
        out.print("[dim]Valid time is not used in this graph.[/dim]")
    for type, spec in data["nodes"].items():
        out.print()
        out.print(f"[bold]{type}[/bold]  [dim]{spec['description']}[/dim]")
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
    """Apply a change to bibliotheke.yaml, say what was done, and return the
    reopened graph."""
    graph = _open(root)
    graph._mutable()
    try:
        marker = change(graph.marker)
    except ModelError as error:
        raise GraphError(str(error)) from None
    modeledit.save_marker(graph.root, marker)
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
        out.print("[green]Data OK[/green]")
        return
    out.print(f"[red]{len(problems)} problem{'s' if len(problems) != 1 else ''} in the data:[/red]")
    for problem in problems:
        err.print(f"  {problem}")


def _print_changes(title: str, changes: list[modeledit.Change]) -> None:
    out.print(f"[bold]{title}[/bold]")
    if not changes:
        out.print("  no change")
        return
    marks = {modeledit.ADDITIVE: "[green]+[/green]", modeledit.DESTRUCTIVE: "[red]-[/red]", modeledit.NEUTRAL: "[dim]~[/dim]"}
    table = Table(box=None, pad_edge=False, show_header=False)
    for column in ("mark", "where", "what", "kind"):
        table.add_column(column, style="dim" if column == "kind" else None)
    for kind in modeledit.KINDS:
        for change in changes:
            if change.kind == kind:
                table.add_row(f"  {marks[kind]}", change.where, change.what, kind)
    out.print(table)


def _csv(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


@model_add.command("node")
def model_add_node(
    type: Annotated[str, typer.Argument(help="Node type, lowercase with hyphens, e.g. part-of-speech.")],
    description: Description = "",
    id: Annotated[Optional[str], typer.Option("--id", help="Id template, e.g. '<name>-<lang>'.")] = None,
    body: Annotated[str, typer.Option("--body", help="What the Markdown body holds.")] = "",
    root: Root = None,
) -> None:
    """Add a node type. On a graph without a model, this starts one."""
    graph = _model_write(
        root,
        lambda m: modeledit.add_node_type(m, type, description=description, id=id, body=body),
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
    modeledit.save_marker(graph.root, marker)
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
    modeledit.save_marker(graph.root, marker)
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
    modeledit.save_marker(graph.root, marker)
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
    root: Root = None,
) -> None:
    """How the model changed between two git revisions, and what the change breaks.

    Each change is marked additive (+), destructive (-) or neutral (~), then
    the data is checked against the newer model. Exits 1 when something breaks.
    """
    old = _open(root, rev1)
    new = _open(root, rev2)
    changes = modeledit.diff_models(old.model, new.model)
    problems = new.model.problems(new.nodes, new.root) if new.model else []
    if as_json:
        _echo_json(
            {
                "from": old.source.commit,
                "to": new.source.commit if rev2 else "working tree",
                "changes": [c.to_dict() for c in changes],
                "problems": problems,
            }
        )
    else:
        _print_changes(f"Model changes, {rev1} → {rev2 or 'working tree'}", changes)
        _print_problems(problems)
    if problems:
        raise typer.Exit(1)


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


@model_app.command("edit")
def model_edit(force: Force = False, root: Root = None) -> None:
    """Edit bibliotheke.yaml in $EDITOR, with the result checked before it is saved.

    The edited file is parsed, diffed against the current model and checked
    against the data. A change that breaks current records is not saved
    unless --force.
    """
    graph = _open(root)
    graph._mutable()
    path = graph.root / store.ROOT_MARKER
    before = path.read_text()
    after = _edit_text(before)
    if after is None or after == before:
        out.print("No change")
        return
    try:
        marker = yaml.safe_load(after)
        if not isinstance(marker, dict):
            raise ModelError("must be a mapping")
        model = DataModel.from_marker(marker)
    except (yaml.YAMLError, ModelError) as error:
        raise GraphError(f"not saved: {error}") from None
    changes = modeledit.diff_models(graph.model, model)
    problems = model.problems(graph.nodes, graph.root) if model else []
    _print_changes("Model changes", changes)
    _print_problems(problems)
    if problems and not force:
        raise GraphError("not saved; fix the data or the model, or pass --force to save anyway")
    path.write_text(after)
    out.print(f"Saved {store.ROOT_MARKER}")


@app.command()
def check(root: Root = None) -> None:
    """Validate the graph files: dangling edges, misplaced files, duplicate records,
    orphaned bodies, `file` facts that name missing files, and anything the data
    model in bibliotheke.yaml does not allow."""
    graph = _open(root)
    problems = graph.check()
    for problem in problems:
        err.print(problem)
    if problems:
        raise typer.Exit(1)
    typer.echo(f"OK: {len(graph.nodes)} nodes")


@app.command()
def view(
    color_by: Annotated[
        Optional[str], typer.Option("--color-by", help="Colour nodes by 'type', a fact key, or an edge (a node takes its target's colour).")
    ] = None,
    theme: Annotated[Optional[str], typer.Option("--theme", help="'map' (default) or 'classic'.")] = None,
    rels: Rels = None,
    tree: Annotated[
        Optional[str],
        typer.Option("--tree", help="A hierarchy relation (e.g. contains): lay the graph out as a still tree instead of a force simulation."),
    ] = None,
    valid_at: ValidAt = None,
    known_at: KnownAt = None,
    changed_at: ChangedAt = None,
    root: Root = None,
) -> None:
    """Open the interactive graph view (needs a Kitty-graphics terminal).

    Defaults come from an optional `view:` section in bibliotheke.yaml:
    `theme`, `color_by`, `colors` (category -> "#rrggbb") and `labels`
    (how many of the best-connected nodes to name).
    """
    from bibliotheke.view import GraphView, theme_for

    graph = _open(root)
    settings = _settings(graph, "view")
    try:
        chosen = theme_for(theme or settings.get("theme") or "map", settings.get("colors"), settings.get("labels"))
    except ValueError as error:
        raise GraphError(str(error)) from error
    at = _at(valid_at, known_at, changed_at)
    GraphView(graph, at, color_by or settings.get("color_by") or "type", chosen, rels, tree=tree or settings.get("tree")).run()


@app.command()
def globe(
    who: Annotated[
        Optional[str], typer.Option("--who", help="Comma-separated node types to place. Default: person.")
    ] = None,
    theme: Annotated[
        Optional[str], typer.Option("--theme", help="Globe look: dark (default), earth, light, blueprint or wire.")
    ] = None,
    valid_at: ValidAt = None,
    known_at: KnownAt = None,
    changed_at: ChangedAt = None,
    root: Root = None,
) -> None:
    """Show where things are on a globe (needs a Kitty-graphics terminal).

    A place is any node with `lat` and `lon` facts. Other nodes are placed by
    following `based_in`, then `located_in`, then `affiliated_with` edges, so
    a person is at their organisation's city unless they have a `based_in`
    of their own. Arcs join places linked through a shared node such as a
    paper. Defaults come from an optional `globe:` section in
    bibliotheke.yaml: `who`, `via` and `theme`.
    """
    from bibliotheke.globe import GlobeView
    from bibliotheke.geo import VIA, WHO

    graph = _open(root)
    settings = _settings(graph, "globe")
    kinds = tuple(t.strip() for t in (who or ",".join(settings.get("who") or WHO)).split(",") if t.strip())
    via = tuple(settings.get("via") or VIA)
    at = _at(valid_at, known_at, changed_at)
    GlobeView(graph, at, kinds, via, theme or settings.get("theme") or "dark").run()


def main() -> None:
    try:
        app()
    except GraphError as error:
        err.print(f"[red]error:[/red] {error}")
        raise SystemExit(1) from None
