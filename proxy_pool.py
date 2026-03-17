#!/usr/bin/env python3
"""
Proxy pool manager for Shopee crawler.
Sources: free proxy lists + optional commercial proxy (env var).

Usage:
    pool = ProxyPool()
    await pool.refresh()
    proxy = await pool.next()   # e.g. "http://1.2.3.4:8080"
    pool.mark_bad(proxy)

Set PROXY_URL env var to use a commercial rotating proxy, e.g.:
    PROXY_URL=http://user:pass@proxy.provider.com:8080
"""

import asyncio
import logging
import os
import random
import time
from typing import Optional

import aiohttp

log = logging.getLogger(__name__)

# ── Free proxy sources (JSON APIs) ───────────────────────────────
FREE_PROXY_SOURCES = [
    # proxylist.geonode.com — large list, filterable
    "https://proxylist.geonode.com/api/proxy-list?limit=50&page=1&sort_by=lastChecked&sort_type=desc&filterByAnonymous=true&protocols=http%2Chttps&speed=fast",
    # proxyscrape
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=all&ssl=yes&anonymity=all&simplified=true",
]

TEST_URL    = "https://shopee.vn/api/v4/account/basic/get_account_info"
TEST_TIMEOUT = 8


class ProxyPool:
    def __init__(self):
        self._pool: list[str]     = []
        self._bad:  set[str]      = set()
        self._idx   = 0
        self._last_refresh = 0.0

        # Commercial proxy takes priority if set
        commercial = os.getenv("PROXY_URL", "").strip()
        if commercial:
            self._commercial = commercial
            log.info(f"Commercial proxy configured: {commercial[:30]}...")
        else:
            self._commercial = ""

    @property
    def size(self):
        return len(self._pool)

    async def refresh(self, force=False):
        """Fetch + validate proxy list. Auto-refresh every 10 min."""
        if not force and time.time() - self._last_refresh < 600:
            return
        if self._commercial:
            # Commercial rotating proxy — single entry, always valid
            self._pool = [self._commercial]
            log.info("Using commercial rotating proxy.")
            self._last_refresh = time.time()
            return

        log.info("Fetching free proxy list...")
        raw: list[str] = []

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            for src in FREE_PROXY_SOURCES:
                try:
                    async with session.get(src) as resp:
                        if "geonode" in src:
                            data = await resp.json(content_type=None)
                            for p in data.get("data", []):
                                raw.append(f"http://{p['ip']}:{p['port']}")
                        else:
                            text = await resp.text()
                            for line in text.strip().splitlines():
                                line = line.strip()
                                if ":" in line and not line.startswith("#"):
                                    raw.append(f"http://{line}")
                except Exception as e:
                    log.warning(f"Proxy source failed ({src[:50]}): {e}")

        log.info(f"  Raw proxies: {len(raw)} — validating...")
        good = await self._validate_batch(raw[:80])  # test first 80
        log.info(f"  Working proxies: {len(good)}")

        if good:
            self._pool = good
            self._bad.clear()
            random.shuffle(self._pool)
        self._last_refresh = time.time()

    async def _validate_batch(self, proxies: list[str]) -> list[str]:
        """Test proxies concurrently, return working ones."""
        sem = asyncio.Semaphore(20)
        results = []

        async def test(proxy):
            async with sem:
                ok = await self._test_proxy(proxy)
                if ok:
                    results.append(proxy)

        await asyncio.gather(*[test(p) for p in proxies], return_exceptions=True)
        return results

    async def _test_proxy(self, proxy: str) -> bool:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TEST_TIMEOUT)) as session:
                async with session.get(
                    "https://api.ipify.org",
                    proxy=proxy,
                    ssl=False,
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def next(self) -> Optional[str]:
        """Get next working proxy (round-robin, skip bad)."""
        if not self._pool:
            await self.refresh(force=True)

        if not self._pool:
            log.warning("No proxies available — running without proxy")
            return None

        for _ in range(len(self._pool)):
            proxy = self._pool[self._idx % len(self._pool)]
            self._idx += 1
            if proxy not in self._bad:
                return proxy

        # All marked bad — clear and try again
        self._bad.clear()
        return self._pool[0] if self._pool else None

    def mark_bad(self, proxy: str):
        if proxy:
            self._bad.add(proxy)
            log.warning(f"  Proxy marked bad: {proxy}")
            if len(self._bad) >= len(self._pool):
                log.warning("  All proxies bad — will refresh on next call")
                self._last_refresh = 0


# ── Playwright proxy helper ───────────────────────────────────────
def playwright_proxy(proxy_url: Optional[str]) -> Optional[dict]:
    """Convert proxy URL to Playwright proxy dict."""
    if not proxy_url:
        return None
    # http://user:pass@host:port  or  http://host:port
    from urllib.parse import urlparse
    p = urlparse(proxy_url)
    result = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
    if p.username:
        result["username"] = p.username
    if p.password:
        result["password"] = p.password
    return result
