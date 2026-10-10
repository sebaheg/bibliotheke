import json

import pytest
import yaml
from typer.testing import CliRunner

from bibliotheke import fetch as fetcher
from bibliotheke.cli import app
from bibliotheke.fetch import Fetched, FetchError, Options, arxiv, github, html, web, youtube
from bibliotheke.graph import Graph

runner = CliRunner()


def run(root, *args):
    result = runner.invoke(app, [*args, "--root", str(root)])
    if result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    assert result.exit_code == 0, result.output
    return result.output.strip()


# -- URL matching --------------------------------------------------------------


@pytest.mark.parametrize(
    "url, kind, key",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=5s", "youtube", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "youtube", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/live/dQw4w9WgXcQ?feature=share", "youtube", "dQw4w9WgXcQ"),
        ("https://arxiv.org/abs/2505.22954v2", "arxiv", "2505.22954"),
        ("https://arxiv.org/pdf/2505.22954", "arxiv", "2505.22954"),
        ("arxiv:2505.22954", "arxiv", "2505.22954"),
        ("https://arxiv.org/abs/hep-th/9901001", "arxiv", "hep-th/9901001"),
        ("https://github.com/jennyzzt/dgm", "github", "jennyzzt/dgm"),
        ("https://github.com/jennyzzt/dgm.git", "github", "jennyzzt/dgm"),
        ("https://github.com/jennyzzt/dgm/tree/main/src", "github", "jennyzzt/dgm"),
        ("https://some.blog/post?x=1#section", "web", "https://some.blog/post?x=1"),
    ],
)
def test_handler_for(url, kind, key):
    handler, found = fetcher.handler_for(url)
    assert (handler.kind, found) == (kind, key)


def test_no_handler():
    with pytest.raises(FetchError):
        fetcher.handler_for("not a url")
    assert github.key("https://github.com/orgs/foo/bar") is None


def test_claimed_leaves_web_pages_out():
    urls = ["https://x.org/a", "https://arxiv.org/abs/2505.22954", "https://github.com/a/b", "https://arxiv.org/abs/2505.22954"]
    assert fetcher.claimed(urls) == ["https://arxiv.org/abs/2505.22954", "https://github.com/a/b"]


# -- YouTube -------------------------------------------------------------------

JSON3 = {
    "events": [
        {"tStartMs": 0, "dDurationMs": 2000, "segs": [{"utf8": "Welcome"}, {"utf8": " to", "tOffsetMs": 500}, {"utf8": " the show."}]},
        {"tStartMs": 2000, "aAppend": 1, "segs": [{"utf8": "\n"}]},
        {"tStartMs": 2000, "segs": [{"utf8": "Today we talk\nabout graphs."}]},
        {"tStartMs": 50000, "segs": [{"utf8": "Second paragraph starts here."}]},
        {"tStartMs": 130000, "segs": [{"utf8": "Chapter two begins."}]},
    ]
}

META = {
    "id": "abc123DEF45",
    "title": "Graphs All The Way Down",
    "channel": "Knowledge Pod",
    "upload_date": "20250601",
    "duration": 3600,
    "view_count": 1200,
    "description": "A talk.\nPaper: https://arxiv.org/abs/2505.22954 Code: https://github.com/jennyzzt/dgm",
    "chapters": [{"start_time": 0, "end_time": 120, "title": "Intro"}, {"start_time": 120, "end_time": 3600, "title": "The Meat"}],
    "subtitles": {},
    "automatic_captions": {"en-orig": [{"ext": "vtt", "url": "u1"}, {"ext": "json3", "url": "https://yt/json3"}]},
}


def test_youtube_parse_and_transcript(monkeypatch):
    monkeypatch.setattr(youtube, "info", lambda video_id: META)
    monkeypatch.setattr(youtube.http, "get_text", lambda url, headers=None: json.dumps(JSON3))
    fetched = youtube.fetch("abc123DEF45", Options())
    assert fetched.node["name"] == "Graphs All The Way Down" and fetched.node["id"] == "graphs-all-the-way-down"
    assert fetched.node["facts"]["youtube_id"] == "abc123DEF45"
    assert fetched.node["facts"]["duration"] == 3600 and fetched.node["valid_from"] == "2025-06-01"
    assert fetched.identity == ("youtube_id", "abc123DEF45")
    assert fetched.links == ["https://arxiv.org/abs/2505.22954", "https://github.com/jennyzzt/dgm"]
    text = fetched.files["sources/youtube/abc123DEF45.md"]
    assert "- Transcript: automatic captions" in text
    assert "## Intro" in text and "## The Meat" in text
    assert "[0:00](https://youtu.be/abc123DEF45?t=0) Welcome to the show. Today we talk about graphs." in text
    assert "[0:50](https://youtu.be/abc123DEF45?t=50) Second paragraph starts here." in text
    assert text.index("## The Meat") < text.index("[2:10](https://youtu.be/abc123DEF45?t=130) Chapter two begins.")
    assert "## Description" in fetched.node["body"]


