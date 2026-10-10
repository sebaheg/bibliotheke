"""Any web page, such as a blog post: the article as Markdown and what the
page says about itself. The fallback when no other handler takes a URL."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from bibliotheke.fetch import FetchError, Fetched, Options, claimed, name_id, source_path
from bibliotheke.fetch import html as htmlmod
from bibliotheke.fetch import http
from bibliotheke.graph import slugify

kind = "web"
type = "article"

model: dict[str, Any] = {
    "nodes": {
        "article": {
            "description": "A web page, such as a blog post.",
            "name": "The page's title.",
            "body": "Notes on what it says.",
            "facts": {
                "url": {"type": "url", "required": True},
                "site": {"type": "text", "description": "The site's name, or its host."},
                "author": {"type": "text"},
                "published": {"type": "time"},
                "file": {"type": "file", "description": "The page's text as Markdown."},
            },
        },
    },
    "edges": {},
}


def key(url: str) -> str | None:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))


def best_title(meta: htmlmod.Meta, text: str, site: str) -> str:
    """The page's title: the longest of the og:title, the <title> tag without
    a trailing site name, and the first heading, since any one of them can
    be the site's name instead of the page's."""
    heading = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), "")
    tag = re.sub(r"\s*[|\-–—:·]\s*" + re.escape(site) + r"\s*$", "", meta.title_tag, flags=re.I) if site else meta.title_tag
    tag = re.sub(r"\s+[|–—]\s+[^|–—]{1,30}$", "", tag)
    og = meta.title if meta.title != meta.title_tag else ""
    candidates = [c for c in (og, tag, heading) if c and c.lower() != site.lower()]
    return max(candidates, key=len) if candidates else ""


def fetch(key: str, options: Options) -> Fetched:
    page = http.get_text(key)
    meta = htmlmod.meta(page)
    canonical = urljoin(key, meta.canonical) if meta.canonical else key
    text = htmlmod.to_markdown(page, key)
    if not text.strip():
        raise FetchError(f"{key}: no text on the page")
    host = urlsplit(key).netloc.removeprefix("www.")
    title = best_title(meta, text, meta.site or host) or key
    slug = name_id(title) or slugify(urlsplit(key).path) or "index"
    rel = source_path(kind, host, f"{slug}.md")
    header = f"# {title}\n\n- Source: {key}\n"
    if meta.author:
        header += f"- Author: {meta.author}\n"
    if meta.published:
        header += f"- Published: {meta.published}\n"
    body = f"# {title}\n\n"
    if meta.description:
        body += f"{meta.description}\n\n"
    body += f"Text: {rel}\n"
    node = {
        "type": type,
        "id": name_id(title),
        "name": title,
        "valid_from": meta.published or None,
        "facts": {
            "url": canonical,
            "site": meta.site or host,
            "author": meta.author or None,
            "published": meta.published or None,
            "file": rel,
        },
        "body": body,
    }
    return Fetched(kind=kind, key=key, node=node, files={rel: header + "\n" + text}, identity=("url", canonical), links=claimed(htmlmod.links(page, key), (kind, key)))
