import pytest
import yaml

from bibliotheke import At, Graph, GraphError
from bibliotheke import timeutil

T1 = "2026-10-01T10:00:00Z"
T2 = "2026-10-02T10:00:00Z"
T3 = "2026-10-03T10:00:00Z"


@pytest.fixture
def graph(tmp_path):
    g = Graph.init(tmp_path)
    g.add_node("person", "Anna Berg")
    g.add_node("organisation", "DeepMind")
    return g


def test_canonical_time_forms():
    assert timeutil.canonical(2013) == "2013"
    assert timeutil.canonical("2019-6") == "2019-06"
    assert timeutil.canonical("2026-10-01") == "2026-10-01"
    assert timeutil.canonical("2026-10-04T09:12:00+02:00") == "2026-10-04T07:12:00Z"
    with pytest.raises(ValueError):
        timeutil.canonical("last tuesday")


def test_round_trip_keeps_records_and_readable_times(graph, tmp_path):
    graph.add_node("person", "Zoë Å", notes="line one\nline two\n")
    graph.add_edge("anna-berg", "works_at", "deepmind", valid_from=2019, valid_to="2022-06", known_at="2026-10-01", now=T1)
    graph.add_fact("anna-berg", "field", "ai", now=T1)

    text = (tmp_path / "nodes" / "person" / "anna-berg.yaml").read_text()
    assert "valid_from: 2019\n" in text
    assert "valid_to: 2022-06\n" in text
    assert "known_at: 2026-10-01\n" in text
    assert f"changed_at: {T1}\n" in text
    assert yaml.safe_load(text)["id"] == "anna-berg"

    reloaded = Graph.open(tmp_path)
    assert reloaded.nodes["zoe-a"].notes == "line one\nline two\n"
    assert reloaded.nodes == graph.nodes


def test_valid_time(graph):
    graph.add_edge("anna-berg", "works_at", "deepmind", valid_from=2019, valid_to=2022)
    seen = lambda when: bool(graph.out_edges("anna-berg", At(valid_at=when)))  # noqa: E731
    assert not seen(2018)
    assert seen(2019)
    assert seen("2022-12-31")  # valid_to names a period and includes it
    assert not seen(2023)


def test_knowledge_time(graph):
    graph.add_fact("anna-berg", "field", "ai", known_at="2026-09-15")
    assert not graph.facts("anna-berg", At(known_at="2026-09-14"))
    assert graph.facts("anna-berg", At(known_at="2026-09-15"))
    assert graph.facts("anna-berg", At(known_at=2027))


def test_change_time_update_keeps_history(graph):
    fact = graph.add_fact("anna-berg", "employer", "Google", now=T1)
    graph.update(fact.id, value="DeepMind", now=T2)

    assert [f.value for f in graph.facts("anna-berg")] == ["DeepMind"]
    assert [f.value for f in graph.facts("anna-berg", At(changed_at=T1))] == ["Google"]
    assert [f.value for f in graph.facts("anna-berg", At(changed_at=T2))] == ["DeepMind"]
    assert graph.facts("anna-berg", At(changed_at="2026-09-30")) == []
    assert len(graph.node("anna-berg").facts) == 2


def test_retract_hides_but_does_not_delete(graph):
    edge = graph.add_edge("anna-berg", "works_at", "deepmind", now=T1)
    graph.retract(edge.id, now=T3)
    assert graph.out_edges("anna-berg") == []
    assert len(graph.out_edges("anna-berg", At(changed_at=T2))) == 1
    with pytest.raises(GraphError):
        graph.retract(edge.id)


def test_axes_combine(graph):
    edge = graph.add_edge("anna-berg", "works_at", "deepmind", valid_from=2019, known_at="2026-10-01", now=T1)
    graph.update(edge.id, valid_to=2022, now=T3)
    # As first recorded, the job looked open-ended; after the correction it ended in 2022.
    assert graph.out_edges("anna-berg", At(valid_at=2024, changed_at=T2))
    assert not graph.out_edges("anna-berg", At(valid_at=2024))
    assert graph.out_edges("anna-berg", At(valid_at=2021, known_at="2026-10-01"))
    assert not graph.out_edges("anna-berg", At(valid_at=2021, known_at="2026-09-30"))


