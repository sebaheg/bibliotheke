import pytest

from bibliotheke import At, Graph
from bibliotheke import store
from bibliotheke.view import (
    THEMES,
    GraphView,
    build_scene,
    fit,
    label_texts,
    layout_edges,
    node_size,
    short_name,
    theme_for,
)


def people(tmp_path):
    graph = Graph.init(tmp_path)
    graph.add_node("person", "Anna Berg")
    graph.add_node("person", "Bo Ek")
    graph.add_node("organisation", "DeepMind")
    graph.add_fact("anna-berg", "field", "ai")
    graph.add_edge("anna-berg", "works_at", "deepmind", valid_from=2019, valid_to=2022)
    graph.add_edge("bo-ek", "knows", "anna-berg", valid_from=2010)
    return graph


def test_scene_follows_time_filter_and_color_key(tmp_path):
    graph = people(tmp_path)
    scene = build_scene(graph, At(), color_by="field")
    assert [n.id for n in scene.nodes] == ["anna-berg", "bo-ek", "deepmind"]
    assert scene.edges == [(0, 2), (1, 0)]
    assert scene.categories == ["ai", "person", "organisation"]
    assert len(set(scene.legend.values())) == 3

    assert build_scene(graph, At(valid_at=2015)).edges == [(1, 0)]


def test_layout_links_components_through_a_hub():
    assert layout_edges(3, [(0, 1), (1, 2)]) == [(0, 1), (1, 2)]
    # Components {0, 1, 2} (anchored on its best-connected node) and {3}.
    assert layout_edges(4, [(0, 1), (1, 2)]) == [(0, 1), (1, 2), (4, 1), (4, 3)]


def test_fit_centres_and_scales():
    xs, ys, zs = fit([0.0, 10.0], [5.0, 5.0], [2.0, 4.0])
    assert xs == [-1.0, 1.0] and ys == [0.0, 0.0]
    assert zs == pytest.approx([-0.2, 0.2])


async def test_view_runs_headless(tmp_path):
    app = GraphView(people(tmp_path), At(), "field")
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        app._show_node(0)
        app._show_edge(0)
        await pilot.press("escape")
    pixels = app.plot.render_rgba(320, 240)
    assert len(pixels) == 320 * 240 * 4 and any(pixels)


def test_themes_colours_and_labels(tmp_path):
    graph = people(tmp_path)
    theme = theme_for("map", {"person": "#ff0000"}, labels=1)
    scene = build_scene(graph, At(), theme=theme)
    assert scene.legend["person"] == (255, 0, 0)
    assert scene.legend["organisation"] in THEMES["map"].palette
    assert theme.labels == 1 and THEMES["map"].labels != 1  # the built-in is untouched
    with pytest.raises(ValueError):
        theme_for("neon")
    with pytest.raises(ValueError):
        theme_for("map", {"person": "red"})

    assert short_name("Darwin Gödel Machine (Zhang et al., 2025)") == "Darwin Godel Machine"
    assert label_texts(scene.nodes, [2, 1, 1], 2) == ["Anna Berg", "Bo Ek", ""]
    assert label_texts(scene.nodes, [0, 0, 0], 3) == ["", "", ""]
    # styled sizes grow with the square root of degree, up to a cap
    map_theme = THEMES["map"]
    assert node_size(map_theme, 0) < node_size(map_theme, 4) < node_size(map_theme, 400) == map_theme.size_max


def test_view_settings_come_from_the_marker(tmp_path):
    graph = people(tmp_path)
    assert store.view_settings(graph.root) == {}
    (tmp_path / "bibliotheke.yaml").write_text("schema_version: 1\nview:\n  theme: classic\n  labels: 3\n")
    assert store.view_settings(graph.root) == {"theme": "classic", "labels": 3}


def test_color_by_edge_groups_nodes_with_their_target(tmp_path):
    graph = Graph.init(tmp_path)
    for type, name in (("word", "lot"), ("word", "run"), ("class", "verb"), ("idiom", "mea culpa")):
        graph.add_node(type, name)
    graph.add_edge("lot", "pos", "verb")
    graph.add_edge("run", "pos", "verb")
    scene = build_scene(graph, At(), color_by="pos")
    by_id = dict(zip((n.id for n in scene.nodes), scene.categories))
    assert by_id == {"lot": "verb", "run": "verb", "verb": "verb", "mea-culpa": "idiom"}
    assert set(scene.legend) == {"verb", "idiom"}


def test_time_span_and_slider_mapping(tmp_path):
    from datetime import datetime, timezone

    from bibliotheke.view import TimeSlider, time_span

    graph = people(tmp_path)
    records = [e for n in graph.nodes.values() for e in n.edges]
    first, last = time_span(records)
    assert (first.year, last.year) == (2010, 2023)  # knows from 2010; works_at through the end of 2022
    assert time_span([f for n in graph.nodes.values() for f in n.facts]) is None  # the field fact is undated

    slider = TimeSlider(first, last)
    assert slider.fraction == 1.0 and slider.when == last
    mid = datetime(2016, 7, 2, tzinfo=timezone.utc)
    assert slider.fraction_of(mid) == pytest.approx(0.5, abs=0.01)
    assert slider.fraction_of(datetime(1990, 1, 1, tzinfo=timezone.utc)) == 0.0


