"""An arXiv paper: its metadata from the arXiv API, the PDF, and the text as
Markdown from arXiv's HTML rendering when there is one (papers from late
2023 on), else from the PDF when PyMuPDF is installed. Authors become person
nodes with ``authored_by`` edges."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

from bibliotheke.fetch import FetchError, Fetched, Options, claimed, name_id, source_path
from bibliotheke.fetch import html as htmlmod
from bibliotheke.fetch import http

kind = "arxiv"
type = "paper"

model: dict[str, Any] = {
    "nodes": {
        "paper": {
            "description": "A paper on arXiv.",
            "name": "The title.",
            "body": "The abstract, then notes on what it says.",
            "facts": {
                "arxiv": {"type": "text", "required": True, "description": "The arXiv id, without version."},
                "url": {"type": "url", "description": "The abstract page on arXiv."},
                "doi": {"type": "text"},
                "published": {"type": "time", "description": "When the first version appeared."},
                "category": {"type": "text", "many": True, "description": "arXiv categories, e.g. cs.AI."},
                "pdf": {"type": "file", "description": "The PDF."},
                "file": {"type": "file", "description": "The text as Markdown."},
            },
        },
        "person": {"description": "A person."},
    },
    "edges": {"authored_by": {"from": "paper", "to": "person", "description": "Its authors."}},
}

_URL = re.compile(r"(?:arxiv\.org/(?:abs|pdf|html)/|^arxiv:|^)(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?(?:\.pdf)?(?:[/#?].*)?$")
_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
API = "https://export.arxiv.org/api/query?id_list="


def key(url: str) -> str | None:
    m = _URL.search(url.strip())
    return m[1] if m else None


def entry(arxiv_id: str) -> dict[str, Any]:
    """The paper's metadata from the arXiv API, as plain values."""
    root = ET.fromstring(http.get_text(API + arxiv_id))
    found = root.find("atom:entry", _NS)
    if found is None or found.find("atom:title", _NS) is None or (found.findtext("atom:title", "", _NS).strip() == "Error"):
        raise FetchError(f"arXiv has no paper {arxiv_id}")

    def text(tag: str) -> str:
        return " ".join((found.findtext(tag, "", _NS) or "").split())

    return {
        "id": arxiv_id,
        "title": text("atom:title"),
        "summary": text("atom:summary"),
        "published": text("atom:published")[:10],
        "updated": text("atom:updated")[:10],
        "authors": [" ".join((a.findtext("atom:name", "", _NS) or "").split()) for a in found.findall("atom:author", _NS)],
        "categories": [c.get("term", "") for c in found.findall("atom:category", _NS) if c.get("term")],
        "doi": text("arxiv:doi") or None,
        "comment": text("arxiv:comment") or None,
        "journal_ref": text("arxiv:journal_ref") or None,
    }


def html_text(arxiv_id: str) -> str | None:
    """The paper as Markdown from arXiv's HTML rendering, or None when the
    paper has none."""
    url = f"https://arxiv.org/html/{arxiv_id}"
    try:
        page = http.get_text(url)
    except FetchError:
        return None
    if "ltx_page_main" not in page and "<article" not in page:
        return None
    return htmlmod.to_markdown(page, url, scope="article")


def pdf_text(pdf: bytes) -> str | None:
    """The PDF's text when PyMuPDF is installed, else None."""
    try:
        import pymupdf  # type: ignore[import-not-found]
    except ImportError:
        return None
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        pages = [page.get_text() for page in doc]
    return "\n\n".join(p.strip() for p in pages if p.strip()) + "\n"


def fetch(key: str, options: Options) -> Fetched:
    meta = entry(key)
    pdf_rel = source_path(kind, f"{key}.pdf")
    md_rel = source_path(kind, f"{key}.md")
    pdf = http.get(f"https://arxiv.org/pdf/{key}")
    files: dict[str, str | bytes] = {pdf_rel: pdf}
    header = f"# {meta['title']}\n\n- Authors: {', '.join(meta['authors'])}\n- Published: {meta['published']}\n- Source: https://arxiv.org/abs/{key}\n\n"
    text = html_text(key)
    how = "arXiv HTML"
    if text is None:
        text = pdf_text(pdf)
        how = "PDF text"
    if text is not None:
        files[md_rel] = header + f"- Text: {how}\n\n" + text
    body = f"# {meta['title']}\n\n## Abstract\n\n{meta['summary']}\n"
    if meta["comment"]:
        body += f"\nComment: {meta['comment']}\n"
    if meta["journal_ref"]:
        body += f"\nJournal: {meta['journal_ref']}\n"
    authors = [{"type": "person", "name": name} for name in meta["authors"] if name]
    node = {
        "type": type,
        "id": name_id(meta["title"]),
        "name": meta["title"],
        "valid_from": meta["published"],
        "facts": {
            "arxiv": key,
            "url": f"https://arxiv.org/abs/{key}",
            "doi": meta["doi"],
            "published": meta["published"],
            "category": meta["categories"],
            "pdf": pdf_rel,
            "file": md_rel if md_rel in files else None,
        },
        "edges": [{"rel": "authored_by", "to": a["name"]} for a in authors],
        "body": body,
    }
    mentioned = re.findall(r"https?://[^\s<>()\"']+", " ".join(filter(None, (meta["summary"], meta["comment"]))))
    return Fetched(kind=kind, key=key, node=node, files=files, neighbours=authors, identity=("arxiv", key), links=claimed(mentioned, (kind, key)))
