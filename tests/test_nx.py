import io

import networkx as nx
import pytest
from networkx.algorithms.link_analysis.pagerank_alg import _pagerank_python

from bibliotheke import At, Graph, GraphError
from bibliotheke import analysis

T1 = "2026-10-01T10:00:00Z"
T2 = "2026-10-02T10:00:00Z"
T3 = "2026-10-03T10:00:00Z"

MODEL = """\
schema_version: 1
nodes:
  word: {}
edges:
  translates_to: {from: word, to: word, symmetric: true}
  derived_from: {from: word, to: word}
"""


@pytest.fixture
def people(tmp_path):
    g = Graph.init(tmp_path)
    for name in ("Anna Berg", "Bo Ek", "Cy Dahl"):
        g.add_node("person", name)
    g.add_node("organisation", "DeepMind")
    g.add_node("organisation", "Bell Labs")
    g.add_fact("anna-berg", "field", "ai", now=T1)
    g.add_fact("anna-berg", "topic", "rsi", now=T1)
    g.add_fact("anna-berg", "topic", "open-endedness", now=T1)
    g.add_edge("anna-berg", "works_at", "deepmind", valid_from=2019, valid_to=2022, now=T1)
    g.add_edge("bo-ek", "knows", "anna-berg", valid_from=2010, now=T1)
    g.add_edge("cy-dahl", "works_at", "bell-labs", valid_from=1950, valid_to=1970, now=T1)
    g.add_edge("cy-dahl", "knows", "bo-ek", now=T1)
    return g


def _edge_versions(graph):
    return sum(len(n.edges) for n in graph.nodes.values())


def test_index_follows_every_write(tmp_path):
    g = Graph.init(tmp_path)
    g.add_node("person", "Anna Berg")
    g.add_node("organisation", "DeepMind")
    edge = g.add_edge("anna-berg", "works_at", "deepmind", valid_from=2019, now=T1)
    fact = g.add_fact("anna-berg", "field", "ai", now=T1)
    assert g._g.number_of_edges() == _edge_versions(g) == 1

    new = g.update(edge.id, valid_to=2022, now=T2)
    assert g._g.number_of_edges() == _edge_versions(g) == 2
    assert g.record(edge.id)[1] is new
    assert [e.valid_to for _, e in g.in_edges("deepmind")] == ["2022"]
    assert [e.valid_to for _, e in g.in_edges("deepmind", At(changed_at=T1))] == [None]
    assert len(g.in_edges("deepmind", at=None)) == 2

    g.retract(new.id, now=T3)
    assert g.in_edges("deepmind") == []
    with pytest.raises(GraphError):
        g.record(edge.id)
    g.update(fact.id, value="ml", now=T3)
    assert g.record(fact.id)[1].value == "ml"

    g.apply({"nodes": [{"type": "person", "name": "Bo Ek", "edges": [{"rel": "knows", "to": "anna-berg"}]}]})
    assert g._g.number_of_edges() == _edge_versions(g) == 3
    assert [o.id for o, _ in g.in_edges("anna-berg")] == ["bo-ek"]

    reloaded = Graph.open(tmp_path)
    assert reloaded._g.number_of_edges() == 3
    assert reloaded.nodes == g.nodes


def test_in_edges_keep_file_order(tmp_path):
    g = Graph.init(tmp_path)
    g.add_node("person", "Zed")
    g.add_node("person", "Amy")
    g.add_node("organisation", "Org")
    g.add_edge("zed", "works_at", "org")
    g.add_edge("amy", "works_at", "org")
    assert [o.id for o, _ in g.in_edges("org")] == ["zed", "amy"]
    assert [o.id for o, _ in Graph.open(tmp_path).in_edges("org")] == ["amy", "zed"]


def test_nx_view_flattens_facts_and_filters(people):
    H = people.nx()
    assert isinstance(H, nx.MultiDiGraph) and nx.is_frozen(H)
    assert H.nodes["anna-berg"] == {"type": "person", "name": "Anna Berg", "field": "ai", "topic": "rsi; open-endedness"}
    assert set(H.edges(keys=True)) == {
        (u, v, k) for u, v, k in ((n.id, e.to, e.id) for n in people.nodes.values() for e in n.edges)
    }
    attrs = H.edges["anna-berg", "deepmind", people.nodes["anna-berg"].edges[0].id]
    assert attrs == {"rel": "works_at", "id": attrs["id"], "valid_from": "2019", "valid_to": "2022", "known_at": T1, "changed_at": T1}

    old = people.nx(At(valid_at=1960))
    # Anna's facts are undated, so she stays in view; her edges do not.
    assert set(old) == {"cy-dahl", "bell-labs", "bo-ek", "anna-berg"}
    assert [a["rel"] for _, _, a in old.edges(data=True)] == ["works_at", "knows"]

    assert set(people.nx(rels=["knows"]).edges()) == {("bo-ek", "anna-berg"), ("cy-dahl", "bo-ek")}
    assert set(people.nx(types=["person"])) == {"anna-berg", "bo-ek", "cy-dahl"}
    assert people.nx(types=["person"]).number_of_edges() == 2

    with pytest.raises(nx.NetworkXError):
        H.add_node("x")
    nx.write_graphml(H, io.BytesIO())


