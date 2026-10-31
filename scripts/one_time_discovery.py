#!/usr/bin/env python3
"""ONE-TIME discovery of event-page URLs from explicitly configured allowed sources.

Supported source kinds: sitemap, rss, atom, html.
No broad host crawling is performed. Every source is explicitly configured,
robots.txt is checked before retrieval, and only matching URLs are stored.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import logging
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.robotparser import RobotFileParser

EVENT_HINT = re.compile(r"(?i)(event|conference|congress|summit|symposium|expo|convention|trade[-_/ ]?show|festival|agenda|programme|program)")
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_https_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.strip())
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"Only absolute public HTTPS URLs are accepted: {value}")
    if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError(f"Local URLs are not accepted: {value}")
    return urllib.parse.urlunsplit(("https", parsed.netloc, parsed.path or "/", parsed.query, ""))


def normalize_candidate(value: str, base_url: str, allowed_host: str | None) -> str | None:
    absolute = urllib.parse.urljoin(base_url, html.unescape(value.strip()))
    parsed = urllib.parse.urlsplit(absolute)
    host = (parsed.hostname or "").lower().rstrip(".")
    allowed = allowed_host.lower().rstrip(".") if allowed_host else None
    if parsed.scheme.lower() != "https" or not host or (allowed and not (host == allowed or host.endswith("." + allowed))):
        return None
    # Event page URLs should be canonical page links, not tracking variants.
    return urllib.parse.urlunsplit(("https", parsed.netloc.lower(), parsed.path or "/", "", ""))


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                self._href = value
                self._text = []
                break

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append((self._href, " ".join(" ".join(self._text).split())))
            self._href = None
            self._text = []


class Fetcher:
    def __init__(self, user_agent: str, timeout: int, min_delay: float) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.min_delay = min_delay
        self.robots: dict[str, RobotFileParser] = {}
        self.last_request: dict[str, float] = {}

    def _robots_for(self, url: str) -> RobotFileParser:
        host = urllib.parse.urlsplit(url).netloc.lower()
        if host in self.robots:
            return self.robots[host]
        robots_url = f"https://{host}/robots.txt"
        req = urllib.request.Request(robots_url, headers={"User-Agent": self.user_agent})
        parser = RobotFileParser(robots_url)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                if response.status == 200:
                    parser.parse(response.read().decode("utf-8", "replace").splitlines())
                else:
                    parser.parse([])
        except urllib.error.HTTPError as exc:
            # A missing robots file is conventionally treated as no published rules.
            # Deny on explicit authorization/access errors rather than probing further.
            if exc.code in (401, 403):
                parser.parse(["User-agent: *", "Disallow: /"])
            elif exc.code == 404:
                parser.parse([])
            else:
                raise RuntimeError(f"Cannot verify robots.txt for {host}: HTTP {exc.code}") from exc
        except Exception as exc:
            raise RuntimeError(f"Cannot verify robots.txt for {host}: {exc}") from exc
        self.robots[host] = parser
        return parser

    def allowed(self, url: str) -> bool:
        parser = self._robots_for(url)
        return parser.can_fetch(self.user_agent, url)

    def fetch(self, url: str) -> tuple[bytes, str]:
        if not self.allowed(url):
            raise PermissionError(f"robots.txt disallows retrieval: {url}")
        parsed = urllib.parse.urlsplit(url)
        host = parsed.netloc.lower()
        delay = max(self.min_delay, self.robots[host].crawl_delay(self.user_agent) or 0)
        elapsed = time.monotonic() - self.last_request.get(host, 0)
        if elapsed < delay:
            time.sleep(delay - elapsed)
        req = urllib.request.Request(
            url,
            headers={"User-Agent": self.user_agent, "Accept": "application/xml,text/xml,application/rss+xml,application/atom+xml,text/html,*/*"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            final = urllib.parse.urlsplit(response.geturl())
            if final.scheme != "https" or final.hostname != parsed.hostname:
                raise RuntimeError(f"Refusing unexpected redirect from {url} to {response.geturl()}")
            body = response.read()
            content_type = response.headers.get_content_type()
        self.last_request[host] = time.monotonic()
        return body, content_type


def allowed_pattern(url: str, include_pattern: str | None, label: str = "") -> bool:
    if include_pattern:
        return re.search(include_pattern, url + "\n" + label) is not None
    return EVENT_HINT.search(urllib.parse.urlsplit(url).path + " " + label) is not None


def collect_sitemap(fetcher: Fetcher, source_url: str, allowed_host: str, pattern: str | None,
                    max_sitemaps: int, max_urls: int) -> dict[str, str | None]:
    found: dict[str, str | None] = {}
    seen_maps: set[str] = set()
    queue = [source_url]
    while queue:
        sitemap_url = queue.pop(0)
        if sitemap_url in seen_maps:
            continue
        seen_maps.add(sitemap_url)
        if len(seen_maps) > max_sitemaps:
            raise RuntimeError(f"Sitemap recursion exceeded limit ({max_sitemaps})")
        body, _ = fetcher.fetch(sitemap_url)
        root = ET.fromstring(body)
        root_tag = root.tag.rsplit("}", 1)[-1].lower()
        if root_tag == "sitemapindex":
            for loc in root.findall("sm:sitemap/sm:loc", SITEMAP_NS):
                if not loc.text:
                    continue
                child = safe_https_url(loc.text)
                host = (urllib.parse.urlsplit(child).hostname or "").lower()
                if host == allowed_host or host.endswith("." + allowed_host):
                    queue.append(child)
        elif root_tag == "urlset":
            for item in root.findall("sm:url", SITEMAP_NS):
                loc = item.findtext("sm:loc", default="", namespaces=SITEMAP_NS).strip()
                lastmod = item.findtext("sm:lastmod", default="", namespaces=SITEMAP_NS).strip() or None
                candidate = normalize_candidate(loc, sitemap_url, allowed_host)
                if candidate and allowed_pattern(candidate, pattern):
                    found[candidate] = lastmod
                    if len(found) >= max_urls:
                        return found
        else:
            raise RuntimeError(f"Unsupported sitemap root element {root_tag!r} at {sitemap_url}")
    return found


def collect_feed(fetcher: Fetcher, source_url: str, allowed_host: str, pattern: str | None,
                 max_urls: int, allow_external: bool = False) -> dict[str, str | None]:
    if allow_external and not pattern:
        raise ValueError("allow_external_links requires an explicit include_pattern")
    body, _ = fetcher.fetch(source_url)
    root = ET.fromstring(body)
    found: dict[str, str | None] = {}
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1].lower() != "link":
            continue
        value = node.attrib.get("href") or (node.text or "").strip()
        if not value:
            continue
        candidate = normalize_candidate(value, source_url, None if allow_external else allowed_host)
        if candidate and allowed_pattern(candidate, pattern):
            found[candidate] = None
            if len(found) >= max_urls:
                break
    return found


def collect_html(fetcher: Fetcher, source_url: str, allowed_host: str, pattern: str | None,
                 max_urls: int, allow_external: bool = False) -> dict[str, str | None]:
    if allow_external and not pattern:
        raise ValueError("allow_external_links requires an explicit include_pattern")
    body, content_type = fetcher.fetch(source_url)
    if content_type not in ("text/html", "application/xhtml+xml"):
        raise RuntimeError(f"Expected an HTML page, got {content_type}")
    parser = LinkParser()
    parser.feed(body.decode("utf-8", "replace"))
    found: dict[str, str | None] = {}
    for href, label in parser.links:
        candidate = normalize_candidate(href, source_url, None if allow_external else allowed_host)
        if candidate and allowed_pattern(candidate, pattern, label):
            found[candidate] = None
            if len(found) >= max_urls:
                break
    return found


def initialize(db: sqlite3.Connection) -> None:
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""
        CREATE TABLE IF NOT EXISTS event_url_candidates (
            url TEXT PRIMARY KEY,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            source_count INTEGER NOT NULL DEFAULT 1,
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS source_observations (
            source_name TEXT NOT NULL,
            source_url TEXT NOT NULL,
            event_url TEXT NOT NULL,
            source_lastmod TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            is_listed INTEGER NOT NULL DEFAULT 1 CHECK (is_listed IN (0, 1)),
            PRIMARY KEY (source_name, event_url)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL,
            source_url TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            status TEXT NOT NULL,
            url_count INTEGER NOT NULL DEFAULT 0,
            error TEXT
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS ix_candidate_active ON event_url_candidates(is_active)")
    db.execute("CREATE INDEX IF NOT EXISTS ix_observations_active ON source_observations(is_listed)")
    db.commit()


def sync_source(db: sqlite3.Connection, fetcher: Fetcher, source: dict, limits: dict) -> dict:
    started = now_utc()
    name = source["name"]
    source_url = safe_https_url(source["url"])
    allowed_host = (urllib.parse.urlsplit(source_url).hostname or "").lower()
    pattern = source.get("include_pattern") or None
    allow_external = bool(source.get("allow_external_links", False))
    kind = source["kind"].lower()
    try:
        if kind == "sitemap":
            results = collect_sitemap(fetcher, source_url, allowed_host, pattern,
                                      limits["max_sitemaps_per_source"], limits["max_urls_per_source"])
        elif kind in ("rss", "atom"):
            results = collect_feed(fetcher, source_url, allowed_host, pattern, limits["max_urls_per_source"], allow_external)
        elif kind == "html":
            results = collect_html(fetcher, source_url, allowed_host, pattern, limits["max_urls_per_source"], allow_external)
        else:
            raise ValueError(f"Unsupported source kind {kind!r}; use sitemap, rss, atom, or html")

        if not results:
            raise RuntimeError("Source produced no matching URLs; preserving existing observations")

        stamp = now_utc()
        with db:
            db.execute("UPDATE source_observations SET is_listed = 0 WHERE source_name = ?", (name,))
            for url, lastmod in results.items():
                db.execute("""
                    INSERT INTO source_observations
                        (source_name, source_url, event_url, source_lastmod, first_seen_at, last_seen_at, is_listed)
                    VALUES (?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(source_name, event_url) DO UPDATE SET
                        source_url = excluded.source_url,
                        source_lastmod = excluded.source_lastmod,
                        last_seen_at = excluded.last_seen_at,
                        is_listed = 1
                """, (name, source_url, url, lastmod, stamp, stamp))
                db.execute("""
                    INSERT INTO event_url_candidates (url, first_seen_at, last_seen_at, source_count, is_active)
                    VALUES (?, ?, ?, 1, 1)
                    ON CONFLICT(url) DO UPDATE SET last_seen_at = excluded.last_seen_at, is_active = 1
                """, (url, stamp, stamp))
            db.execute("""
                UPDATE event_url_candidates SET is_active = EXISTS (
                    SELECT 1 FROM source_observations so
                    WHERE so.event_url = event_url_candidates.url AND so.is_listed = 1
                )
            """)
            db.execute("""
                UPDATE event_url_candidates SET source_count = (
                    SELECT COUNT(DISTINCT so.source_name) FROM source_observations so
                    WHERE so.event_url = event_url_candidates.url AND so.is_listed = 1
                )
            """)
            completed = now_utc()
            db.execute("""
                INSERT INTO sync_runs (source_name, source_url, started_at, completed_at, status, url_count)
                VALUES (?, ?, ?, ?, 'ok', ?)
            """, (name, source_url, started, completed, len(results)))
        return {"source": name, "status": "ok", "urls": len(results), "error": None}
    except Exception as exc:
        completed = now_utc()
        with db:
            db.execute("""
                INSERT INTO sync_runs (source_name, source_url, started_at, completed_at, status, error)
                VALUES (?, ?, ?, ?, 'error', ?)
            """, (name, source_url, started, completed, str(exc)[:1000]))
        return {"source": name, "status": "error", "urls": 0, "error": str(exc)}


def export_csv(db_path: Path, dest: Path) -> None:
    with sqlite3.connect(db_path) as db, dest.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["url", "first_seen_at", "last_seen_at", "source_count"])
        writer.writerows(db.execute("""
            SELECT url, first_seen_at, last_seen_at, source_count
            FROM event_url_candidates WHERE is_active = 1 ORDER BY url
        """))


def run(config_path: Path, db_path: Path, timeout_override: int | None = None) -> int:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    defaults = config.get("defaults", {})
    sources = [s for s in config.get("sources", []) if s.get("enabled", True)]
    # These supplied research directories are leads only; do not poll them here.
    from urllib.parse import urlsplit
    for source in sources:
        host = (urlsplit(source.get("url", "")).hostname or "").lower().removeprefix("www.")
        if any(host == b or host.endswith("." + b) for b in ("conferencegrid.com", "sessionize.com")):
            raise PermissionError("Do not fetch ConferenceGrid or Sessionize using this discovery collector")
    if not sources:
        raise ValueError("No enabled sources configured. Add explicit public source indexes to the config.")
    user_agent = defaults.get("user_agent", "ConferenceURLCatalog/1.0 (+replace-with-contact-email)")
    timeout = timeout_override or int(defaults.get("timeout_seconds", 30))
    min_delay = float(defaults.get("min_delay_seconds", 2.0))
    limits = {
        "max_sitemaps_per_source": int(defaults.get("max_sitemaps_per_source", 100)),
        "max_urls_per_source": int(defaults.get("max_urls_per_source", 50000)),
    }
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path, timeout=30) as db:
        initialize(db)
        fetcher = Fetcher(user_agent, timeout, min_delay)
        results = [sync_source(db, fetcher, source, limits) for source in sources]
        for result in results:
            if result["status"] == "ok":
                logging.info("%s: %s URLs", result["source"], result["urls"])
            else:
                logging.error("%s failed: %s", result["source"], result["error"])
        counts = db.execute("SELECT COUNT(*) FROM event_url_candidates WHERE is_active = 1").fetchone()[0]
        logging.info("Active distinct URLs across configured sources: %s", counts)
        return 0 if any(r["status"] == "ok" for r in results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="ONE-TIME discovery from authorized indexes into SQLite.")
    parser.add_argument("--config", type=Path, default=Path("sources.json"), help="JSON source configuration")
    parser.add_argument("--db", type=Path, default=Path("conference_url_candidates.sqlite"), help="SQLite database path")
    parser.add_argument("--timeout", type=int, help="Override per-request timeout in seconds")
    parser.add_argument("--export-csv", type=Path, help="Export active URLs after a successful sync")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        result = run(args.config, args.db, args.timeout)
        if result == 0 and args.export_csv:
            export_csv(args.db, args.export_csv)
            logging.info("Exported review-only URLs to %s", args.export_csv)
        return result
    except Exception as exc:
        logging.exception("One-time discovery failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
