"""
NSE session manager — hardened for institutional use.

NSE protects its JSON APIs with a multi-layer bot-detection stack:
  1. TLS fingerprint (JA3/JA4) — vanilla requests gets 403 at the door.
  2. Cookie chain — needs nsit/nseappid/bm_sv before /api/* will respond.
  3. Brotli-compressed responses by default for modern browsers.

We solve all three with `curl_cffi`, which uses libcurl-impersonate to
present a real Chrome TLS+HTTP2 fingerprint and decompresses brotli
natively.
"""

from __future__ import annotations

import os
import time
import logging
from typing import Any

try:
    from curl_cffi import requests as cffi_requests  # type: ignore
    _HAS_CURL_CFFI = True
except ImportError:
    _HAS_CURL_CFFI = False
    cffi_requests = None  # type: ignore

import requests as _requests
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception, before_sleep_log,
)

from config import NSE_BASE_URL, NSE_HEADERS, MAX_RETRIES, REQUEST_DELAY_SECONDS

logger = logging.getLogger(__name__)

_WARM_UP_URLS = [
    f"{NSE_BASE_URL}/",
    f"{NSE_BASE_URL}/option-chain",
    f"{NSE_BASE_URL}/market-data/large-deals",
    f"{NSE_BASE_URL}/companies-listing/corporate-filings-announcements",
    f"{NSE_BASE_URL}/companies-listing/corporate-filings-corporate-actions",
]

_REQUIRED_COOKIES    = ("nsit", "nseappid", "bm_sv", "ak_bmsc")
SESSION_REFRESH_EVERY = 50
WARMUP_MAX_ATTEMPTS   = 3
_RETRYABLE_STATUS     = frozenset({429, 500, 502, 503, 504})


def _is_retryable(exc: BaseException) -> bool:
    resp = getattr(exc, "response", None)
    if resp is not None and hasattr(resp, "status_code"):
        return resp.status_code in _RETRYABLE_STATUS
    return isinstance(exc, (_requests.ConnectionError, _requests.Timeout, TimeoutError))


class NSESessionWarmupError(RuntimeError):
    pass


class NSESession:
    def __init__(self, impersonate: str = "chrome124"):
        self._impersonate = impersonate
        self._session = self._build_session()
        self._request_count = 0
        proxy_url = os.environ.get("NSE_PROXY_URL")
        if proxy_url:
            self._session.proxies = {"http": proxy_url, "https": proxy_url}
        self._warm_up(strict=True)

    def _build_session(self):
        if _HAS_CURL_CFFI:
            sess = cffi_requests.Session(impersonate=self._impersonate)
            base = {k: v for k, v in NSE_HEADERS.items()
                    if k.lower() not in {"user-agent", "accept-encoding"}}
            sess.headers.update(base)
            return sess
        logger.warning("curl_cffi not installed — NSE bot detection will likely 403 you.")
        sess = _requests.Session()
        sess.headers.update(NSE_HEADERS)
        return sess

    def _have_required_cookies(self) -> bool:
        return any(name in self._session.cookies for name in _REQUIRED_COOKIES)

    def _warm_up(self, strict: bool = False):
        last_error = None
        for attempt in range(1, WARMUP_MAX_ATTEMPTS + 1):
            for url in _WARM_UP_URLS:
                try:
                    resp = self._session.get(url, timeout=20)
                    if resp.status_code < 400:
                        time.sleep(0.6)
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    logger.debug("Warmup %s failed: %s", url, exc)
            if self._have_required_cookies():
                logger.info("NSE session warmed up (attempt %d)", attempt)
                return
            time.sleep(2 ** attempt)

        msg = f"NSE warmup failed after {WARMUP_MAX_ATTEMPTS} attempts. Last error: {last_error}"
        if strict:
            raise NSESessionWarmupError(msg)
        logger.warning(msg)

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def get(self, url: str, **kwargs):
        self._request_count += 1
        if self._request_count % SESSION_REFRESH_EVERY == 0:
            self._warm_up(strict=False)
        time.sleep(REQUEST_DELAY_SECONDS)
        kwargs.setdefault("timeout", 25)
        resp = self._session.get(url, **kwargs)
        if resp.status_code in (401, 403):
            logger.warning("Got %d on %s — re-warming", resp.status_code, url)
            self._warm_up(strict=False)
            resp = self._session.get(url, **kwargs)
        resp.raise_for_status()
        return resp

    def get_json(self, url: str, **kwargs) -> Any:
        resp = self.get(url, **kwargs)
        body = resp.text
        if not body or body.lstrip()[:1] not in ("{", "["):
            raise ValueError(f"Non-JSON from {url} (status={resp.status_code}, body={body[:200]!r})")
        return resp.json()
