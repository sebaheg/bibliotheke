import pytest
import yaml

from bibliotheke import At, Graph, lenses
from bibliotheke.lenses import LensError, Spec
from bibliotheke.view import GraphView, Picture, label_texts

MARKER = {
    "schema_version": 1,
    "nodes": {
        "paper": {"facts": {"year": "int", "kind": "text"}},
        "concept": {},
        "claim": {"facts": {"kind": "text"}},
        "city": {},
    },
    "edges": {
        "cites": {"from": "paper", "to": "paper"},
        "about": {"from": ["paper", "claim"], "to": "concept"},
        "made_in": {"from": "claim", "to": "paper"},
    },
}


def literature(tmp_path, view=None):
    """Two papers, a concept, a claim and a city no edge reaches, in a
    fresh directory each time."""
    marker = {**MARKER, **({"view": view} if view else {})}
    tmp_path = tmp_path / str(len(list(tmp_path.iterdir())))
    tmp_path.mkdir()
    (tmp_path / "bibliotheke.yaml").write_text(yaml.safe_dump(marker, sort_keys=False))
    graph = Graph.open(tmp_path)
    graph.add_node("paper", "Alpha")
    graph.add_node("paper", "Beta")
    graph.add_node("concept", "Search")
    graph.add_node("claim", "Alpha says")
    graph.add_node("city", "Lund")
    graph.add_fact("alpha", "year", 2024)
    graph.add_fact("beta", "year", 2025)
    graph.add_fact("alpha-says", "kind", "result")
    graph.add_edge("beta", "cites", "alpha")
    graph.add_edge("alpha", "about", "search")
    graph.add_edge("alpha-says", "made_in", "alpha")
    graph.add_edge("alpha-says", "about", "search")
    return graph


VIEW = {
    "theme": "map",
    "labels": 4,
    "colors": {"paper": "#f0f0f4"},
    "hide": ["city"],
    "lenses": [
        {
            "name": "Literature",
            "description": "papers and concepts",
            "rels": ["cites", "about"],
            "hide": ["claim", "city"],
            "size_by": "in:cites",
            "labels": {"count": 1, "types": ["paper"]},
            "legend": ["paper", "concept"],
            "edges": {"cites": "#c8ccd8"},
        },
        {"name": "Claims", "show": ["claim", "paper"], "rels": ["made_in"], "tree": "made_in", "color_by": "kind", "legend": False},
        {"name": "Everything", "hide_unlinked": True},
    ],
}


def test_a_graph_without_lenses_gets_one_from_the_view_keys(tmp_path):
    graph = literature(tmp_path)
    assert lenses.declared(graph) == [Spec("Graph")]
    graph = literature(tmp_path, {"color_by": "kind", "tree": "made_in", "hide": "city"})
    (spec,) = lenses.declared(graph)
    assert (spec.color_by, spec.tree, spec.hide, spec.layout) == ("kind", "made_in", ("city",), "tree")


def test_lenses_inherit_the_view_level_and_override_it(tmp_path):
    graph = literature(tmp_path, VIEW)
    found = lenses.declared(graph)
    assert [s.name for s in found] == ["Literature", "Claims", "Everything"]
    lit, claims, everything = found
    assert lit.hide == ("city", "claim") and lit.labels == 1 and lit.label_types == ("paper",)
    assert lit.colors == {"paper": "#f0f0f4"} and lit.edges == {"cites": "#c8ccd8"} and lit.legend == ("paper", "concept")
    assert claims.hide == ("city", "concept"), "show is the whole statement: the inherited hide does not add to it"
    assert claims.legend == () and claims.tree == "made_in" and claims.labels == 4
    assert everything.hide == ("city",) and everything.hide_unlinked and everything.rels is None


def test_lenses_are_checked_against_the_model(tmp_path):
    bad = [
        ({"hide": ["citty"]}, "no node type 'citty'"),
        ({"lenses": [{"name": "x", "rels": ["quotes"]}]}, "view.lenses[x].rels: no relation 'quotes'"),
        ({"lenses": [{"name": "x", "tree": "year"}]}, "tree: no relation"),
        ({"lenses": [{"name": "x", "layout": "tree"}]}, "needs a `tree:`"),
        ({"size_by": "in:quotes"}, "no relation 'quotes'"),
        ({"size_by": "pages"}, "not degree, in:<relation>"),
        ({"color_by": "pages"}, "not 'type', a fact key, a relation"),
        ({"labels": {"count": 2, "kinds": []}}, "keys are count and types"),
        ({"edges": {"about": "blue"}}, "not a colour"),
        ({"min_parallel": 0}, "1 or more"),
        ({"style": {"glow": 1}}, "style: a mapping of"),
        ({"lenses": []}, "a list of lenses"),
        ({"lenses": [{"name": "a"}, {"name": "A"}]}, "two lenses are named a"),
        ({"lenses": [{"name": "a", "colour_by": "kind"}]}, "unknown key 'colour_by'"),
        ({"nope": 1}, "view: unknown key 'nope'"),
    ]
    for view, message in bad:
        graph = literature(tmp_path, view)
        with pytest.raises(LensError, match=message.replace("[", r"\[").replace("]", r"\]").replace("(", r"\(").replace(")", r"\)")):
            lenses.declared(graph)
        assert lenses.problems(graph) and lenses.problems(graph)[0].startswith("bibliotheke.yaml: ")
        assert any("bibliotheke.yaml" in problem for problem in graph.check())
    assert lenses.problems(literature(tmp_path, VIEW)) == []


