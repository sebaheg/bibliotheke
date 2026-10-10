import pytest

from bibliotheke import At, Graph, lenses
from bibliotheke.geo import build_geo, coordinates, infer, places_of
from bibliotheke.globe import MAX_RISE, GlobePicture, arc_height, distance_km, label_groups
from bibliotheke.lenses import LensError
from bibliotheke.view import GraphView


def world(tmp_path):
    g = Graph.init(tmp_path)
    batch = {
        "nodes": [
            {"type": "city", "name": "London", "facts": {"lat": 51.5074, "lon": -0.1278}},
            {"type": "city", "name": "Oxford", "facts": {"lat": 51.752, "lon": -1.2577}},
            {"type": "city", "name": "Tokyo", "facts": {"lat": 35.6762, "lon": 139.6503}},
            {"type": "organisation", "name": "Lab", "edges": [{"rel": "located_in", "to": "london"}]},
            {"type": "organisation", "name": "Nowhere Inc"},
            {"type": "person", "name": "Ada", "edges": [{"rel": "affiliated_with", "to": "lab"}]},
            # an explicit home city wins over the affiliation's
            {
                "type": "person",
                "name": "Bo",
                "edges": [{"rel": "affiliated_with", "to": "lab"}, {"rel": "based_in", "to": "tokyo"}],
            },
            {"type": "person", "name": "Cy", "edges": [{"rel": "affiliated_with", "to": "nowhere-inc"}]},
            {
                "type": "person",
                "name": "Di",
                "edges": [{"rel": "based_in", "to": "oxford", "valid_from": 2025}],
            },
            {
                "type": "paper",
                "name": "Paper",
                "edges": [{"rel": "authored_by", "to": "ada"}, {"rel": "authored_by", "to": "bo"}],
            },
        ]
    }
    g.apply(batch)
    return g


def test_places_follow_edges_and_prefer_based_in(tmp_path):
    g = world(tmp_path)
    assert coordinates(g, "london") == (51.5074, -0.1278)
    assert coordinates(g, "lab") is None
    assert places_of(g, "ada") == ["london"]
    assert places_of(g, "bo") == ["tokyo"]
    assert places_of(g, "cy") == []

    scene = build_geo(g)
    names = {p.node.id: p.members for p in scene.places}
    assert names == {"london": ["ada"], "oxford": ["di"], "tokyo": ["bo"]}
    assert scene.unplaced == ["cy"]
    # the paper links its authors' cities; the shared lab does not count
    linked = {(scene.places[a].node.id, scene.places[b].node.id): via for (a, b), via in scene.links.items()}
    assert linked == {("london", "tokyo"): ["paper"]}
    # time filters apply: Di's home city is from 2025
    assert "oxford" not in {p.node.id for p in build_geo(g, At(valid_at=2024)).places}


def test_nearby_labels_share_one(tmp_path):
    scene = build_geo(world(tmp_path))
    assert 70 < distance_km((51.5074, -0.1278), (51.752, -1.2577)) < 90
    groups = [[scene.places[i].node.id for i in group] for group in label_groups(scene.places)]
    assert sorted(groups) == [["london", "oxford"], ["tokyo"]]


def test_inference_reads_what_to_place_from_the_graph(tmp_path):
    g = world(tmp_path)
    who, via = infer(g)
    assert who == ("organisation", "paper", "person"), "everything that is somewhere"
    assert via[:2] == ("based_in", "located_in") and set(via) == {"located_in", "affiliated_with", "based_in", "authored_by"}
    assert infer(Graph.init(tmp_path / "empty")) == ((), ())


def test_arcs_along_relations_or_none(tmp_path):
    g = world(tmp_path)
    g.add_edge("ada", "knows", "bo")
    scene = build_geo(g, arcs=("knows",))
    linked = {(scene.places[a].node.id, scene.places[b].node.id) for a, b in scene.links}
    assert linked == {("london", "tokyo")}
    assert build_geo(g, arcs="none").links == {}
    hidden = build_geo(g, hide=("paper",))
    assert hidden.links == {}, "a hidden type links nothing"