def test_youtube_prefers_uploaded_captions_and_language():
    meta = {
        "subtitles": {"sv": [{"ext": "json3", "url": "sv"}], "en": [{"ext": "json3", "url": "en-up"}]},
        "automatic_captions": {"en": [{"ext": "json3", "url": "en-auto"}]},
    }
    assert youtube.caption_track(meta, "en") == ("en-up", "captions")
    assert youtube.caption_track(meta, "sv") == ("sv", "captions")
    assert youtube.caption_track({"automatic_captions": {"de-DE": [{"ext": "json3", "url": "de"}]}}, "de") == ("de", "automatic captions")
    assert youtube.caption_track(meta, "fr") is None


def test_youtube_without_captions(monkeypatch, tmp_path):
    monkeypatch.setattr(youtube, "info", lambda video_id: {**META, "automatic_captions": {}})
    with pytest.raises(FetchError, match="no en captions"):
        youtube.fetch("abc123DEF45", Options(transcribe="captions"))
    monkeypatch.setattr(youtube, "whisper", lambda video_id, cache: [youtube.Cue(0, "Transcribed locally.")])
    fetched = youtube.fetch("abc123DEF45", Options(cache=tmp_path))
    assert "- Transcript: whisper" in fetched.files["sources/youtube/abc123DEF45.md"]


def test_stamp():
    assert youtube.stamp(5) == "0:05" and youtube.stamp(750) == "12:30" and youtube.stamp(4350) == "1:12:30"


# -- arXiv ---------------------------------------------------------------------

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2505.22954v1</id>
    <updated>2025-05-29T17:59:02Z</updated>
    <published>2025-05-29T17:59:02Z</published>
    <title>Darwin Gödel Machine: Open-Ended Evolution of
  Self-Improving Agents</title>
    <summary>Today's AI systems have human-designed, fixed architectures. Code: https://github.com/jennyzzt/dgm</summary>
    <author><name>Jenny Zhang</name></author>
    <author><name>Jeff Clune</name></author>
    <arxiv:comment xmlns:arxiv="http://arxiv.org/schemas/atom">Project page at example.org</arxiv:comment>
    <arxiv:primary_category xmlns:arxiv="http://arxiv.org/schemas/atom" term="cs.AI"/>
    <category term="cs.AI"/>
    <category term="cs.LG"/>
  </entry>
