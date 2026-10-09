"""Rebuild the example graph in examples/people from scratch.

    uv run python examples/build_people.py

A small graph of public figures (painters, musicians, AI researchers) with
approximately dated relations, to try the CLI, time travel and `bib view` on.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from bibliotheke import Graph

ROOT = Path(__file__).parent / "people"
KNOWN = "2026-10-01"
RECORDED = "2026-10-04T09:00:00Z"
CORRECTED = "2026-10-04T15:30:00Z"

# (name, field, active from, active through)
PEOPLE = [
    ("Claude Monet", "painting", 1860, 1926),
    ("Paul Cézanne", "painting", 1861, 1906),
    ("Vincent van Gogh", "painting", 1880, 1890),
    ("Paul Gauguin", "painting", 1873, 1903),
    ("Henri Matisse", "painting", 1890, 1954),
    ("Pablo Picasso", "painting", 1894, 1973),
    ("Georges Braque", "painting", 1900, 1963),
    ("Salvador Dalí", "painting", 1922, 1989),
    ("Frida Kahlo", "painting", 1925, 1954),
    ("Diego Rivera", "painting", 1906, 1957),
    ("Andy Warhol", "painting", 1949, 1987),
    ("Jean-Michel Basquiat", "painting", 1978, 1988),
    ("Miles Davis", "music", 1944, 1991),
    ("John Coltrane", "music", 1945, 1967),
    ("Bill Evans", "music", 1950, 1980),
    ("Herbie Hancock", "music", 1961, None),
    ("David Bowie", "music", 1962, 2016),
    ("Brian Eno", "music", 1971, None),
    ("John Lennon", "music", 1957, 1980),
    ("Paul McCartney", "music", 1957, None),
    ("Alan Turing", "ai", 1934, 1954),
    ("Claude Shannon", "ai", 1937, 2001),
    ("Geoffrey Hinton", "ai", 1978, None),
    ("Yann LeCun", "ai", 1987, None),
    ("Yoshua Bengio", "ai", 1991, None),
    ("Fei-Fei Li", "ai", 2005, None),
    ("Demis Hassabis", "ai", 2009, None),
    ("Ilya Sutskever", "ai", 2012, None),
    ("Andrej Karpathy", "ai", 2015, None),
]

# (type, name)
OTHERS = [
    ("movement", "Impressionism"),
    ("movement", "Cubism"),
    ("movement", "Surrealism"),
    ("movement", "Pop Art"),
    ("band", "The Beatles"),
    ("organisation", "Bell Labs"),
    ("organisation", "University of Toronto"),
    ("organisation", "Stanford University"),
    ("organisation", "Google"),
    ("organisation", "DeepMind"),
    ("organisation", "Meta"),
    ("organisation", "OpenAI"),
    ("award", "Turing Award"),
]

# (from, relation, to, valid from, valid through)
EDGES = [
    ("claude-monet", "member_of", "impressionism", 1874, 1926),
    ("paul-cezanne", "influenced", "pablo-picasso", 1907, None),
    ("paul-cezanne", "influenced", "henri-matisse", 1899, None),
    ("vincent-van-gogh", "lived_with", "paul-gauguin", "1888-10", "1888-12"),
    ("henri-matisse", "friend_of", "pablo-picasso", 1906, 1954),
    ("pablo-picasso", "member_of", "cubism", 1907, 1919),
    ("georges-braque", "member_of", "cubism", 1908, 1919),
    ("pablo-picasso", "collaborated_with", "georges-braque", 1908, 1914),
    ("salvador-dali", "member_of", "surrealism", 1929, 1939),
    ("frida-kahlo", "married_to", "diego-rivera", 1929, 1939),
    ("frida-kahlo", "married_to", "diego-rivera", 1940, 1954),
    ("andy-warhol", "member_of", "pop-art", 1961, 1987),
    ("jean-michel-basquiat", "collaborated_with", "andy-warhol", 1984, 1985),
    ("david-bowie", "wrote_song_about", "andy-warhol", 1971, 1971),
    ("john-coltrane", "played_with", "miles-davis", 1955, 1960),
    ("bill-evans", "played_with", "miles-davis", 1958, 1959),
    ("herbie-hancock", "played_with", "miles-davis", 1963, 1968),
    ("david-bowie", "collaborated_with", "brian-eno", 1976, 1979),
    ("john-lennon", "member_of", "the-beatles", 1960, 1970),
    ("paul-mccartney", "member_of", "the-beatles", 1960, 1970),
    ("john-lennon", "collaborated_with", "paul-mccartney", 1957, 1970),
    ("alan-turing", "met", "claude-shannon", 1943, 1943),
    ("claude-shannon", "works_at", "bell-labs", 1941, 1956),
    ("turing-award", "named_after", "alan-turing", 1966, None),
    ("geoffrey-hinton", "works_at", "university-of-toronto", 1987, None),
    ("geoffrey-hinton", "works_at", "google", 2013, 2023),
    ("yann-lecun", "postdoc_with", "geoffrey-hinton", 1987, 1988),
    ("yann-lecun", "works_at", "bell-labs", 1988, 1996),
    ("yoshua-bengio", "works_at", "bell-labs", 1992, 1993),
    ("yann-lecun", "works_at", "meta", 2013, 2025),
    ("geoffrey-hinton", "won", "turing-award", 2018, 2018),
    ("yann-lecun", "won", "turing-award", 2018, 2018),
    ("yoshua-bengio", "won", "turing-award", 2018, 2018),
    ("ilya-sutskever", "student_of", "geoffrey-hinton", 2007, 2012),
    ("ilya-sutskever", "works_at", "google", 2013, 2015),
    ("ilya-sutskever", "works_at", "openai", 2015, None),
    ("fei-fei-li", "works_at", "stanford-university", 2009, None),
    ("andrej-karpathy", "student_of", "fei-fei-li", 2011, 2015),
    ("andrej-karpathy", "works_at", "openai", 2015, 2017),
    ("demis-hassabis", "works_at", "deepmind", 2010, None),
    ("google", "acquired", "deepmind", 2014, None),
]


def build() -> Graph:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    graph = Graph.init(ROOT)
    stamps = {"known_at": KNOWN, "now": RECORDED}
    for name, field, start, end in PEOPLE:
        node = graph.add_node("person", name)
        graph.add_fact(node.id, "field", field, valid_from=start, valid_to=end, **stamps)
    for type, name in OTHERS:
        graph.add_node(type, name)
    records = {}
    for src, rel, dst, start, end in EDGES:
        edge = graph.add_edge(src, rel, dst, valid_from=start, valid_to=end, **stamps)
        records[src, rel, dst] = edge.id
    # A correction made later the same day: the first entry left this open-ended.
    graph.update(records["ilya-sutskever", "works_at", "openai"], valid_to=2024, now=CORRECTED)
    return graph


if __name__ == "__main__":
    built = build()
    print(f"{len(built.nodes)} nodes written to {ROOT}")
