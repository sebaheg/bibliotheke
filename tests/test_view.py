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