</feed>"""

ARXIV_HTML = """<html><body><article class="ltx_document"><h1>Darwin Gödel Machine</h1><p>We present <math alttext="x_t"><mi>x</mi></math> things.</p></article></body></html>"""


def test_arxiv(monkeypatch):
    def get_text(url, headers=None):
        if url.startswith(arxiv.API):
            return ATOM
        if url == "https://arxiv.org/html/2505.22954":
            return ARXIV_HTML
        raise AssertionError(url)

    monkeypatch.setattr(arxiv.http, "get_text", get_text)
    monkeypatch.setattr(arxiv.http, "get", lambda url, headers=None: b"%PDF-1.4 fake")
    fetched = arxiv.fetch("2505.22954", Options())
    assert fetched.node["name"] == "Darwin Gödel Machine: Open-Ended Evolution of Self-Improving Agents"
    assert fetched.node["id"] == "darwin-godel-machine"
    assert fetched.node["facts"]["category"] == ["cs.AI", "cs.LG"]
    assert fetched.node["facts"]["published"] == "2025-05-29"
    assert fetched.node["edges"] == [{"rel": "authored_by", "to": "Jenny Zhang"}, {"rel": "authored_by", "to": "Jeff Clune"}]
    assert [n["name"] for n in fetched.neighbours] == ["Jenny Zhang", "Jeff Clune"]
    assert fetched.files["sources/arxiv/2505.22954.pdf"] == b"%PDF-1.4 fake"
    md = fetched.files["sources/arxiv/2505.22954.md"]
    assert "- Text: arXiv HTML" in md and "$x_t$" in md
    assert fetched.links == ["https://github.com/jennyzzt/dgm"]
    assert "## Abstract" in fetched.node["body"]


def test_arxiv_without_html(monkeypatch):
    def get_text(url, headers=None):
        if url.startswith(arxiv.API):
            return ATOM
        raise FetchError(f"{url}: HTTP 404")

    monkeypatch.setattr(arxiv.http, "get_text", get_text)
    monkeypatch.setattr(arxiv.http, "get", lambda url, headers=None: b"%PDF")
    monkeypatch.setattr(arxiv, "pdf_text", lambda pdf: None)
    fetched = arxiv.fetch("2505.22954", Options())
    assert "sources/arxiv/2505.22954.md" not in fetched.files and fetched.node["facts"]["file"] is None


def test_arxiv_missing(monkeypatch):
    monkeypatch.setattr(arxiv.http, "get_text", lambda url, headers=None: '<feed xmlns="http://www.w3.org/2005/Atom"></feed>')
    with pytest.raises(FetchError, match="no paper"):
        arxiv.fetch("9999.99999", Options())


# -- GitHub ------------------------------------------------------------------

REPO = {
    "full_name": "jennyzzt/dgm",
    "html_url": "https://github.com/jennyzzt/dgm",
    "description": "Darwin Gödel Machine",
    "homepage": "https://arxiv.org/abs/2505.22954",
    "language": "Python",
    "stargazers_count": 1500,
    "forks_count": 200,
    "license": {"spdx_id": "Apache-2.0"},
    "topics": ["rsi", "agents"],
    "created_at": "2025-05-20T10:00:00Z",
    "pushed_at": "2025-09-01T10:00:00Z",
    "default_branch": "main",
    "owner": {"login": "jennyzzt", "type": "User"},
}
TREE = {"tree": [{"path": "README.md", "type": "blob", "size": 100}, {"path": "dgm", "type": "tree"}, {"path": "dgm/agent.py", "type": "blob", "size": 20000}], "truncated": False}


def _github(monkeypatch):
    def get_json(url, headers=None):
        if url.endswith("/repos/jennyzzt/dgm"):
            return REPO
        if "/git/trees/" in url:
            return TREE
        if url.endswith("/users/jennyzzt"):
            return {"name": "Jenny Zhang"}
        raise AssertionError(url)

    monkeypatch.setattr(github.http, "get_json", get_json)
    monkeypatch.setattr(github.http, "get_text", lambda url, headers=None: "# DGM\n\nSee https://arxiv.org/abs/2505.22954.")


def test_github(monkeypatch):
    _github(monkeypatch)
    fetched = github.fetch("jennyzzt/dgm", Options())
    facts = fetched.node["facts"]
    assert fetched.node["name"] == "jennyzzt/dgm" and facts["github"] == "jennyzzt/dgm"
    assert facts["stars"]["value"] == 1500 and facts["license"] == "Apache-2.0" and facts["topics"] == ["rsi", "agents"]
    assert fetched.node["valid_from"] == "2025-05-20"
    assert fetched.neighbours == [{"type": "person", "name": "Jenny Zhang", "facts": {"github": "jennyzzt"}}]
    assert fetched.node["edges"] == [{"rel": "owned_by", "to": "Jenny Zhang"}]
    text = fetched.files["sources/github/jennyzzt/dgm.md"]
    assert "## Files (2)" in text and "dgm/agent.py  (20,000 bytes)" in text and "## README" in text
    assert fetched.links == ["https://arxiv.org/abs/2505.22954"]


def test_github_listing_folds_big_directories():
    files = [{"path": "a.py", "type": "blob"}] + [{"path": f"logs/run{i}.txt", "type": "blob"} for i in range(30)]
    lines = github.listing(files)
    assert lines[0] == "a.py" and lines[-1] == "logs/... 24 more files" and len(lines) == 8


def test_github_listing_folds_many_sibling_directories():
    files = [{"path": f"runs/run{i:02d}/out.txt", "type": "blob"} for i in range(20)] + [{"path": "src/main.py", "type": "blob"}]
    lines = github.listing(files)
    assert lines[:3] == ["runs/run00/out.txt", "runs/run01/out.txt", "runs/run02/out.txt"]
    assert lines[3] == "runs/... 17 more directories (17 files)" and lines[4] == "src/main.py"


def test_web_title_is_the_most_specific(monkeypatch):
    page = """<html><head><title>The Real Title of the Post</title><meta property="og:title" content="Sakana AI">
    <meta property="og:site_name" content="Sakana AI"></head><body><main><h1>Sakana AI</h1><p>text</p></main></body></html>"""
    monkeypatch.setattr(web.http, "get_text", lambda url, headers=None: page)
    assert web.fetch("https://sakana.ai/dgm/", Options()).node["name"] == "The Real Title of the Post"
    meta = html.meta("<title>Post | Blog</title>")
    assert web.best_title(meta, "", "blog.example.org") == "Post"


def test_name_id():
    assert fetcher.name_id("Darwin Gödel Machine: Open-Ended Evolution of Self-Improving Agents") == "darwin-godel-machine"
    assert fetcher.name_id("But what is a neural network? | Deep learning chapter 1") == "but-what-is-a-neural-network"
    assert fetcher.name_id("Attention Is All You Need") == "attention-is-all-you-need"
    assert fetcher.name_id("Note: a very long title with many many words in it indeed") == "note-a-very-long-title-with-many-many"
    assert fetcher.name_id("A: B") == "a-b"


# -- web -----------------------------------------------------------------------

PAGE = """<html><head><title>On Graphs | Blog</title><meta property="og:title" content="On Graphs">
<meta name="author" content="Ann Author"><meta property="article:published_time" content="2025-06-01T10:00:00Z">
<meta property="og:site_name" content="Ann's Blog"><link rel="canonical" href="https://blog.example.org/on-graphs"></head>
<body><nav><a href="/">home</a></nav><main><h1>On Graphs</h1><p>Graphs are <em>everywhere</em>. See <a href="https://github.com/a/b">the code</a>.</p>
<ul><li>one</li><li>two</li></ul></main><footer>© 2025</footer></body></html>"""


def test_web(monkeypatch):
    monkeypatch.setattr(web.http, "get_text", lambda url, headers=None: PAGE)
    fetched = web.fetch("https://blog.example.org/on-graphs?utm=1", Options())
    assert fetched.node["name"] == "On Graphs"
    assert fetched.node["facts"] == {
        "url": "https://blog.example.org/on-graphs",
        "site": "Ann's Blog",
        "author": "Ann Author",
        "published": "2025-06-01",
        "file": "sources/web/blog.example.org/on-graphs.md",
    }
    text = fetched.files["sources/web/blog.example.org/on-graphs.md"]
    assert fetched.node["id"] == "on-graphs"
    assert "home" not in text and "©" not in text
    assert "Graphs are *everywhere*. See [the code](https://github.com/a/b)." in text and "- one\n- two" in text
    assert fetched.links == ["https://github.com/a/b"]


def test_html_meta_from_json_ld():
    page = '<html><head><script type="application/ld+json">{"@type":"Article","author":{"name":"Bo"},"datePublished":"2024-02-03T00:00:00Z"}</script><title>T</title></head><body><p>x</p></body></html>'
    found = html.meta(page)
    assert (found.title, found.author, found.published) == ("T", "Bo", "2024-02-03")


# -- writing into a graph --------------------------------------------------------


def fake(**facts) -> Fetched:
    return Fetched(
        kind="github",
        key="a/b",
        node={
            "type": "repository",
            "name": "a/b",
            "valid_from": "2024-01-01",
            "facts": {"github": "a/b", "url": "https://github.com/a/b", "stars": {"value": 10, "valid_from": "2025-01-01"}, "topics": ["x", "y"], "file": "sources/github/a/b.md", **facts},
            "edges": [{"rel": "owned_by", "to": "Ann"}],
            "body": "# a/b\n",
        },
        files={"sources/github/a/b.md": "# a/b\n\ndigest\n"},
        neighbours=[{"type": "person", "name": "Ann", "facts": {"github": "ann"}}],
        identity=("github", "a/b"),
        links=["https://arxiv.org/abs/1.2"],
    )


def test_write_without_model(tmp_path):
    graph = Graph.init(tmp_path)
    result = fetcher.write(graph, fake())
    assert not result.existing and result.node_id == "a-b" and result.files == ["sources/github/a/b.md"]
    assert result.counts["nodes added"] == 2 and result.counts["edges added"] == 1 and result.counts["files written"] == 1
    assert (tmp_path / "sources/github/a/b.md").read_text() == "# a/b\n\ndigest\n"
    graph = Graph.open(tmp_path)
    assert graph.node("ann").type == "person"
    assert {f.key: f.value for f in graph.facts("a-b") if f.key != "topics"}["stars"] == 10
    assert graph.body("a-b") == "# a/b\n"
    assert graph.check() == []

    # a refetch: the same node, changed facts get a new version, nothing doubles
    again = fetcher.write(Graph.open(tmp_path), fake(stars={"value": 12, "valid_from": "2025-02-01"}))
    assert again.existing and again.node_id == "a-b"
    assert again.counts["facts updated"] == 1 and again.counts["nodes added"] == 0 and again.counts["edges skipped"] == 1
    graph = Graph.open(tmp_path)
    stars = [f for f in graph.node("a-b").facts if f.key == "stars"]
    assert [(f.value, f.valid_from, f.superseded_at is None) for f in stars] == [(10, "2025-01-01", False), (12, "2025-02-01", True)]
    assert len([f for f in graph.facts("a-b") if f.key == "topics"]) == 2


def test_write_with_a_narrower_model(tmp_path):
    graph = Graph.init(tmp_path)
    run(tmp_path, "model", "add", "node", "repository")
    run(tmp_path, "model", "add", "fact", "repository", "github", "text")
    run(tmp_path, "model", "add", "fact", "repository", "stars", "int")
    graph = Graph.open(tmp_path)
    result = fetcher.write(graph, fake())
    assert result.counts["nodes added"] == 1
    assert any("no 'url' fact" in d for d in result.dropped)
    assert any("no person type" in d for d in result.dropped)
    assert "ann" not in Graph.open(tmp_path).nodes
    assert Graph.open(tmp_path).check() == []

    # the model refuses a type it does not have, and --type picks another
    with pytest.raises(FetchError, match="no node type 'article'"):
        fetcher.prepare(graph, fake(), type="article")


def test_write_finds_the_node_by_identity_and_takes_an_id(tmp_path):
    graph = Graph.init(tmp_path)
    fetcher.write(graph, fake(), id="dgm")
    graph = Graph.open(tmp_path)
    assert "dgm" in graph.nodes
    assert fetcher.prepare(graph, fake()).node_id == "dgm"


def test_install_model(tmp_path):
    Graph.init(tmp_path)
    assert "Added node type video" in run(tmp_path, "model", "add", "source", "youtube")
    assert "already has" in run(tmp_path, "model", "add", "source", "youtube")
    run(tmp_path, "model", "add", "source", "arxiv")
    run(tmp_path, "model", "add", "source", "github")
    model = Graph.open(tmp_path).model
    assert {"video", "paper", "person", "repository", "organisation"} <= set(model.nodes)
    assert model.edges["owned_by"].targets == ("person", "organisation") and not model.edges["owned_by"].many
    assert model.nodes["paper"].facts["category"].many and model.nodes["paper"].facts["arxiv"].required
    with pytest.raises(FetchError):
        run(tmp_path, "model", "add", "source", "gopher")


def test_cli_fetch(tmp_path, monkeypatch):
    Graph.init(tmp_path)
    run(tmp_path, "model", "add", "source", "github")
    _github(monkeypatch)
    shown = yaml.safe_load(run(tmp_path, "fetch", "https://github.com/jennyzzt/dgm", "--dry-run"))
    assert shown["nodes"][0]["id"] == "jennyzzt-dgm" and shown["files"] == {"sources/github/jennyzzt/dgm.md": "42 words"}
    assert not (tmp_path / "sources").exists()

    output = run(tmp_path, "fetch", "https://github.com/jennyzzt/dgm")
    assert output.startswith("repository jennyzzt-dgm added:") and "also mentions: https://arxiv.org/abs/2505.22954" in output
    result = json.loads(run(tmp_path, "fetch", "https://github.com/jennyzzt/dgm", "--json"))
    assert result["existing"] and result["id"] == "jennyzzt-dgm" and result["counts"].get("nodes added", 0) == 0
    assert run(tmp_path, "check").endswith("OK: 2 nodes")