async def test_slider_scrubs_the_graph_in_time(tmp_path):
    from datetime import datetime, timezone

    from bibliotheke.view import TimeSlider

    app = GraphView(people(tmp_path), At(), "type")
    assert app.span is not None
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.2)
        slider = app.query_one("#time", TimeSlider)
        assert app.node_visible == [True, True, True] and app.edge_valid == [True, True]
        # Back to 2015: Bo knows Anna, but Anna does not work at DeepMind yet.
        slider.set_fraction(slider.fraction_of(datetime(2015, 6, 1, tzinfo=timezone.utc)), by_user=True)
        await pilot.pause(0.2)
        assert app.at.valid_at.startswith("2015-06")
        assert app.edge_valid == [False, True]  # (anna→deepmind, bo→anna)
        assert app.node_visible == [True, True, False]  # DeepMind has nothing in view in 2015
        assert "2 nodes, 1 edges" in str(app.query_one("#sb-body").render())
        # Keys step the time; space plays from the start and a key press pauses.
        await pilot.press("full_stop", "full_stop")
        await pilot.pause(0.1)
        assert app.at.valid_at > "2015-06"
        await pilot.press("space")
        await pilot.pause(0.3)
        assert app.playing and slider.fraction > 0.0
        await pilot.press("comma")
        await pilot.pause(0.1)
        assert not app.playing


def test_tree_layout_places_children_apart_and_root_files_on_the_root(tmp_path):
    import math

    from bibliotheke.view import tree_layout

    g = Graph.init(tmp_path)
    g.add_node("file", "README.md")  # no directory above it: a leaf of the root
    for d in ("src", "src/a", "src/a/b", "src/a/b/c", "tests"):
        g.add_node("dir", d + "/", id=d.replace("/", "-"))
    for i in range(12):
        g.add_node("file", f"tests/t{i}.py", id=f"t{i}")
        g.add_edge("tests", "contains", f"t{i}")
    g.add_node("file", "src/a/b/c/deep.py", id="deep")
    g.add_edge("src", "contains", "src-a")
    g.add_edge("src-a", "contains", "src-a-b")
    g.add_edge("src-a-b", "contains", "src-a-b-c")
    g.add_edge("src-a-b-c", "contains", "deep")
    g.add_node("author", "Anna")  # linked to nothing drawn
    scene = build_scene(g, At(), "type", rels=["contains"], tree="contains")
    assert "anna" not in {n.id for n in scene.nodes}, "a node with none of the drawn relations is left out"
    pos = dict(zip((n.id for n in scene.nodes), tree_layout(g, scene, "contains")))
    dist = lambda a, b: math.dist(pos[a], pos[b])  # noqa: E731
    # Every file sits on its directory's shell, at the same distance.
    shell = {round(dist("tests", f"t{i}"), 6) for i in range(12)}
    assert len(shell) == 1
    # The two top-level directories' subtrees do not overlap: the deep chain
    # ends further from `tests` than any test file sits from it.
    assert dist("tests", "deep") > max(dist("tests", f"t{i}") for i in range(12))
    # A chain runs straight out, each link adding its own length: no doubling.
    steps = [dist("src", "src-a"), dist("src-a", "src-a-b"), dist("src-a-b", "src-a-b-c")]
    assert dist("src", "src-a-b-c") == pytest.approx(sum(steps), rel=0.02)
    assert max(steps) < 1.0
    # The root file is near the origin, inside the tree, not on a rim.
    assert math.dist(pos["readme-md"], (0, 0, 0)) < dist("tests", "deep")


async def test_tree_view_is_still(tmp_path):
    g = Graph.init(tmp_path)
    g.add_node("dir", "src/", id="src")
    g.add_node("file", "src/a.py", id="a")
    g.add_edge("src", "contains", "a")
    app = GraphView(g, At(), "type", tree="contains")
    assert app.layout is None and app.energy == 0.0 and len(app.still) == 2
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause(0.2)
        before = app._positions()
        await pilot.pause(0.2)
        assert app._positions() == before


async def test_lenses_switch_the_picture(tmp_path):
    from bibliotheke.view import Lens

    graph = people(tmp_path)
    lenses = [
        Lens("All", "every edge"),
        Lens("Work", "only who works where", rels=["works_at"]),
    ]
    app = GraphView(graph, At(), lenses=lenses)
    assert app.lens.name == "All" and len(app.scene.edges) == 2
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.2)
        assert "All" in app.sub_title
        await pilot.press("2")
        await pilot.pause(0.3)
        assert app.lens.name == "Work" and "Work" in app.sub_title
        assert [e.rel for e in app.scene.edge_records] == ["works_at"]
        assert [n.id for n in app.scene.nodes] == ["anna-berg", "deepmind"], "Bo takes no part in works_at"
        assert app.query_one("#plot").plot is app.plot
        await pilot.press("tab")
        await pilot.pause(0.3)
        assert app.lens.name == "All" and len(app.scene.edges) == 2
        assert len(app.pictures) == 2, "each lens is built once and kept"
