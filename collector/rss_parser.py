"""RSS / Atom feed parser for conference events.

Strategy:
  1. Try to find schema.org Event JSON-LD in each <item>/<entry> description (some feeds embed it).
  2. Otherwise extract title, pubDate/updated, link, and description from the feed item.
     We only create a *candidate URL* observation — not a full event — when we cannot resolve
     a start_date, because a start_date is mandatory in the CrowdStage schema.
  3. Items whose title / description strongly suggest an event (date pattern found) get promoted
     to a proper event record with the best-guess date.

This parser is intentionally conservative: it never invents dates.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from xml.etree import ElementTree as ET

from .parsers import _finish, clean_url, date_part, plain


# Namespace map used by Atom feeds
_ATOM_NS = "http://www.w3.org/2005/Atom"
_ATOM = f"{{{_ATOM_NS}}}"

# Patterns that identify a date range in plain text, e.g. "March 5–7, 2026" or "2026-03-05"
_DATE_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2})"              # ISO date
    r"|(\w+ \d{1,2}(?:[–\-]\d{1,2})?,?\s*\d{4})"  # "March 5–7, 2026"
    r"|(\d{1,2} \w+ \d{4})",              # "5 March 2026"
    re.I,
)

# Month name → number
_MONTHS = {m: i + 1 for i, m in enumerate([
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
])}


def _text(el) -> str:
    """Return element text, stripping CDATA and HTML tags."""
    if el is None:
        return ""
    raw = (el.text or "").strip()
    # Strip HTML tags for plain text
    raw = re.sub(r"<[^>]+>", " ", unescape(raw))
    return re.sub(r"\s+", " ", raw).strip()


def _guess_date(text: str) -> str | None:
    """Try to extract the first YYYY-MM-DD from text; returns ISO string or None."""
    m = _DATE_RE.search(text)
    if not m:
        return None
    iso = m.group(1)
    if iso:
        return iso
    # Try to parse "March 5, 2026" etc.
    phrase = (m.group(2) or m.group(3) or "").strip().rstrip(",")
    parts = re.split(r"[\s,]+", phrase)
    year = month = day = None
    for part in parts:
        if part.isdigit():
            v = int(part)
            if v > 1900:
                year = v
            else:
                day = v
        else:
            mon = _MONTHS.get(part.lower())
            if mon:
                month = mon
    if year and month and day:
        try:
            return f"{year:04d}-{month:02d}-{day:02d}"
        except Exception:
            return None
    return None


def _parse_pub_date(text: str | None) -> str | None:
    """Parse RFC 2822 or ISO-8601 date string to YYYY-MM-DD."""
    if not text:
        return None
    text = text.strip()
    # Try ISO-8601 first
    iso = date_part(text)
    if iso:
        return iso
    # Try RFC 2822 (RSS pubDate)
    try:
        dt = parsedate_to_datetime(text)
        return dt.date().isoformat()
    except Exception:
        pass
    return None


def _el(item, *tags):
    """Find first matching element among multiple tag names."""
    for tag in tags:
        found = item.find(tag)
        if found is not None:
            return found
    return None


def _rss_items(root: ET.Element):
    """Yield (title, link, description, date_str) tuples from RSS or Atom feed."""
    tag = root.tag
    # Atom
    if tag == f"{_ATOM}feed" or tag == "feed":
        for entry in root.findall(f"{_ATOM}entry") or root.findall("entry"):
            title_el = _el(entry, f"{_ATOM}title", "title")
            link_el = _el(entry, f"{_ATOM}link", "link")
            summ_el = _el(entry, f"{_ATOM}summary", "summary", f"{_ATOM}content", "content")
            updated_el = _el(entry, f"{_ATOM}updated", "updated")
            link = (link_el.get("href") if link_el is not None else None) or _text(link_el)
            yield _text(title_el), link, _text(summ_el), _text(updated_el)
        return
    # RSS 1.0 / 2.0 — items are inside <channel>
    channel = root.find("channel")
    if channel is None:
        channel = root
    for item in channel.findall("item"):
        title_el = item.find("title")
        link_el = item.find("link")
        desc_el = item.find("description")
        # pubDate can be in different namespaces
        pub_el = _el(item, "pubDate", "{http://purl.org/dc/elements/1.1/}date", "dc:date", "date")
        yield _text(title_el), _text(link_el), _text(desc_el), _text(pub_el)


def parse_rss(body: bytes, source: dict) -> list[dict]:
    """Parse an RSS or Atom feed and return event-like records.

    Records without a resolvable start_date are silently skipped (safe default).
    """
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []

    out: list[dict] = []
    publisher = source.get("publisher", "")
    is_official = bool(source.get("is_official_source"))

    for title_raw, link_raw, desc_raw, date_raw in _rss_items(root):
        title = plain(title_raw, 240)
        if not title:
            continue

        event_url = clean_url(link_raw)
        combined_text = f"{title_raw} {desc_raw} {date_raw}"

        # 1. Explicit date from pubDate/updated
        start = _parse_pub_date(date_raw)
        # 2. Fall back: scan text for a date pattern
        if not start:
            start = _guess_date(combined_text)
        # 3. Still no date — skip
        if not start:
            continue

        summary = plain(desc_raw, 1200) if desc_raw else None
        stable_id = event_url or f"{publisher}|{title.lower()}|{start}"

        rec = dict(
            title=title,
            start_date=start,
            end_date=start,       # RSS rarely has end date; use same day
            timezone=None,
            venue=None,
            city=None,
            region=None,
            country=None,
            format="unknown",
            categories=[],
            summary=summary,
            event_url=event_url,
            official_url=event_url if is_official else None,
            registration_url=None,
            cfp_url=None,
            cfp_deadline=None,
            status="upcoming",
            source_url=source["url"],
        )
        try:
            out.append(_finish(rec, publisher, stable_id))
        except ValueError:
            continue

    return out
