"""HTML to Markdown with the standard library: enough for an agent to read
an article or a paper, not a faithful rendering. The page's metadata
(title, author, date, site) is read from the usual tags."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin

_SKIP = {"script", "style", "noscript", "nav", "header", "footer", "aside", "svg", "form", "button", "iframe", "template"}
_BLOCK = {"p", "div", "section", "article", "main", "ul", "ol", "li", "blockquote", "pre", "table", "tr", "figure", "figcaption", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "br", "dl", "dt", "dd"}
_HEADING = {f"h{n}": n for n in range(1, 7)}
_VOID = {"br", "img", "hr", "meta", "link", "input", "source", "wbr", "col", "area", "base", "embed", "param", "track"}


@dataclass
class Meta:
    title: str = ""  # og:title, else the <title> tag
    title_tag: str = ""
    author: str = ""
    published: str = ""
    site: str = ""
    description: str = ""
    canonical: str = ""


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta = Meta()
        self._in_title = False
        self._title: list[str] = []
        self._jsonld: list[str] = []
        self._in_jsonld = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            name = (a.get("property") or a.get("name") or "").lower()
            content = a.get("content", "").strip()
            if not content:
                return
            m = self.meta
            if name in ("og:title", "twitter:title") and not m.title:
                m.title = content
            elif name in ("author", "article:author", "dc.creator", "citation_author", "parsely-author") and not m.author:
                m.author = content
            elif name in ("article:published_time", "date", "dc.date", "citation_date", "citation_publication_date", "parsely-pub-date", "datepublished") and not m.published:
                m.published = content
            elif name == "og:site_name" and not m.site:
                m.site = content
            elif name in ("description", "og:description") and not m.description:
                m.description = content
        elif tag == "link" and (a.get("rel") or "").lower() == "canonical" and not self.meta.canonical:
            self.meta.canonical = a.get("href", "")
        elif tag == "time" and a.get("datetime") and not self.meta.published:
            self.meta.published = a["datetime"]
        elif tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
            self._in_jsonld = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "script":
            self._in_jsonld = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title.append(data)
        elif self._in_jsonld:
            self._jsonld.append(data)

    def finish(self) -> Meta:
        m = self.meta
        m.title_tag = " ".join("".join(self._title).split())
        if not m.title:
            m.title = m.title_tag
        for text in self._jsonld:
            import json

            try:
                data = json.loads(text)
            except ValueError:
                continue
            for item in data if isinstance(data, list) else [data]:
                if not isinstance(item, dict):
                    continue
                if not m.author:
                    author = item.get("author")
                    if isinstance(author, list):
                        author = author[0] if author else None
                    if isinstance(author, dict):
                        m.author = str(author.get("name") or "")
                    elif isinstance(author, str):
                        m.author = author
                if not m.published and isinstance(item.get("datePublished"), str):
                    m.published = item["datePublished"]
        m.published = _date(m.published)
        return m


def meta(html: str) -> Meta:
    parser = _MetaParser()
    parser.feed(html)
    return parser.finish()


def _date(text: str) -> str:
    """A date as YYYY-MM-DD when the text starts with one, else as given."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", text.strip())
    return m[1] if m else text.strip()


