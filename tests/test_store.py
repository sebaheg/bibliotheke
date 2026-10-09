from pathlib import Path

import yaml

from bibliotheke import Graph, Node, store

PEOPLE = Path(__file__).parent.parent / "examples" / "people"


def _assert_stable(root: Path) -> None:
    """Loading a node file and dumping it again gives the same bytes."""
    for path in sorted((root / store.NODES_DIR).glob("**/*.yaml")):
        text = path.read_text()
        assert store.dump_node(Node.from_dict(yaml.safe_load(text))) == text, path


def test_resave_is_byte_identical(tmp_path):
    g = Graph.init(tmp_path)
    g.add_node("person", "Zoë Å", notes="line one\nline two\n")
    g.add_node("organisation", "DeepMind")
    g.add_fact("zoe-a", "field", "ai", valid_from=2013, now="2026-10-01T10:00:00Z")
    g.add_fact("zoe-a", "h_index", 42, now="2026-10-01T10:00:00Z")
    g.add_fact("zoe-a", "score", 0.5, known_at="2026-09", now="2026-10-01T10:00:00Z")
    g.add_fact("zoe-a", "active", True, now="2026-10-01T10:00:00Z")
    g.add_fact("zoe-a", "quote", "a: colon, and 'quotes'", now="2026-10-01T10:00:00Z")
    edge = g.add_edge("zoe-a", "works_at", "deepmind", valid_from="2019-06", valid_to="2022-06-30", now="2026-10-01T10:00:00Z")
    fact = g.add_fact("zoe-a", "title", "researcher", now="2026-10-01T10:00:00Z")
    g.update(edge.id, valid_to=2023, now="2026-10-02T10:00:00Z")
    g.retract(fact.id, now="2026-10-03T10:00:00Z")

    _assert_stable(tmp_path)
    before = {p: p.read_bytes() for p in (tmp_path / store.NODES_DIR).glob("**/*.yaml")}
    for node in Graph.open(tmp_path).nodes.values():
        store.save_node(tmp_path, node)
    assert {p: p.read_bytes() for p in before} == before


def test_example_graph_is_stable():
    _assert_stable(PEOPLE)
