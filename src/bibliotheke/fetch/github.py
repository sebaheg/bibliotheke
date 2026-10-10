"""A GitHub repository: what the API says about it, and a digest of the
README and the file tree for an agent to read before deciding what to clone.
Nothing is cloned into the graph; ``bib repo`` is the tool for history."""

from __future__ import annotations

import os
import re
from datetime import date
from typing import Any

from bibliotheke.fetch import Fetched, Options, claimed, source_path
from bibliotheke.fetch import http

kind = "github"
type = "repository"

model: dict[str, Any] = {
    "nodes": {
        "repository": {
            "description": "A code repository on GitHub.",
            "name": "owner/name as on GitHub.",
            "body": "Its description, then notes on what it does and how.",
            "facts": {
                "github": {"type": "text", "required": True, "description": "owner/name."},
                "url": {"type": "url"},
                "description": {"type": "text"},
                "language": {"type": "text", "description": "The main language."},
                "license": {"type": "text", "description": "SPDX id."},
                "topics": {"type": "text", "many": True},
                "stars": {"type": "int", "description": "Stargazers when last fetched."},
                "forks": {"type": "int"},
                "created": {"type": "time"},
                "homepage": {"type": "url"},
                "file": {"type": "file", "description": "A digest: README and file tree."},
            },
        },
        "person": {"description": "A person.", "facts": {"github": {"type": "text", "description": "GitHub login."}}},
        "organisation": {"description": "An organisation.", "facts": {"github": {"type": "text", "description": "GitHub login."}}},
    },
    "edges": {"owned_by": {"from": "repository", "to": ["person", "organisation"], "many": False, "description": "The GitHub account it belongs to."}},
}

