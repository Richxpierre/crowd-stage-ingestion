"""ICS and official event-page JSON-LD adapters (no arbitrary page scraping)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
from html import unescape
import json
import re
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup


def clean_url(url: str | None) -> str | None:
    if not url:
        return None
    value = unescape(str(url)).strip()
    p = urlsplit(value)
    if p.scheme.lower() != "https" or not p.hostname or p.username or p.password:
        return None
    # Discard tracking query parameters; keep meaningful query params.
    safe_query = "&".join(
        part for part in p.query.split("&")
        if part and not part.lower().startswith(("utm_", "fbclid=", "gclid=", "cmp=", "src="))
    )
    return urlunsplit(("https", p.netloc.lower(), p.path or "/", safe_query, ""))


def plain(value: object, maxlen: int = 1200) -> str | None:
    if value is None:
        return None
    s = re.sub(r"\s+", " ", str(value)).strip()
    return s[:maxlen] if s else None


def date_part(value: object) -> str | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    # ISO-8601 datetime or YYYY-MM-DD, including a timezone suffix.
    match = re.match(r"^(\d{4}-\d{2}-\d{2})", str(value))
    return match.group(1) if match else None


def _hash(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _finish(record: dict, publisher: str, event_id: str) -> dict:
    if not record.get("title") or not record.get("start_date"):
        raise ValueError("Event must provide a title and start date")
    start = date.fromisoformat(record["start_date"])
    end = date.fromisoformat(record.get("end_date") or record["start_date"])
    if end < start or (end - start).days > 366:
        raise ValueError("Event has invalid or suspicious date range")
    record["end_date"] = end.isoformat()
    record["source_record_id"] = event_id
    # Keep source data isolated by publisher; ICS UIDs are shared across category feeds.
    record["dedupe_key"] = hashlib.sha256(f"{publisher.lower()}|{event_id}".encode()).hexdigest()
    record["content_hash"] = _hash({k: v for k, v in record.items() if k not in ("source_url", "content_hash")})
    return record


def _unescape_ics(value: str) -> str:
    # RFC 5545 TEXT escaping. Unescape backslash last.
    return (value.replace(r"\N", "\n").replace(r"\n", "\n")
            .replace(r"\,", ",").replace(r"\;", ";")
            .replace(chr(92) * 2, chr(92)))


def _ical_events(body: bytes):
    """Read folded VEVENT properties (minimal RFC 5545 text/date subset)."""
    lines: list[str] = []
    for line in body.decode("utf-8-sig", "replace").replace("\r\n", "\n").split("\n"):
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    properties = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            properties = {}
        elif line == "END:VEVENT" and properties is not None:
            yield properties
            properties = None
        elif properties is not None and ":" in line:
            key, value = line.split(":", 1)
            name = key.split(";", 1)[0].upper()
            properties[name] = (key, value)


def _ics_date(value: str | None) -> str | None:
    if not value:
        return None
    match = re.match(r"^(\d{4})(\d{2})(\d{2})", value)
    if not match:
        return None
    try:
        return date(int(match[1]), int(match[2]), int(match[3])).isoformat()
    except ValueError:
        return None


def parse_ics(body: bytes, source: dict) -> list[dict]:
    out: list[dict] = []
    for item in _ical_events(body):
        def raw(name: str) -> str:
            return item.get(name, ("", ""))[1]
        uid = plain(_unescape_ics(raw("UID")), 500)
        title = plain(_unescape_ics(raw("SUMMARY")), 240)
        start = _ics_date(raw("DTSTART"))
        if not uid or not title or not start:
            continue
        end = _ics_date(raw("DTEND")) or start
        # RFC 5545 DATE-only DTEND is exclusive; DATETIME is not.
        dtkey = item.get("DTSTART", ("", ""))[0].upper()
        if "DTEND" in item and ("VALUE=DATE" in dtkey or len(raw("DTSTART")) == 8):
            end = (date.fromisoformat(end) - timedelta(days=1)).isoformat()
        tzmatch = re.search(r"TZID=([^;:]+)", dtkey, flags=re.I)
        categories = [plain(_unescape_ics(x), 60)
                      for x in re.split(r"(?<!\\),", raw("CATEGORIES")) if x]
        event_url = clean_url(_unescape_ics(raw("URL")))
        rec = dict(
            title=title, start_date=start, end_date=end,
            timezone=tzmatch.group(1) if tzmatch else ("UTC" if raw("DTSTART").endswith("Z") else None),
            venue=plain(_unescape_ics(raw("LOCATION")), 240),
            city=None, region=None, country=None,
            format="unknown", categories=[x for x in categories if x],
            summary=plain(_unescape_ics(raw("DESCRIPTION")), 1200),
            event_url=event_url,
            official_url=event_url if source.get("is_official_source") else None,
            registration_url=None, cfp_url=None, cfp_deadline=None,
            status="cancelled" if raw("STATUS").upper() == "CANCELLED" else "upcoming",
            source_url=source["url"],
        )
        try:
            out.append(_finish(rec, source["publisher"], uid))
        except ValueError:
            continue
    return out


def _ld_nodes(value: object):
    if isinstance(value, list):
        for x in value:
            yield from _ld_nodes(x)
    elif isinstance(value, dict):
        if "@graph" in value:
            yield from _ld_nodes(value["@graph"])
        yield value


def _is_event(node: dict) -> bool:
    kinds = node.get("@type", [])
    if isinstance(kinds, str):
        kinds = [kinds]
    return any(str(x).split("/")[-1].endswith("Event") for x in kinds)


def _extract_location(loc: object) -> tuple[str | None, str | None, str | None, str | None]:
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    if isinstance(loc, str):
        return plain(loc, 240), None, None, None
    if not isinstance(loc, dict):
        return None, None, None, None
    address = loc.get("address", {})
    if isinstance(address, str):
        return plain(loc.get("name") or address, 240), None, None, None
    if not isinstance(address, dict):
        address = {}
    return (plain(loc.get("name"), 240), plain(address.get("addressLocality"), 100),
            plain(address.get("addressRegion"), 100), plain(address.get("addressCountry"), 100))


def _as_url(value: object) -> str | None:
    if isinstance(value, dict):
        value = value.get("url") or value.get("@id")
    return clean_url(str(value)) if value else None


def parse_jsonld(body: bytes, source: dict, page_url: str | None = None) -> list[dict]:
    page_url = clean_url(page_url or source["url"])
    soup = BeautifulSoup(body, "html.parser")
    records = []
    for script in soup.find_all("script", type=re.compile("^application/ld\\+json", re.I)):
        if not script.string and not script.get_text():
            continue
        try:
            payload = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        for node in _ld_nodes(payload):
            if not _is_event(node):
                continue
            title = plain(node.get("name"), 240)
            start = date_part(node.get("startDate"))
            if not title or not start:
                continue
            venue, city, region, country = _extract_location(node.get("location"))
            attendance = str(node.get("eventAttendanceMode", ""))
            fmt = "hybrid" if "MixedEventAttendanceMode" in attendance else (
                "online" if "OnlineEventAttendanceMode" in attendance else (
                "in_person" if "OfflineEventAttendanceMode" in attendance else "unknown"))
            state = str(node.get("eventStatus", ""))
            categories = node.get("keywords") or []
            if isinstance(categories, str):
                categories = re.split(r"[,;]", categories)
            elif not isinstance(categories, list):
                categories = []
            offers = node.get("offers")
            if isinstance(offers, list):
                offers = offers[0] if offers else None
            event_url = _as_url(node.get("url")) or page_url
            rec = dict(
                title=title, start_date=start, end_date=date_part(node.get("endDate")) or start,
                timezone=None, venue=venue, city=city, region=region, country=country,
                format=fmt, categories=[plain(c, 60) for c in categories if plain(c, 60)][:15],
                summary=plain(node.get("description"), 1200), event_url=event_url,
                official_url=event_url if source.get("is_official_source") else None,
                registration_url=_as_url(offers), cfp_url=None, cfp_deadline=None,
                status="cancelled" if "EventCancelled" in state else "upcoming",
                source_url=page_url,
            )
            # Prefer @id; otherwise keep annual editions distinct even on evergreen pages.
            stable_id = plain(node.get("@id"), 500) or f"{event_url}|{start[:4]}|{title.lower()}"
            try:
                records.append(_finish(rec, source["publisher"], stable_id))
            except ValueError:
                continue
    return records