def test_without_a_model_the_graph_itself_says_what_exists(tmp_path):
    graph = Graph.init(tmp_path)
    graph.add_node("word", "lot")
    graph.add_node("word", "run")
    graph.add_edge("lot", "rhymes_with", "run")
    (tmp_path / "bibliotheke.yaml").write_text("schema_version: 1\nview:\n  rels: [rhymes_with]\n  hide: [idiom]\n")
    with pytest.raises(LensError, match="no node type 'idiom'; the graph has word"):
        lenses.declared(Graph.open(tmp_path))


def test_built_lenses_size_label_and_legend_as_declared(tmp_path):
    graph = literature(tmp_path, VIEW)
    lit, claims, everything = lenses.build(graph, lenses.declared(graph))
    assert lit.rels == ["cites", "about"] and lit.hide == ["city", "claim"] and lit.legend == ["paper", "concept"]
    assert lit.theme.edge_colors == {"cites": (200, 204, 216)} and lit.theme.overrides["paper"] == (240, 240, 244)
    assert lit.theme.labels == 1 and lit.weight(graph.node("alpha")) == 1.0 and lit.weight(graph.node("beta")) == 0.0
    assert lit.label(graph.node("alpha")) == "Alpha" and lit.label(graph.node("search")) == ""
    picture = Picture(graph, At(), lit)
    assert [n.id for n in picture.scene.nodes] == ["alpha", "beta", "search"]
    assert picture.legend_rows == ["concept", "paper"], "listed rows only, in the scene's order"
    assert label_texts(picture.scene.nodes, picture.scene.weights, 1, picture.scene.labels) == ["Alpha", "", ""]

    picture = Picture(graph, At(), claims)
    assert picture.legend_rows == [] and claims.tree == "made_in"
    assert {n.type for n in picture.scene.nodes} == {"claim", "paper"}
    assert [n.id for n in picture.scene.nodes if n.type == "paper"] == ["alpha", "beta"], "beta: its type takes part in the tree"

    picture = Picture(graph, At(), everything)
    assert "lund" not in {n.id for n in picture.scene.nodes}, "nothing links to the city"
    assert picture.legend_rows == ["claim", "concept", "paper"]


def test_command_line_makes_a_custom_lens_and_picks_one(tmp_path):
    graph = literature(tmp_path, VIEW)
    found = lenses.lenses(graph, lens="cla")
    assert [lens.name for lens in found] == ["Claims", "Everything", "Literature"]
    with pytest.raises(LensError, match="no lens .x.; the lenses are literature, claims, everything"):
        lenses.lenses(graph, lens="x")
    (custom,) = lenses.lenses(graph, rels=["about"], hide=["claim"])
    assert custom.name == "Custom" and custom.rels == ["about"] and custom.hide == ["claim"] and custom.tree is None
    (custom,) = lenses.lenses(graph, tree="made_in", theme="classic", color_by="year")
    assert custom.tree == "made_in" and custom.rels == ["cites", "about"] and not custom.theme.styled and custom.color_by == "year"
    app = GraphView(graph, At(), lenses=lenses.lenses(graph))
    assert app.lens.name == "Literature" and len(app.lenses) == 3


def test_valid_at_default(tmp_path):
    assert lenses.valid_at(literature(tmp_path)) is None
    assert lenses.valid_at(literature(tmp_path, {"valid_at": 2024})) == "2024"
    assert lenses.valid_at(literature(tmp_path, {"valid_at": "now"})).startswith("20")
    with pytest.raises(LensError, match="view.valid_at"):
        lenses.valid_at(literature(tmp_path, {"valid_at": "yesterday"}))


def test_rewrite_follows_renames_and_drops():
    view = {
        "color_by": "kind",
        "size_by": "in:cites",
        "hide": ["city"],
        "edges": {"cites": "#c8ccd8"},
        "lenses": [{"name": "a", "rels": ["cites", "about"], "tree": "cites", "labels": {"count": 1, "types": ["paper"]}}],
    }
    renamed = lenses.rewrite(view, "rel", "cites", "references")
    assert renamed["size_by"] == "in:references" and renamed["edges"] == {"references": "#c8ccd8"}
    assert renamed["lenses"][0]["rels"] == ["references", "about"] and renamed["lenses"][0]["tree"] == "references"
    dropped = lenses.rewrite(view, "rel", "cites", None)
    assert "size_by" not in dropped and dropped["edges"] == {}
    assert dropped["lenses"][0] == {"name": "a", "rels": ["about"], "labels": {"count": 1, "types": ["paper"]}}
    assert lenses.rewrite(view, "fact", "kind", "sort")["color_by"] == "sort"
    assert "color_by" not in lenses.rewrite(view, "fact", "kind", None)
    typed = lenses.rewrite(view, "type", "paper", "article")
    assert typed["lenses"][0]["labels"]["types"] == ["article"] and typed["hide"] == ["city"]
    assert lenses.rewrite(view, "type", "city", None)["hide"] == []
    assert view["lenses"][0]["rels"] == ["cites", "about"], "the original is untouched"
