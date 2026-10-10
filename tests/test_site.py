import json
import subprocess

from typer.testing import CliRunner

from bibliotheke import At, Graph
from bibliotheke import site, timeutil
from bibliotheke.cli import app

runner = CliRunner()


def people(tmp_path):
    graph = Graph.init(tmp_path / "people")
    graph.add_node("person", "Anna Berg", notes="Works on *agents*.")
    graph.add_node("person", "Bo Ek")
    graph.add_node("organisation", "DeepMind")
    graph.add_node("organisation", "Nowhere")  # no records at all
    graph.add_fact("anna-berg", "field", "ai")
    graph.add_fact("anna-berg", "homepage", "https://example.org/anna")
    graph.add_edge("anna-berg", "works_at", "deepmind", valid_from=2019, valid_to=2022)
    graph.add_edge("bo-ek", "knows", "anna-berg", valid_from=2010)
    return graph


def test_site_writes_explorer_and_pages(tmp_path):
    graph = people(tmp_path)
    built = site.build(graph, tmp_path / "out", title="People", base_url="https://example.org/people/")
    out = built.out
    for path in ("index.html", "graph.json", "explorer.js", "site.css", "types.html", ".nojekyll", "t/person.html"):
        assert (out / path).is_file(), path
    assert "<title>People</title>" in (out / "index.html").read_text()
    page = (out / "n" / "anna-berg.html").read_text()
    assert "<h1>Anna Berg</h1>" in page
    assert "<em>agents</em>" in page  # notes are Markdown
    assert '<a href="https://example.org/anna"' in page
    assert '<a href="../n/deepmind.html">DeepMind</a>' in page
    assert '<a href="../n/bo-ek.html">Bo Ek</a>' in page  # an edge in
    assert json.loads((out / "n" / "anna-berg.json").read_text())["id"] == "anna-berg"
    assert "https://example.org/people/n/bo-ek.html" in (out / "sitemap.txt").read_text().splitlines()


def test_explorer_data_lets_the_browser_filter_by_time(tmp_path):
    graph = people(tmp_path)
    data = site.explorer_data(graph, "People")
    nodes = {n["id"]: n for n in data["nodes"]}
    assert "w" not in nodes["nowhere"]  # always in view
    (lens,) = data["lenses"]
    assert len(lens["nodes"]) == 4 and len(lens["edges"]) == 2
    assert all(-1 <= c <= 1 for n in lens["nodes"] for c in n["p"])
    assert data["spans"]["valid"][0] < data["spans"]["valid"][1]

    # The browser's test (explorer.js `nodeInView`) agrees with list_nodes.
    def in_view(node, t):
        return "w" not in node or any((a is None or a <= t) and (b is None or b > t) for a, b, _ in node["w"])

    for year in ("2015", "2020", "2024"):
        t = site._day(timeutil.start(year))
        expected = {n.id for n in graph.list_nodes(At(valid_at=year))}
        assert {i for i, n in nodes.items() if in_view(n, t)} == expected, year


def test_cli_site_and_pages_workflow(tmp_path):
    graph = people(tmp_path)
    subprocess.run(["git", "init", "-q", str(graph.root)], check=True)
    result = runner.invoke(app, ["site", "--out", str(tmp_path / "out"), "--root", str(graph.root)])
    assert result.exit_code == 0, result.output
    assert "4 node pages" in result.output
    result = runner.invoke(app, ["ci", "--pages", "--install", "pip install bib", "--root", str(graph.root)])
    assert result.exit_code == 0, result.output
    assert "actions/deploy-pages" in result.output and "bib site --out" in result.output
    assert "pip install bib\n" in result.output