class _Markdown(HTMLParser):
    """Builds Markdown from the tag stream. Text outside ``<main>`` or
    ``<article>`` is skipped when the page has one."""

    def __init__(self, base: str, scope: str | None) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.scope = scope  # the tag that holds the content, or None for the whole body
        self.out: list[str] = []
        self.stack: list[str] = []
        self.skip = 0  # the stack depth at which a skipped element started, or 0
        self.inside = scope is None
        self.scope_depth = 0
        self.lists: list[tuple[str, int]] = []
        self.href: str | None = None
        self.math = 0
        self.pre = 0

    # -- helpers --
    def _text(self, text: str) -> None:
        if self.inside and not self.skip:
            self.out.append(text)

    def _block(self) -> None:
        """Start a new paragraph: two newlines, never more."""
        if not self.inside or self.skip:
            return
        joined = "".join(self.out).rstrip(" ")
        self.out = [joined]
        if joined and not joined.endswith("\n\n"):
            self.out.append("\n" if joined.endswith("\n") else "\n\n")

    def _line(self) -> None:
        """Start a new line, without a blank one."""
        if not self.inside or self.skip:
            return
        joined = "".join(self.out).rstrip(" ")
        self.out = [joined]
        if joined and not joined.endswith("\n"):
            self.out.append("\n")

    # -- events --
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag not in _VOID:
            self.stack.append(tag)
        if self.scope and tag == self.scope and not self.inside:
            self.inside = True
            self.scope_depth = len(self.stack)
            return
        if self.skip or self.math:
            return
        if tag in _SKIP or "display:none" in a.get("style", "").replace(" ", ""):
            self.skip = len(self.stack)
            return
        if tag == "math":
            self.math = len(self.stack)
            alt = a.get("alttext", "").strip()
            if alt:
                self._text(f"${alt}$" if a.get("display") != "block" else f"\n\n$${alt}$$\n\n")
            return
        if tag in _HEADING:
            self._block()
            self._text("#" * _HEADING[tag] + " ")
        elif tag in ("ul", "ol"):
            self._line() if self.lists else self._block()
            self.lists.append((tag, 0))
        elif tag == "li":
            self._line()
            if self.lists:
                kind, n = self.lists[-1]
                self.lists[-1] = (kind, n + 1)
                self._text("  " * (len(self.lists) - 1) + (f"{n + 1}. " if kind == "ol" else "- "))
            else:
                self._text("- ")
        elif tag == "pre":
            self._block()
            self._text("```\n")
            self.pre += 1
        elif tag == "code" and not self.pre:
            self._text("`")
        elif tag == "blockquote":
            self._block()
            self._text("> ")
        elif tag == "br":
            self._text("\n")
        elif tag == "hr":
            self._block()
            self._text("---")
            self._block()
        elif tag in ("strong", "b"):
            self._text("**")
        elif tag in ("em", "i"):
            self._text("*")
        elif tag == "a" and a.get("href") and not a["href"].startswith(("#", "javascript:")):
            self.href = urljoin(self.base, a["href"])
            self._text("[")
        elif tag == "img" and a.get("alt"):
            self._text(f"![{a['alt']}]({urljoin(self.base, a.get('src', ''))})")
        elif tag == "tr":
            self._line()
        elif tag in ("td", "th"):
            if self.out and not self.out[-1].endswith("\n"):
                self._text(" | ")
        elif tag in _BLOCK:
            self._block()

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID:
            return
        if self.scope and self.inside and tag == self.scope and len(self.stack) == self.scope_depth:
            self.inside = False
            self.scope_depth = 0
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        elif tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                pass
        else:
            return  # a stray end tag
        if self.math:
            if len(self.stack) < self.math:
                self.math = 0
            return
        if self.skip:
            if len(self.stack) < self.skip:
                self.skip = 0
            return
        if tag in _HEADING:
            self._block()
        elif tag in ("ul", "ol"):
            if self.lists:
                self.lists.pop()
            self._line() if self.lists else self._block()
        elif tag == "li":
            self._line()
        elif tag == "pre":
            self.pre = max(0, self.pre - 1)
            self._text("\n```")
            self._block()
        elif tag == "code" and not self.pre:
            self._text("`")
        elif tag in ("strong", "b"):
            self._text("**")
        elif tag in ("em", "i"):
            self._text("*")
        elif tag == "a" and self.href:
            self._text(f"]({self.href})")
            self.href = None
        elif tag in _BLOCK:
            self._block()

    def handle_data(self, data: str) -> None:
        if self.math:
            return
        if self.pre:
            self._text(data)
        else:
            text = re.sub(r"\s+", " ", data)
            if self.out and self.out[-1].endswith(("\n", " ")):
                text = text.lstrip(" ")
            if text:
                self._text(text)

    def result(self) -> str:
        text = "".join(self.out)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"\[\]\([^)]*\)", "", text)  # links with no text
        return text.strip() + "\n"


def to_markdown(html: str, base: str = "", scope: str | None = "auto") -> str:
    """Markdown of the page. ``scope`` names the element that holds the
    content; ``auto`` takes ``<main>``, then ``<article>``, when present."""
    if scope == "auto":
        scope = next((tag for tag in ("main", "article") if re.search(rf"<{tag}[\s>]", html, re.I)), None)
    parser = _Markdown(base, scope)
    parser.feed(html)
    return parser.result()


def links(html: str, base: str = "") -> list[str]:
    """Every absolute link on the page, once each, in order."""
    found: list[str] = []
    for href in re.findall(r"""<a\s[^>]*href\s*=\s*["']([^"']+)["']""", html, re.I):
        url = urljoin(base, href)
        if url.startswith("http") and url not in found:
            found.append(url)
    return found


__all__ = ["Meta", "links", "meta", "to_markdown"]
