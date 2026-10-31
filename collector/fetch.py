"""Small, fail-closed HTTP fetcher with robots, size, and private-network guards."""
from __future__ import annotations

import ipaddress
import logging
import socket
import time
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests

LOG = logging.getLogger(__name__)


def host_of(url: str) -> str:
    p = urlsplit(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.port not in (None, 443):
        raise ValueError(f"HTTPS and normal public hostname required: {url}")
    hostname = p.hostname.lower().rstrip(".")
    if hostname == "localhost" or "." not in hostname:
        raise ValueError(f"Refusing local/non-public host: {hostname}")
    try:
        ipaddress.ip_address(hostname)
        raise ValueError("Direct IP literals are not permitted")
    except ValueError as exc:
        if "Direct IP" in str(exc):
            raise
    # Reject non-public DNS results. Network routes are still subject to provider safeguards.
    ips = {item[4][0] for item in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)}
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise ValueError(f"Refusing non-public DNS destination: {hostname}")
    return hostname


class Fetcher:
    def __init__(self, user_agent: str, min_delay_seconds: float = 2.0, timeout: float = 25.0):
        if not user_agent or "YOUR_EMAIL" in user_agent:
            raise ValueError("Set a real COLLECTOR_USER_AGENT with contact email")
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": user_agent, "Accept": "text/calendar,application/xml,text/xml,text/html,*/*"})
        self.min_delay = max(float(min_delay_seconds), 1.0)
        self.timeout = timeout
        self.robots: dict[str, RobotFileParser] = {}
        self.last_fetch: dict[str, float] = {}

    def _get_robots(self, hostname: str) -> RobotFileParser:
        if hostname in self.robots:
            return self.robots[hostname]
        url = f"https://{hostname}/robots.txt"
        host_of(url)
        response = self.session.get(url, timeout=self.timeout, allow_redirects=False)
        if response.status_code in (401, 403):
            raise PermissionError(f"Robots policy denies access at {hostname}")
        if response.status_code == 404:
            lines = []
        elif response.status_code == 200:
            if len(response.content) > 128 * 1024:
                raise ValueError("Robots response unexpectedly large")
            lines = response.text.splitlines()
        else:
            raise RuntimeError(f"Cannot verify robots.txt for {hostname}: {response.status_code}")
        policy = RobotFileParser(url)
        policy.parse(lines)
        self.robots[hostname] = policy
        return policy

    def fetch(self, url: str, etag: str | None = None, last_modified: str | None = None):
        hostname = host_of(url)
        policy = self._get_robots(hostname)
        agent = self.session.headers["User-Agent"]
        if not policy.can_fetch(agent, url):
            raise PermissionError(f"robots.txt blocks {url}")
        delay = max(self.min_delay, float(policy.crawl_delay(agent) or 0))
        prev = self.last_fetch.get(hostname, 0)
        if prev and time.monotonic() - prev < delay:
            time.sleep(delay - (time.monotonic() - prev))
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        try:
            for attempt in range(3):
                response = self.session.get(url, headers=headers, timeout=self.timeout,
                                            allow_redirects=False, stream=True)
                if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    response.close()
                    time.sleep(min(2 ** attempt * 3, 15))
                    continue
                break
            if response.status_code in (301, 302, 303, 307, 308):
                # Do not silently follow cross-domain or extra redirects.
                response.close()
                raise RuntimeError(f"Redirect must be reviewed and configured: {url}")
            if response.status_code == 304:
                response.close()
                return 304, b"", response.headers
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=65536):
                size += len(chunk)
                if size > 8 * 1024 * 1024:
                    raise RuntimeError("Source response exceeds 8 MB safety limit")
                chunks.append(chunk)
            return response.status_code, b"".join(chunks), response.headers
        finally:
            self.last_fetch[hostname] = time.monotonic()
            if "response" in locals():
                response.close()