def test_a_graph_with_coordinates_gets_a_globe_lens(tmp_path):
    g = world(tmp_path)
    specs = lenses.declared(g)
    assert [s.name for s in specs] == ["Graph", "Globe"] and specs[1].globe and specs[1].layout == "globe"
    found = lenses.lenses(g, lens="globe")
    assert found[0].name == "Globe" and found[0].globe
    assert lenses.lenses(g, lens="glo")[0].globe
    no_coordinates = Graph.init(tmp_path / "plain")
    no_coordinates.add_node("person", "Ada")
    with pytest.raises(LensError, match="no globe: no node has lat and lon"):
        lenses.lenses(no_coordinates, lens="globe")


def test_a_declared_globe_lens(tmp_path):
    g = world(tmp_path)
    (tmp_path / "bibliotheke.yaml").write_text(
        "schema_version: 1\nview:\n  lenses:\n    - name: Graph\n    - name: Where\n      layout: globe\n"
        "      place: [person]\n      via: [based_in, affiliated_with, located_in]\n      arcs: none\n"
        "      style: {globe: earth}\n"
    )
    g = Graph.open(tmp_path)
    specs = lenses.declared(g)
    assert [s.name for s in specs] == ["Graph", "Where"], "a declared globe lens: no automatic one"
    where = specs[1]
    assert where.place == ("person",) and where.via[0] == "based_in" and where.arcs == "none"
    lens = lenses.lenses(g, lens="globe")[0]
    assert lens.name == "Where" and lens.theme.globe == "earth"
    picture = GlobePicture(g, At(), lens)
    assert sorted(p.node.id for p in picture.places) == ["london", "oxford", "tokyo"]
    assert picture.links == {} and picture.scene.legend == {"person": picture.scene.legend["person"]}
    assert [n.id for n in picture.scene.nodes] == [p.node.id for p in picture.places]
    assert "Ada" in "\n".join(picture.place_lines([p.node.id for p in picture.places].index("london"), At()))


async def test_globe_runs_headless(tmp_path):
    g = world(tmp_path)
    app = GraphView(g, At(), lenses=lenses.lenses(g, lens="globe"))
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        assert app.picture.globe and app.handle is None
        await pilot.press("escape")
        await pilot.press("j")  # to the graph lens and back: a globe sits in the rotation
        await pilot.pause(0.3)
        assert not getattr(app.picture, "globe", False)
        await pilot.press("j")
        await pilot.pause(0.3)
        assert app.picture.globe
    pixels = app.plot.render_rgba(320, 240)
    assert len(pixels) == 320 * 240 * 4 and any(pixels)


def test_arcs_rise_with_their_length_up_to_a_cap():
    london, oxford, tokyo = (51.5, -0.13), (51.75, -1.26), (35.68, 139.65)
    assert arc_height(london, oxford) < 0.002, "neighbours hug the surface"
    assert arc_height(london, oxford) < arc_height(london, tokyo) <= MAX_RISE
    assert arc_height(london, (-51.5, 179.87)) == MAX_RISE


def test_a_place_lights_its_own_arcs(tmp_path):
    g = world(tmp_path)
    (tmp_path / "bibliotheke.yaml").write_text(
        "schema_version: 1\nview:\n  lenses:\n    - name: Where\n      layout: globe\n"
        "      place: [person]\n      via: [based_in, affiliated_with, located_in]\n"
    )
    g = Graph.open(tmp_path)
    picture = GlobePicture(g, At(), lenses.lenses(g)[0])
    names = [p.node.id for p in picture.places]
    assert picture.links, "the paper joins London and Tokyo"
    rest = picture.plot.render_rgba(320, 240)
    assert picture.light(names.index("london")) and not picture.light(names.index("london"))
    assert picture.plot.render_rgba(320, 240) != rest
    assert picture.light(None)
    assert picture.plot.render_rgba(320, 240) == rest
