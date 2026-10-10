"""``bib browse``: walk the graph as a tree, types to nodes to details, in
any terminal. The first thing to run on a graph you do not know."""

from __future__ import annotations

from collections import Counter
from typing import Any

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Header, Input, Static, Tree
from textual.widgets.tree import TreeNode

from bibliotheke.graph import At, Graph
from bibliotheke.model import Node

BODY_LINES = 24  # how much of a body the details pane shows


def type_counts(graph: Graph, at: At) -> list[tuple[str, int]]:
    """Node types with how many nodes each has in view, most first; types the
    model declares but no node uses come last, with zero."""
    counts = Counter(node.type for node in graph.list_nodes(at))
    if graph.model:
        for type in graph.model.nodes:
            counts.setdefault(type, 0)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def type_description(graph: Graph, type: str) -> str:
    spec = graph.model.nodes.get(type) if graph.model else None
    return spec.description if spec else ""


def node_text(graph: Graph, node: Node, at: At, numbered: list[tuple[str, str]] | None = None) -> str:
    """A node's details as Rich markup: facts, edges both ways, the start
    of its body. With ``numbered`` (a list to fill), each edge gets a number
    and the node at its other end is appended, so a reader can follow it."""
    name = lambda node_id: escape(graph.nodes[node_id].name) if node_id in graph.nodes else escape(node_id)  # noqa: E731

    def tag(node_id: str) -> str:
        if numbered is None:
            return "  "
        numbered.append(("node", node_id))
        return f"[bold]{len(numbered):3d}[/bold] "

    facts = graph.facts(node.id, at)
    outgoing = graph.out_edges(node.id, at)
    incoming = graph.in_edges(node.id, at)
    # The span most of the node's records share is said once, up top; a
    # record shows its own only when it differs. (A paper imported with its
    # publication date on everything would otherwise repeat it forty times.)
    spans = Counter((r.valid_from, r.valid_to) for r in (*facts, *outgoing, *(e for _, e in incoming)))
    common = spans.most_common(1)[0][0] if spans and spans.most_common(1)[0][1] > 1 else (None, None)
    spec = graph.model.nodes.get(node.type) if graph.model else None
    event = bool(spec and spec.event)
    span = lambda r: "" if (r.valid_from, r.valid_to) == common else _span(r, event)  # noqa: E731
    head = f"[dim]{escape(node.type)} · {escape(node.id)}"
    if common != (None, None):
        # An event happens at an instant: say when, not "from then on".
        head += f" · {common[0]}" if event and common[1] is None else f" · valid {common[0] or '…'} → {common[1] or '…'}"
    lines = [f"[bold]{escape(node.name)}[/bold]", head + "[/dim]"]
    if node.notes:
        lines += ["", escape(node.notes.rstrip())]
    if facts:
        lines += ["", "[bold]Facts[/bold]"]
        lines += [f"  {escape(f.key)}: {escape(str(f.value))}{span(f)}" for f in facts]
    if outgoing:
        lines += ["", "[bold]Edges out[/bold]"]
        lines += [f"{tag(e.to)}→ {escape(e.rel)} {name(e.to)}{span(e)}" for e in outgoing]
    if incoming:
        lines += ["", "[bold]Edges in[/bold]"]
        lines += [f"{tag(other.id)}← {name(other.id)} {escape(e.rel)}{span(e)}" for other, e in incoming]
    if graph.has_body(node):
        body = graph.body(node.id).rstrip().splitlines()
        lines += ["", "[bold]Body[/bold]"]
        lines += ["  " + escape(line) for line in body[:BODY_LINES]]
        if len(body) > BODY_LINES:
            lines.append(f"  [dim]… {len(body) - BODY_LINES} more lines[/dim]")
    return "\n".join(lines)


def type_text(graph: Graph, type: str, count: int) -> str:
    """What the model says about a type, and how many there are."""
    lines = [f"[bold]{escape(type)}[/bold]", f"[dim]{count} node{'s' if count != 1 else ''}[/dim]"]
    spec = graph.model.nodes.get(type) if graph.model else None
    if spec is None:
        lines += ["", "[dim]Not in the data model.[/dim]" if graph.model else "[dim]The graph has no data model.[/dim]"]
        return "\n".join(lines)
    if spec.description:
        lines += ["", escape(spec.description)]
    lines += ["", f"[bold]name[/bold]: {escape(spec.name)}"]
    if spec.body:
        lines.append(f"[bold]body[/bold]: {escape(spec.body)}")
    if spec.facts:
        lines += ["", "[bold]Facts[/bold]"]
        for key, fact in spec.facts.items():
            flags = " ".join(flag for flag, on in (("required", fact.required), ("many", fact.many)) if on)
            kind = fact.type + (f" ({fact.values})" if isinstance(fact.values, str) else "")
            lines.append(f"  {escape(key)}: {escape(kind)}{'  ' + flags if flags else ''}{'  ' + escape(fact.description) if fact.description else ''}")
    out = [(rel, e) for rel, e in graph.model.edges.items() if type in e.sources]
    into = [(rel, e) for rel, e in graph.model.edges.items() if type in e.targets and type not in e.sources]
    if out or into:
        lines += ["", "[bold]Edges[/bold]"]
        lines += [f"  → {escape(rel)} {escape(', '.join(e.targets))}" for rel, e in out]
        lines += [f"  ← {escape(', '.join(e.sources))} {escape(rel)}" for rel, e in into]
    return "\n".join(lines)


