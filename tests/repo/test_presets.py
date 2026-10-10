from bibliotheke import At, Graph, lenses
from bibliotheke.model import Edge, Fact, Node
from bibliotheke.repo import MARKER, presets
from bibliotheke.view import Picture, build_scene


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
    return Graph.from_nodes(nodes, marker=MARKER)


def _file(path, top):
    node = Node(id=path.replace("/", "-").replace(".", "-"), type="file", name=path)
    node.facts.append(_fact("dir", top))
    return node


def _fact(key, value):
    return Fact(id=f"f{key}{value}", key=key, value=value, known_at="2026-01-01", changed_at="2026-01-01")


def _edge(rel, to):
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
    lens = presets.lenses(graph)[0]
    scene = build_scene(graph, At(), "dir", lens.theme, None, group, lens.weight, lens.label)
    assert sorted(scene.legend) == ["author", "d0", "d1", "d2", "d3", "d4", "d5", "dir", "other"]
    assert scene.legend["dir"] == presets.DIR and scene.legend["other"] == presets.OTHER


def test_weights_and_labels_favour_imported_files():
    graph = _repo_graph()
    lens = presets.lenses(graph)[0]
    assert lens.weight(graph.node("d0-f0-py")) == 4.0  # imported by four files
    assert lens.weight(graph.node("d7-f0-py")) == 0.0
    assert lens.weight(graph.node("d0")) == 0.0 and lens.weight(graph.node("anna")) == 0.0
    assert lens.label(graph.node("d0-f0-py")) == "d0/f0.py"
    assert lens.label(graph.node("d0")) == "", "only files are labelled"
    assert presets.label(Node(id="x", type="file", name="src/pkg/harness/budget.py")) == "harness/budget.py"
    assert presets.label(Node(id="d", type="dir", name="src/pkg/")) == "pkg"
    theme = lens.theme
    assert theme.halo == 0.0 and theme.labels == 6 and theme.edge_colors["contains"] == (0x4A, 0x4D, 0x56)


def test_lenses_are_the_markers_and_cover_the_relations():
    graph = _repo_graph()
    found = presets.lenses(graph)
    assert [lens.name for lens in found] == [spec["name"] for spec in MARKER["view"]["lenses"]]
    assert [lens.name for lens in found] == ["Structure", "Map", "Dependencies", "Coupling", "People"]
    assert found[0].tree == "contains" and found[1].tree is None and found[3].min_parallel == 3
    assert {rel for lens in found for rel in (lens.rels or [])} == {"contains", "imports", "co_changed", "touched"}
    assert all(lens.theme.halo == 0.0 for lens in found)
    assert lenses.problems(graph) == [] and lenses.valid_at(graph) is not None
    # The scanner's marker is an ordinary graph's: nothing in it needs presets.py to draw.
    plain = lenses.lenses(graph)
    picture = Picture(graph, At(), plain[0])
    assert len(picture.scene.nodes) == 5, "the hub and the four files importing it: nothing else takes part in a drawn relation"


def test_command_line_overrides_reach_the_repository_lenses():
    graph = _repo_graph()
    (custom,) = presets.lenses(graph, rels=["imports"])
    assert custom.name == "Custom" and custom.rels == ["imports"] and custom.tree is None
    (custom,) = presets.lenses(graph, rels=["contains", "imports"], tree="contains")
    assert custom.tree == "contains"
    by_ext = presets.lenses(graph, color_by="ext")
    assert by_ext[0].group is None and by_ext[0].color_by == "ext"
    assert [lens.name for lens in presets.lenses(graph, lens="people")][0] == "People"
