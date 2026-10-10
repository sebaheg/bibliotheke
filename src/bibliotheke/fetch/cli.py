"""``bib fetch``: a URL into the graph."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Optional

import typer
import yaml

from bibliotheke.fetch import Options, handler_for, prepare, write
from bibliotheke.graph import Graph


def fetch(
    url: Annotated[str, typer.Argument(help="A YouTube video, an arXiv paper, a GitHub repository, or any web page.")],
    type: Annotated[Optional[str], typer.Option("--type", help="Node type to use instead of the source's own (video, paper, repository, article).")] = None,
    id: Annotated[Optional[str], typer.Option("--id", help="Node id to use; default: the one fetched before, or from the title.")] = None,
    lang: Annotated[str, typer.Option("--lang", help="Caption language for videos.")] = "en",
    transcribe: Annotated[
        str, typer.Option("--transcribe", help="For videos: captions, whisper, or auto (captions when there are any, else whisper).")
    ] = "auto",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print the batch this would import and write nothing.")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print the result as JSON.")] = False,
    root: Annotated[
        Optional[Path], typer.Option("--root", help="Graph directory. Default: the nearest parent with bibliotheke.yaml.")
    ] = None,
) -> None:
    """Fetch a URL into the graph: a node, the files behind it under sources/, and the people it names.

    The transcript, text or digest goes to `sources/<kind>/`, and the node's
    `file` fact points at it, so an agent can read it and extract claims
    with `bib import`. Fetching the same URL again updates the node instead
    of adding a second one. With a model, what the model does not allow is
    dropped and reported, not refused; `bib model add source <kind>` adds the
    types and facts a kind needs.
    """
    if transcribe not in ("captions", "whisper", "auto"):
        raise typer.BadParameter("one of captions, whisper, auto", param_hint="--transcribe")
    graph = Graph.open(root)
    handler, key = handler_for(url)
    fetched = handler.fetch(key, Options(lang=lang, transcribe=transcribe))
    if dry_run:
        prepared = prepare(graph, fetched, type=type, id=id)
        shown = {**prepared.batch, "files": {rel: _size(content) for rel, content in fetched.files.items()}}
        if prepared.dropped:
            shown["dropped"] = prepared.dropped
        if fetched.links:
            shown["links"] = fetched.links
        typer.echo(json.dumps(shown, indent=2) if as_json else yaml.safe_dump(shown, sort_keys=False, allow_unicode=True, width=100), nl=False)
        return
    result = write(graph, fetched, type=type, id=id)
    if as_json:
        typer.echo(json.dumps({
            "kind": fetched.kind,
            "key": fetched.key,
            "type": result.type,
            "id": result.node_id,
            "existing": result.existing,
            "counts": dict(result.counts),
            "files": result.files,
            "dropped": result.dropped,
            "links": fetched.links,
        }, indent=2))
        return
    verb = "updated" if result.existing else "added"
    typer.echo(f"{result.type} {result.node_id} {verb}: " + ", ".join(f"{n} {what}" for what, n in sorted(result.counts.items()) if n))
    for rel in result.files:
        typer.echo(f"  {rel} ({_size(fetched.files[rel])})")
    for note in result.dropped:
        typer.echo(f"  dropped: {note}")
    if fetched.links:
        typer.echo("  also mentions: " + ", ".join(fetched.links))


def _size(content: str | bytes) -> str:
    if isinstance(content, bytes):
        return f"{len(content):,} bytes"
    return f"{len(content.split()):,} words"