def _span(record: Any, event: bool = False) -> str:
    if not record.valid_from and not record.valid_to:
        return ""
    if event and not record.valid_to:
        return f" [dim]({record.valid_from})[/dim]"
    return f" [dim]({record.valid_from or '…'} → {record.valid_to or '…'})[/dim]"


PAGE = 40  # nodes listed before "… and N more"


class Walk:
    """The same walk as a prompt loop in the plain terminal: every step
    prints into the scrollback and stays there. Numbers pick from the last
    list, ``/text`` filters, ``..`` goes back, ``q`` quits; on a node, the
    numbers are its edges, so you can keep walking across the graph."""

    def __init__(self, graph: Graph, at: At, console: Any, read: Any = input) -> None:
        self.graph = graph
        self.at = at
        self.out = console
        self.read = read
        self.type: str | None = None  # the type whose nodes were listed
        self.node: Node | None = None  # the node whose details were shown
        self.needle = ""
        self.choices: list[tuple[str, str]] = []  # what a number picks: ("type"|"node", id)

    def run(self) -> None:
        self._types()
        while True:
            try:
                line = self.read(self._prompt()).strip()
            except (EOFError, KeyboardInterrupt):
                self.out.print()
                return
            if line in ("q", "quit", "exit"):
                return
            if line == "..":
                self._back()
            elif line.startswith("/"):
                self.needle = line[1:].strip()
                self._nodes() if self.type else self._types()
            elif line == "":
                self._again()
            elif line.isdigit() and 1 <= int(line) <= len(self.choices):
                kind, ident = self.choices[int(line) - 1]
                self._type(ident) if kind == "type" else self._node(ident)
            elif line in dict(type_counts(self.graph, self.at)):
                self._type(line)
            elif line in self.graph.nodes:
                self._node(line)
            else:
                self.out.print(f"[dim]? {escape(line)}: a number from the list, a type, a node id, /text, .. or q[/dim]")

    def _prompt(self) -> str:
        where = self.node.id if self.node else self.type or self.graph.root.name or "graph"
        return f"{where}> "

    def _again(self) -> None:
        if self.node:
            self._node(self.node.id)
        elif self.type:
            self._nodes()
        else:
            self._types()

    def _back(self) -> None:
        if self.node:
            self.node = None
            self._nodes() if self.type else self._types()
        elif self.type:
            self.type, self.needle = None, ""
            self._types()
        else:
            self._types()

    def _types(self) -> None:
        self.type = self.node = None
        self.choices = []
        counts = type_counts(self.graph, self.at)
        self.out.print()
        for i, (type, count) in enumerate(counts, 1):
            self.choices.append(("type", type))
            description = type_description(self.graph, type)
            self.out.print(f"[bold]{i:3d}[/bold]  {escape(type):14s} [dim]{count:5d}[/dim]  {escape(description)}")
        self.out.print(f"[dim]{sum(c for _, c in counts)} nodes in {len(counts)} types · a number or a type lists its nodes[/dim]")

    def _type(self, type: str) -> None:
        self.type, self.node, self.needle = type, None, ""
        self._nodes()

    def _nodes(self) -> None:
        assert self.type
        self.choices = []
        nodes = sorted(self.graph.list_nodes(self.at, type=self.type), key=lambda n: n.name.casefold())
        if self.needle:
            needle = self.needle.casefold()
            nodes = [n for n in nodes if needle in n.name.casefold() or needle in n.id.casefold()]
        self.out.print()
        head = f"[bold]{escape(self.type)}[/bold]  [dim]{len(nodes)} node{'s' if len(nodes) != 1 else ''}"
        self.out.print(head + (f", matching /{escape(self.needle)}" if self.needle else "") + "[/dim]")
        for i, node in enumerate(nodes[:PAGE], 1):
            self.choices.append(("node", node.id))
            self.out.print(f"[bold]{i:3d}[/bold]  {escape(node.name)}  [dim]{escape(node.id)}[/dim]")
        if len(nodes) > PAGE:
            self.out.print(f"[dim]     … and {len(nodes) - PAGE} more; /text narrows the list[/dim]")

    def _node(self, node_id: str) -> None:
        node = self.graph.node(node_id)
        self.node = node
        self.choices = []
        self.out.print()
        self.out.print(node_text(self.graph, node, self.at, numbered=self.choices))
        if self.choices:
            self.out.print(f"[dim]a number follows that edge; .. goes back to {escape(self.type or 'the types')}[/dim]")


