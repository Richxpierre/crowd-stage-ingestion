"""Bounded official sitemap discovery; never spider unrestricted websites."""
from __future__ import annotations
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit
from .fetch import host_of


def discover_sitemap_urls(fetcher, sitemap_url: str, pattern: str | None = None,
                          max_sitemaps: int = 5, max_candidates: int = 30000) -> list[str]:
    source_host = host_of(sitemap_url)
    queue = [sitemap_url]
    visited: set[str] = set()
    candidates: list[str] = []
    accepted = re.compile(pattern) if pattern else None
    while queue and len(visited) < max_sitemaps:
        url = queue.pop(0)
        if url in visited or host_of(url) != source_host:
            continue
        visited.add(url)
        status, body, _ = fetcher.fetch(url)
        if status != 200:
            continue
        root = ET.fromstring(body)
        kind = root.tag.rsplit("}", 1)[-1]
        for item in root:
            loc = next((child.text for child in item if child.tag.rsplit("}", 1)[-1] == "loc"), None)
            if not loc:
                continue
            try:
                if host_of(loc) != source_host:
                    continue
            except ValueError:
                continue
            if kind == "sitemapindex":
                queue.append(loc)
            elif kind == "urlset" and (accepted is None or accepted.search(urlsplit(loc).path)):
                candidates.append(loc)
                if len(candidates) >= max_candidates:
                    return list(dict.fromkeys(candidates))
        if kind not in ("urlset", "sitemapindex"):
            raise ValueError("Not a supported XML sitemap")
    return list(dict.fromkeys(candidates))