_URL = re.compile(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:[/#?].*)?$")
API = "https://api.github.com"
MAX_TREE = 2000  # paths listed in the digest
PER_DIR = 12  # a directory with more files than this is folded
MAX_SIBLINGS = 10  # a directory with more subdirectories than this is folded


def key(url: str) -> str | None:
    m = _URL.search(url.strip())
    return f"{m[1]}/{m[2]}" if m and m[1] not in ("orgs", "users", "topics", "search") else None


def _headers(accept: str = "application/vnd.github+json") -> dict[str, str]:
    headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def repository(key: str) -> dict[str, Any]:
    return http.get_json(f"{API}/repos/{key}", _headers())


def readme(key: str) -> str | None:
    from bibliotheke.fetch import FetchError

    try:
        return http.get_text(f"{API}/repos/{key}/readme", _headers("application/vnd.github.raw+json"))
    except FetchError:
        return None


def tree(key: str, branch: str) -> tuple[list[dict[str, Any]], bool]:
    """Every path in the default branch and whether GitHub cut the list short."""
    from bibliotheke.fetch import FetchError

    try:
        data = http.get_json(f"{API}/repos/{key}/git/trees/{branch}?recursive=1", _headers())
    except FetchError:
        return [], False
    return data.get("tree") or [], bool(data.get("truncated"))


def owner_name(login: str) -> str:
    from bibliotheke.fetch import FetchError

    try:
        return http.get_json(f"{API}/users/{login}", _headers()).get("name") or login
    except FetchError:
        return login


def digest(repo: dict[str, Any], paths: list[dict[str, Any]], truncated: bool, text: str | None) -> str:
    lines = [f"# {repo['full_name']}", ""]
    if repo.get("description"):
        lines += [repo["description"], ""]
    lines.append(f"- Source: {repo['html_url']}")
    for label, field in (("Language", "language"), ("Stars", "stargazers_count"), ("Created", "created_at"), ("Last push", "pushed_at")):
        if repo.get(field) is not None:
            lines.append(f"- {label}: {repo[field]}")
    if repo.get("license") and repo["license"].get("spdx_id") not in (None, "NOASSERTION"):
        lines.append(f"- License: {repo['license']['spdx_id']}")
    if repo.get("homepage"):
        lines.append(f"- Homepage: {repo['homepage']}")
    files = [p for p in paths if p.get("type") == "blob"]
    lines += ["", f"## Files ({len(files)}{'+' if truncated else ''})", "", "```"]
    lines += listing(files)
    if truncated:
        lines.append("... and more: GitHub cut the list short")
    lines += ["```", ""]
    if text:
        lines += ["## README", "", text.strip(), ""]
    return "\n".join(lines)


def listing(files: list[dict[str, Any]]) -> list[str]:
    """One line per file, in tree order, with large files sized. A directory
    with many files shows the first few and how many more; a directory with
    many subdirectories shows the first few of those, so a dump of logs or
    runs does not bury the code."""
    tree: dict[str, Any] = {}
    for item in files:
        *dirs, name = item["path"].split("/")
        node = tree
        for part in dirs:
            node = node.setdefault(part + "/", {})
        node[name] = item
    lines: list[str] = []

    def count(node: dict[str, Any]) -> int:
        return sum(count(v) if k.endswith("/") else 1 for k, v in node.items())

    def render(node: dict[str, Any], prefix: str) -> None:
        blobs = [(k, v) for k, v in node.items() if not k.endswith("/")]
        dirs = [(k, v) for k, v in node.items() if k.endswith("/")]
        shown = blobs if len(blobs) <= PER_DIR else blobs[: PER_DIR // 2]
        for name, item in shown:
            size = item.get("size")
            lines.append(prefix + name + (f"  ({size:,} bytes)" if isinstance(size, int) and size >= 10_000 else ""))
        if len(blobs) > len(shown):
            lines.append(f"{prefix}... {len(blobs) - len(shown)} more files")
        kept = dirs if len(dirs) <= MAX_SIBLINGS else dirs[:3]
        for name, sub in kept:
            render(sub, prefix + name)
        if len(dirs) > len(kept):
            rest = sum(count(sub) for _, sub in dirs[len(kept):])
            lines.append(f"{prefix}... {len(dirs) - len(kept)} more directories ({rest} files)")

    render(tree, "")
    if len(lines) > MAX_TREE:
        return lines[:MAX_TREE] + ["... not all listed"]
    return lines


def fetch(key: str, options: Options) -> Fetched:
    repo = repository(key)
    key = repo.get("full_name") or key  # as GitHub spells it, after a rename
    text = readme(key)
    paths, truncated = tree(key, repo.get("default_branch") or "HEAD")
    rel = source_path(kind, f"{key}.md")
    owner = repo.get("owner") or {}
    owner_type = "organisation" if owner.get("type") == "Organization" else "person"
    neighbours = []
    edges = []
    if owner.get("login"):
        neighbours.append({"type": owner_type, "name": owner_name(owner["login"]), "facts": {"github": owner["login"]}})
        edges.append({"rel": "owned_by", "to": neighbours[0]["name"]})
    today = date.today().isoformat()
    created = (repo.get("created_at") or "")[:10] or None
    spdx = (repo.get("license") or {}).get("spdx_id")
    node = {
        "type": type,
        "name": key,
        "valid_from": created,
        "facts": {
            "github": key,
            "url": repo.get("html_url") or f"https://github.com/{key}",
            "description": repo.get("description") or None,
            "language": repo.get("language") or None,
            "license": spdx if spdx and spdx != "NOASSERTION" else None,
            "topics": repo.get("topics") or [],
            "stars": {"value": repo["stargazers_count"], "valid_from": today} if repo.get("stargazers_count") is not None else None,
            "forks": {"value": repo["forks_count"], "valid_from": today} if repo.get("forks_count") is not None else None,
            "created": created,
            "homepage": repo.get("homepage") or None,
            "file": rel,
        },
        "edges": edges,
        "body": f"# {key}\n\n{repo.get('description') or ''}\n\nDigest: {rel}\n",
    }
    mentioned = [repo["homepage"]] if repo.get("homepage") else []
    mentioned += re.findall(r"https?://[^\s<>()\"'\]]+", text or "")
    return Fetched(kind=kind, key=key, node=node, files={rel: digest(repo, paths, truncated, text)}, neighbours=neighbours, identity=("github", key), links=claimed(mentioned, (kind, key)))