class Browser(App):
    CSS = """
    #body { height: 1fr; }
    #tree { width: 44; height: 1fr; border-right: thick $accent; }
    #details { width: 1fr; height: 1fr; padding: 0 2; }
    #filter { display: none; }
    #filter.shown { display: block; }
    """
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("slash", "filter", "Filter"),
        Binding("escape", "clear_filter", "Clear", show=False),
    ]

    def __init__(self, graph: Graph, at: At = At()) -> None:
        super().__init__()
        self.graph = graph
        self.at = at
        self.title = f"bibliotheke — {graph.root.name}"
        self.sub_title = "  ".join(
            f"{label} {value}" for label, value in (("valid", at.valid_at), ("known", at.known_at), ("changed", at.changed_at)) if value
        )
        self.needle = ""

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            yield Tree(self.graph.root.name or "graph", id="tree")
            with VerticalScroll(id="details"):
                yield Static("", id="text")
        yield Input(placeholder="filter nodes by name…", id="filter")
        yield Footer()

    def on_mount(self) -> None:
        self._fill_tree()
        tree = self.query_one("#tree", Tree)
        tree.root.expand()
        tree.focus()
        self._show_overview()

    # -- the tree ------------------------------------------------------------

    def _fill_tree(self) -> None:
        tree = self.query_one("#tree", Tree)
        tree.clear()
        for type, count in type_counts(self.graph, self.at):
            nodes = self._matching(type)
            if self.needle and not nodes:
                continue
            label = f"{type}  [dim]{len(nodes) if self.needle else count}[/dim]"
            branch = tree.root.add(label, data=("type", type, count), allow_expand=bool(nodes))
            if self.needle:  # a filter opens every type that matches
                self._populate_type(branch)
                branch.expand()

    def _matching(self, type: str) -> list[Node]:
        """The type's nodes in view, those matching the filter when one is set.
        (Not ``_nodes``: Textual's DOM owns that name.)"""
        nodes = self.graph.list_nodes(self.at, type=type)
        if self.needle:
            needle = self.needle.casefold()
            nodes = [n for n in nodes if needle in n.name.casefold() or needle in n.id.casefold()]
        return sorted(nodes, key=lambda n: n.name.casefold())

    def _populate_type(self, branch: TreeNode) -> None:
        if branch.children or branch.data is None or branch.data[0] != "type":
            return
        for node in self._matching(branch.data[1]):
            branch.add_leaf(escape(node.name), data=("node", node.id))

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        self._populate_type(event.node)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        data = event.node.data
        if data is None:
            self._show_overview()
        elif data[0] == "type":
            self._show_text(type_text(self.graph, data[1], data[2]))
        else:
            self._show_text(node_text(self.graph, self.graph.node(data[1]), self.at))

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        if event.node.data and event.node.data[0] == "type":
            event.node.toggle()

    # -- the details ---------------------------------------------------------

    def _show_text(self, text: str) -> None:
        self.query_one("#text", Static).update(text)
        self.query_one("#details", VerticalScroll).scroll_home(animate=False)

    def _show_overview(self) -> None:
        graph = self.graph
        counts = type_counts(graph, self.at)
        lines = [f"[bold]{escape(graph.root.name or 'graph')}[/bold]"]
        if graph.model and graph.model.description:
            lines += ["", escape(graph.model.description)]
        lines += ["", f"{sum(c for _, c in counts)} nodes in {len(counts)} types"]
        if self.sub_title:
            lines.append(escape(self.sub_title))
        lines += ["", "Open a type to list its nodes; a node shows its facts, edges and body.", "/ filters by name, esc clears, q quits."]
        self._show_text("\n".join(lines))

    # -- filtering -----------------------------------------------------------

    def action_filter(self) -> None:
        box = self.query_one("#filter", Input)
        box.add_class("shown")
        box.focus()

    def action_clear_filter(self) -> None:
        box = self.query_one("#filter", Input)
        box.value = ""
        box.remove_class("shown")
        self.needle = ""
        self._fill_tree()
        self.query_one("#tree", Tree).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.needle = event.value.strip()
        self._fill_tree()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.query_one("#tree", Tree).focus()
