from bibliotheke import At, Graph
from bibliotheke.geo import build_geo, coordinates, places_of
from bibliotheke.globe import GlobeView, distance_km, label_groups


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


async def test_globe_runs_headless(tmp_path):
    app = GlobeView(world(tmp_path), At())
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        app._show_place(0)
        await pilot.press("escape")
    pixels = app.plot.render_rgba(320, 240)
    assert len(pixels) == 320 * 240 * 4 and any(pixels)
