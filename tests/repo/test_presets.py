from bibliotheke import At, Graph
from bibliotheke.model import Node
from bibliotheke.repo import presets
from bibliotheke.view import build_scene


def _repo_graph():
    """A hand-built repository graph: eight top-level directories, so the
    long tail must fold, and one file everyone imports."""
    nodes = []
    for i in range(8):
        for j in range(8 - i):  # d0 has 8 files, d7 has 1
            nodes.append(_file(f"d{i}/f{j}.py", top=f"d{i}"))
    hub = nodes[0]
    for node in nodes[1:5]:
        node.edges.append(_edge("imports", hub.id))
    nodes.append(Node(id="d0", type="dir", name="d0/"))
    nodes.append(Node(id="anna", type="author", name="Anna"))
    return Graph.from_nodes(nodes, marker=presets_marker())


def presets_marker():
    from bibliotheke.repo import MARKER

    return MARKER


def _file(path, top):
    node = Node(id=path.replace("/", "-").replace(".", "-"), type="file", name=path)
    node.facts.append(_fact("dir", top))
    return node


def _fact(key, value):
    from bibliotheke.model import Fact

    return Fact(id=f"f{key}{value}", key=key, value=value, known_at="2026-01-01", changed_at="2026-01-01")


def _edge(rel, to):
    from bibliotheke.model import Edge

    return Edge(id=f"e{rel}{to}", rel=rel, to=to, known_at="2026-01-01", changed_at="2026-01-01")


def test_top_dirs_grouping_and_colours():
    graph = _repo_graph()
    top = presets.top_dirs(graph)
    assert top == ["d0", "d1", "d2", "d3", "d4", "d5"]
    group = presets.grouping(top)
    assert group("d0") == "d0" and group("d7") == "other" and group("dir") == "dir" and group("author") == "author"
    colors = presets.colors(top)
    assert set(colors) == {*top, "other", "dir", "author"}
    assert len({colors[d] for d in top}) == 6  # six distinct hues
    scene = build_scene(graph, At(), "dir", presets.theme(graph, "map", top), None, group, presets.importers(graph), presets.label)
    assert sorted(scene.legend) == ["author", "d0", "d1", "d2", "d3", "d4", "d5", "dir", "other"]
    assert scene.legend["dir"] == presets.DIR and scene.legend["other"] == presets.OTHER


def test_weights_and_labels_favour_imported_files():
    graph = _repo_graph()
    weight = presets.importers(graph)
    assert weight(graph.node("d0-f0-py")) == 5.0  # imported by four files, plus one
    assert weight(graph.node("d7-f0-py")) == 1.0
    assert weight(graph.node("d0")) == 0.0 and weight(graph.node("anna")) == 0.0
    assert presets.label(graph.node("d0-f0-py")) == "d0/f0.py"
    assert presets.label(Node(id="x", type="file", name="src/pkg/harness/budget.py")) == "harness/budget.py"
    assert presets.label(Node(id="d", type="dir", name="src/pkg/")) == "pkg"
    theme = presets.theme(graph)
    assert theme.halo == 0.0 and theme.labels == presets.LABELS and theme.edge_colors["contains"] == presets.EDGES["contains"]


def test_lenses_cover_the_relations():
    graph = _repo_graph()
    found = presets.lenses(graph)
    assert [lens.name for lens in found] == ["Structure", "Map", "Dependencies", "Coupling", "People"]
    assert found[0].tree == "contains" and found[1].tree is None
    assert {rel for lens in found for rel in (lens.rels or [])} == {"contains", "imports", "co_changed", "touched"}
    assert all(lens.theme.halo == 0.0 for lens in found)
