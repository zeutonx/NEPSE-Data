"""Polite HTTP client for www.sharesansar.com.

- Checks robots.txt before every new path.
- Waits a minimum delay between requests (default 2 s).
- Retries transient failures with exponential backoff.
"""
from __future__ import annotations

import logging
import time
import urllib.robotparser
from typing import Any

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.sharesansar.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 NEPSE-Data-research"
)

log = logging.getLogger(__name__)


class RobotsDisallowed(Exception):
    pass


class SharesansarClient:
    def __init__(self, delay: float = 2.0, retries: int = 3, timeout: float = 30.0):
        self.delay = delay
        self.retries = retries
        self.timeout = timeout
        self._last = 0.0
        self._csrf: str | None = None
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        self.robots = urllib.robotparser.RobotFileParser()
        self.robots.set_url(f"{BASE_URL}/robots.txt")
        try:
            resp = self._raw("GET", f"{BASE_URL}/robots.txt")
            self.robots.parse(resp.text.splitlines())
        except requests.RequestException as exc:  # be conservative but usable
            log.warning("could not read robots.txt (%s); assuming allowed", exc)
            self.robots.parse([])

    # ------------------------------------------------------------------ core
    def _throttle(self) -> None:
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def _raw(self, method: str, url: str, **kw: Any) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(self.retries):
            self._throttle()
            try:
                resp = self.session.request(method, url, timeout=self.timeout, **kw)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
                resp.raise_for_status()
                return resp
            except requests.RequestException as exc:
                last_exc = exc
                backoff = self.delay * (2 ** attempt)
                log.warning("%s %s failed (%s); retry in %.0fs", method, url, exc, backoff)
                time.sleep(backoff)
        assert last_exc is not None
        raise last_exc

    def request(self, method: str, path: str, ajax: bool = False, **kw: Any) -> requests.Response:
        url = path if path.startswith("http") else f"{BASE_URL}{path}"
        if not self.robots.can_fetch(USER_AGENT, url):
            raise RobotsDisallowed(url)
        headers = kw.pop("headers", {})
        if ajax:
            headers.setdefault("X-Requested-With", "XMLHttpRequest")
            headers.setdefault("Accept", "application/json, text/javascript, */*; q=0.01")
        log.debug("%s %s", method, url)
        return self._raw(method, url, headers=headers, **kw)

    # --------------------------------------------------------------- helpers
    def soup(self, path: str, **kw: Any) -> BeautifulSoup:
        resp = self.request("GET", path, **kw)
        soup = BeautifulSoup(resp.text, "lxml")
        self._remember_csrf(soup)
        return soup

    def json(self, path: str, method: str = "GET", **kw: Any) -> Any:
        return self.request(method, path, ajax=True, **kw).json()

    def _remember_csrf(self, soup: BeautifulSoup) -> None:
        tag = soup.find("meta", attrs={"name": "_token"}) or soup.find(
            "meta", attrs={"name": "csrf-token"}
        )
        if tag and tag.get("content"):
            self._csrf = tag["content"]
            return
        inp = soup.find("input", attrs={"name": "_token"})
        if inp and inp.get("value"):
            self._csrf = inp["value"]

    @property
    def csrf(self) -> str:
        if not self._csrf:
            self.soup("/today-share-price")
        if not self._csrf:
            raise RuntimeError("could not find CSRF token on sharesansar.com")
        return self._csrf
