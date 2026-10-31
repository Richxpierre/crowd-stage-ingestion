"""Official event page parser: schema.org Event first, then an explicit CSS mapping.

Site-specific selectors are user-managed and are never inferred from other sites.
No date means no event -- we do not make up events from OpenGraph titles.
"""
from __future__ import annotations

import re
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from .parsers import _finish, clean_url, date_part, parse_jsonld, plain


def _value(soup: BeautifulSoup, css: str | None, attribute: str | None = None) -> str | None:
    if not css:
        return None
    match = soup.select_one(css)
    if match is None:
        return None
    value = match.get(attribute) if attribute else match.get_text(" ", strip=True)
    return plain(value)


def parse_official_html(body: bytes, source: dict, page_url: str | None = None) -> list[dict]:
    """Parse JSON-LD if available, otherwise the source's configured CSS selectors.

    Supported selectors keys: title, start_date, end_date, venue, city, country,
    summary, registration_url, cfp_url. Date selector can refer to a <time>
    element; its datetime attribute is preferred, else use its text content.
    """
    from_ld = parse_jsonld(body, source, page_url=page_url)
    if from_ld:
        return from_ld
    selectors = source.get("selectors") or {}
    if not isinstance(selectors, dict) or not selectors.get("title") or not selectors.get("start_date"):
        return []
    soup = BeautifulSoup(body, "html.parser")

    def field(key: str) -> str | None:
        css = selectors.get(key)
        if not isinstance(css, str) or not css:
            return None
        node = soup.select_one(css)
        if node is None:
            return None
        if key in ("start_date", "end_date"):
            return plain(node.get("datetime") or node.get("content") or node.get_text(" ", strip=True))
        if key in ("registration_url", "cfp_url"):
            return clean_url(urljoin(page_url or source["url"], node.get("href") or node.get("content") or ""))
        return plain(node.get_text(" ", strip=True), 1200 if key == "summary" else 240)

    title = field("title")
    start = date_part(field("start_date"))
    if not title or not start:
        return []
    end = date_part(field("end_date")) or start
    event_url = clean_url(page_url or source["url"])
    record = dict(
        title=title, start_date=start, end_date=end, timezone=None,
        venue=field("venue"), city=field("city"), region=None,
        country=field("country"), format="unknown", categories=[],
        summary=field("summary"), event_url=event_url,
        official_url=event_url,
        registration_url=field("registration_url"), cfp_url=field("cfp_url"),
        cfp_deadline=None, status="upcoming", source_url=event_url,
    )
    try:
        return [_finish(record, source["publisher"], event_url)]
    except ValueError:
        return []