def test_list_hides_nodes_with_nothing_in_view(graph):
    graph.add_fact("anna-berg", "field", "ai", valid_from=2015)
    assert {n.id for n in graph.list_nodes()} == {"anna-berg", "deepmind"}
    # DeepMind never had any records, so it is never filtered out.
    assert {n.id for n in graph.list_nodes(At(valid_at=2000))} == {"deepmind"}
    graph.add_edge("anna-berg", "works_at", "deepmind", valid_from=2019)
    assert graph.list_nodes(At(valid_at=2000)) == []
    assert {n.id for n in graph.list_nodes(At(valid_at=2020))} == {"anna-berg", "deepmind"}


def test_search_and_in_edges(graph):
    graph.add_edge("anna-berg", "works_at", "deepmind")
    assert [n.id for n in graph.search("works_at")] == ["anna-berg"]
    assert [n.id for n in graph.search("deepmind")] == ["anna-berg", "deepmind"]
    assert [(n.id, e.rel) for n, e in graph.in_edges("deepmind")] == [("anna-berg", "works_at")]


def test_errors(graph):
    with pytest.raises(GraphError):
        graph.add_node("person", "Anna Berg")
    with pytest.raises(GraphError):
        graph.add_edge("anna-berg", "knows", "nobody")
    with pytest.raises(GraphError):
        graph.add_fact("anna-berg", "born", "x", valid_from="soon")


def test_check_finds_dangling_edge(graph, tmp_path):
    graph.add_edge("anna-berg", "works_at", "deepmind")
    assert graph.check() == []
    (tmp_path / "nodes" / "organisation" / "deepmind.yaml").unlink()
    assert "missing node 'deepmind'" in Graph.open(tmp_path).check()[0]


BATCH = {
    "known_at": "2026-09-20",
    "nodes": [
        {
            "type": "paper",
            "name": "Darwin Gödel Machine",
            "id": "dgm",
            "valid_from": "2025-05",
            "facts": {"year": 2025, "topic": ["rsi", "open-endedness"], "file": "sources/dgm.pdf"},
            "edges": [{"rel": "authored_by", "to": "anna-berg"}],
            "body": "# DGM\\n\\nSelf-modifying coding agents.\n",
        },
        {"type": "concept", "name": "Open-endedness"},
    ],
    "edges": [{"from": "dgm", "rel": "about", "to": "open-endedness", "valid_from": 2025}],
}


def test_apply_merges_a_batch_and_is_idempotent(graph, tmp_path):
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "dgm.pdf").write_bytes(b"%PDF")
    counts = graph.apply(BATCH, now=T1)
    assert counts == {"nodes added": 2, "facts added": 4, "edges added": 2, "bodies added": 1}

    reloaded = Graph.open(tmp_path)
    paper = reloaded.nodes["dgm"]
    assert sorted(f.value for f in paper.facts if f.key == "topic") == ["open-endedness", "rsi"]
    assert {f.valid_from for f in paper.facts} == {"2025-05"}
    assert {f.known_at for f in paper.facts} == {"2026-09-20"}
    assert [(e.rel, e.to, e.valid_from) for e in paper.edges] == [
        ("authored_by", "anna-berg", "2025-05"),
        ("about", "open-endedness", "2025"),
    ]
    assert "Self-modifying" in reloaded.body("dgm")
    assert [n.id for n in reloaded.search("self-modifying")] == ["dgm"]
    assert reloaded.check() == []

    again = reloaded.apply(BATCH, now=T2)
    assert again == {"facts skipped": 4, "edges skipped": 2, "bodies kept": 1}


def test_apply_checks_everything_before_writing(graph, tmp_path):
    bad = {"nodes": [{"type": "paper", "name": "Lonely"}], "edges": [{"from": "lonely", "rel": "cites", "to": "nobody"}]}
    with pytest.raises(GraphError, match="nobody"):
        graph.apply(bad)
    assert "lonely" not in Graph.open(tmp_path).nodes
    with pytest.raises(GraphError, match="not a paper"):
        graph.apply({"nodes": [{"type": "paper", "name": "Anna Berg"}]})


def test_check_flags_missing_files_and_orphan_bodies(graph, tmp_path):
    graph.add_fact("anna-berg", "file", "sources/missing.pdf")
    (tmp_path / "nodes" / "person" / "ghost.md").write_text("boo")
    problems = graph.check()
    assert any("missing.pdf" in p for p in problems)
    assert any("ghost.md" in p for p in problems)