def test_nx_mirrors_symmetric_edges(tmp_path):
    (tmp_path / "bibliotheke.yaml").write_text(MODEL)
    (tmp_path / "nodes").mkdir()
    g = Graph.open(tmp_path)
    g.add_node("word", "adamant", id="adamant-en")
    g.add_node("word", "envis", id="envis-sv")
    g.add_node("word", "adamas", id="adamas-la")
    edge = g.add_edge("adamant-en", "translates_to", "envis-sv")
    g.add_edge("adamant-en", "derived_from", "adamas-la")
    assert g.in_edges("adamant-en") == []

    H = g.nx()
    assert H.has_edge("envis-sv", "adamant-en", key=edge.id)
    assert H.edges["envis-sv", "adamant-en", edge.id]["mirror"] is True
    assert "mirror" not in H.edges["adamant-en", "envis-sv", edge.id]
    assert H.number_of_edges() == 3
    assert H.to_undirected(as_view=True).number_of_edges() == 2

    assert [s.forward for s in analysis.shortest_path(g, "envis-sv", "adamant-en")] == [True]
    assert [s.rel for s in analysis.shortest_path(g, "envis-sv", "adamas-la")] == ["translates_to", "derived_from"]


def test_pagerank_matches_networkx(people):
    H = people.nx()
    ours = analysis.pagerank(H)
    assert ours == pytest.approx(_pagerank_python(H), abs=1e-6)
    U = H.to_undirected(as_view=True)
    assert analysis.pagerank(U) == pytest.approx(_pagerank_python(U), abs=1e-6)
    assert analysis.pagerank(nx.MultiDiGraph()) == {}


def test_shortest_path(people):
    steps = analysis.shortest_path(people, "deepmind", "bell-labs")
    assert [(s.src, s.rel, s.dst, s.forward) for s in steps] == [
        ("deepmind", "works_at", "anna-berg", False),
        ("anna-berg", "knows", "bo-ek", False),
        ("bo-ek", "knows", "cy-dahl", False),
        ("cy-dahl", "works_at", "bell-labs", True),
    ]
    assert analysis.shortest_path(people, "cy-dahl", "bell-labs", directed=True)[0].forward
    with pytest.raises(GraphError, match="no path"):
        analysis.shortest_path(people, "deepmind", "bell-labs", directed=True)
    with pytest.raises(GraphError, match="no path"):
        analysis.shortest_path(people, "deepmind", "bell-labs", rels=["works_at"])
    with pytest.raises(GraphError, match="not in view"):
        analysis.shortest_path(people, "deepmind", "bell-labs", at=At(valid_at=1960))
    with pytest.raises(GraphError, match="no node"):
        analysis.shortest_path(people, "deepmind", "nobody")


def test_rank_and_communities(people):
    by_degree = analysis.rank(people, "degree")
    assert [(n.id, s) for n, s in by_degree[:2]] == [("anna-berg", 2.0), ("bo-ek", 2.0)]
    assert [n.id for n, _ in analysis.rank(people, "degree", types=["person"], top=1)] == ["bo-ek"]
    assert analysis.rank(people, "betweenness")[0][0].id == "bo-ek"
    assert analysis.rank(people, "pagerank", directed=True)[0][0].id == "deepmind"
    assert [n.id for n, _ in analysis.rank(people, "degree", at=At(valid_at=1960))] == ["cy-dahl", "bell-labs", "bo-ek", "anna-berg"]
    with pytest.raises(GraphError, match="--by"):
        analysis.rank(people, "fame")

    groups = analysis.communities(people)
    assert sorted(len(g) for g in groups) == [2, 3]
    assert {n.id for n in groups[0]} == {"anna-berg", "bo-ek", "deepmind"} or {n.id for n in groups[0]} == {"anna-berg", "bo-ek", "cy-dahl"}
    assert analysis.communities(people, min_size=3) == groups[:1]
