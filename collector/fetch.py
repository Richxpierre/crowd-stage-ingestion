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
LAST_FETCH = {}  # One host-wide limiter shared across sources in this worker process.

class UnsafeDestination(ValueError):
    """A destination that must never trigger alternate-host recovery."""

class RobotsPathDenied(PermissionError):
    """This path is disallowed; another path still needs its own robots check."""


def host_of(url: str) -> str:
    p = urlsplit(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.port not in (None, 443):
        raise UnsafeDestination(f"HTTPS and normal public hostname required: {url}")
    hostname = p.hostname.lower().rstrip(".")
    if hostname == "localhost" or "." not in hostname:
        raise UnsafeDestination(f"Refusing local/non-public host: {hostname}")
    try:
        ipaddress.ip_address(hostname)
        raise UnsafeDestination("Direct IP literals are not permitted")
    except ValueError as exc:
        if "Direct IP" in str(exc):
            raise
    # Reject non-public DNS results. Network routes are still subject to provider safeguards.
    ips = {item[4][0] for item in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)}
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise UnsafeDestination(f"Refusing non-public DNS destination: {hostname}")
    return hostname


class Fetcher:
    def __init__(self, user_agent: str, min_delay_seconds: float = 2.0, timeout: float = 25.0, respect_robots: bool = True):
        if not user_agent or "YOUR_EMAIL" in user_agent:
            raise ValueError("Set a real COLLECTOR_USER_AGENT with contact email")
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": user_agent, "Accept": "text/calendar,application/xml,text/xml,text/html,*/*"})
        self.min_delay = max(float(min_delay_seconds), 1.0)
        self.timeout = timeout
        self.robots: dict[str, RobotFileParser] = {}
        self.last_fetch = LAST_FETCH
        self.deadline = None
        self.respect_robots = respect_robots

    def _timeout(self):
        remaining = self.deadline - time.monotonic() if self.deadline else self.timeout
        if remaining <= 0:
            raise TimeoutError('Website collection reached its two-minute time budget')
        return min(self.timeout, remaining)

    def _get_robots(self, hostname: str) -> RobotFileParser:
        if hostname in self.robots:
            return self.robots[hostname]
        url = f"https://{hostname}/robots.txt"
        host_of(url)
        for attempt in range(5):
            host_of(url)
            response = self.session.get(url, timeout=self._timeout(), allow_redirects=False, stream=True)
            if response.status_code not in (301,302,303,307,308):
                break
            target = urljoin(url,response.headers.get('Location',''))
            response.close()
            from .automatic import same_site
            if attempt==4 or not same_site(target,f'https://{hostname}/') or urlsplit(target).path!='/robots.txt':
                raise PermissionError(f'Cannot safely verify redirected robots.txt for {hostname}')
            url=target
        if response.status_code in (401, 403):
            raise PermissionError(f"Robots policy denies access at {hostname}")
        if response.status_code == 404:
            lines = []
        elif response.status_code == 200:
            data = bytearray()
            for chunk in response.iter_content(8192):
                self._timeout()
                data.extend(chunk)
                if len(data) > 128 * 1024:
                    response.close()
                    raise ValueError("Robots response unexpectedly large")
            lines = data.decode('utf-8','replace').splitlines()
        else:
            raise RuntimeError(f"Cannot verify robots.txt for {hostname}: {response.status_code}")
        response.close()
        policy = RobotFileParser(url)
        policy.parse(lines)
        self.robots[hostname] = policy
        return policy

    def fetch(self, url: str, etag: str | None = None, last_modified: str | None = None, _redirects=0, _root=None):
        self._timeout()
        hostname = host_of(url)
        policy = self._get_robots(hostname) if self.respect_robots else None
        agent = self.session.headers["User-Agent"]
        if policy and not policy.can_fetch(agent, url):
            raise RobotsPathDenied(f"robots.txt blocks {url}")
        delay = max(self.min_delay, float(policy.crawl_delay(agent) or 0) if policy else 0)
        rate_host = hostname.removeprefix('www.')
        prev = self.last_fetch.get(rate_host, 0)
        if prev and time.monotonic() - prev < delay:
            wait = delay - (time.monotonic() - prev)
            if self.deadline and time.monotonic()+wait >= self.deadline:
                raise TimeoutError('Website crawl delay exceeds remaining collection time')
            time.sleep(wait)
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        try:
            for attempt in range(3):
                response = self.session.get(url, headers=headers, timeout=self._timeout(),
                                            allow_redirects=False, stream=True)
                if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    response.close()
                    time.sleep(min(2 ** attempt * 3, 15))
                    continue
                break
            if response.status_code in (301, 302, 303, 307, 308):
                target = urljoin(url, response.headers.get('Location',''))
                root = _root or url
                from .automatic import same_site
                if _redirects >= 4 or not same_site(target, root):
                    response.close()
                    raise RuntimeError(f'Redirect outside approved website or redirect loop: {url}')
                # Every redirect rechecks public DNS, robots, rate limits and HTTPS.
                self.last_fetch[rate_host] = time.monotonic()
                response.close()
                return self.fetch(target, etag, last_modified, _redirects+1, root)
            if response.status_code == 304:
                response.close()
                return 304, b"", response.headers
            if response.status_code in (401,403):
                provider='; Cloudflare' if 'cloudflare' in response.headers.get('Server','').lower() or response.headers.get('cf-mitigated')=='challenge' else ''
                raise PermissionError(f'Website access denied (HTTP {response.status_code}{provider}) at {url}. This is a page access block, not a robots.txt rule.')
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=65536):
                self._timeout()
                size += len(chunk)
                if size > 8 * 1024 * 1024:
                    raise RuntimeError("Source response exceeds 8 MB safety limit")
                chunks.append(chunk)
            result_headers = dict(response.headers)
            result_headers['X-CrowdStage-Final-URL'] = url
            return response.status_code, b"".join(chunks), result_headers
        finally:
            self.last_fetch[rate_host] = time.monotonic()
            if "response" in locals():
                response.close()

