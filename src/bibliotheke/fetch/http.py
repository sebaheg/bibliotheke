"""The one place the fetchers touch the network, so tests can stand in for it."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

USER_AGENT = "bibliotheke (+https://github.com/sebaheg/bibliotheke)"


def get(url: str, headers: dict[str, str] | None = None, *, timeout: float = 60) -> bytes:
    from bibliotheke.fetch import FetchError

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        raise FetchError(f"{url}: HTTP {error.code} {error.reason}") from None
    except urllib.error.URLError as error:
        raise FetchError(f"{url}: {error.reason}") from None


def get_text(url: str, headers: dict[str, str] | None = None) -> str:
    return get(url, headers).decode("utf-8", errors="replace")


def get_json(url: str, headers: dict[str, str] | None = None) -> Any:
    return json.loads(get_text(url, headers))


def status(url: str) -> int:
    """The status code of a HEAD-like probe, without raising."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except urllib.error.URLError:
        return 0
