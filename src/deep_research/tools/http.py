"""One HTTP client for the whole system: cached, throttled, politely identified.

Three constraints drove this module.

*Identity.* Wikipedia, the SEC and GDELT all refuse or throttle requests that
do not carry a descriptive User-Agent with contact information. Generic agents
get 403s with no explanation, so there is exactly one agent string here and
every outbound request uses it.

*Reproducibility.* Responses are cached to a SQLite file that is committed to
the repository. A reviewer with no search credentials can therefore reproduce a
full report from recorded responses, and nobody spends money doing it. Requests
are stored with credentials stripped, so the cache file is safe to commit.

*Politeness.* Each host gets a minimum interval between requests. These are
published limits, not guesses: the SEC asks for no more than 10 requests per
second, arXiv asks for a 3 second delay, and GDELT rate-limits aggressively
enough that its own operators describe the boundary as a fraction of a request
per second.

See docs/DECISIONS.md, D-08 and D-11.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from requests_cache import CachedSession

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CACHE_PATH = PROJECT_ROOT / ".cache" / "http"

CONTACT = os.getenv("DR_CONTACT_EMAIL", "research-agent@example.com")
# Format matters, and the two strictest sources disagree about it. Wikimedia
# wants "client/version (contact) library/version". The SEC returns 403 for any
# agent string containing a URL -- verified empirically, not documented -- so
# contact is given as an email only. This single string satisfies both.
USER_AGENT = f"MeridianDeepResearchAgent/0.1 ({CONTACT}) python-requests"

# Minimum seconds between requests to the same host.
HOST_MIN_INTERVAL = {
    "export.arxiv.org": 3.0,
    "api.gdeltproject.org": 5.0,
    "efts.sec.gov": 0.2,
    "data.sec.gov": 0.2,
    "en.wikipedia.org": 0.2,
    "hn.algolia.com": 0.1,
    "news.google.com": 0.5,
}
DEFAULT_MIN_INTERVAL = 0.25

# Names never written into the cache key or the stored request.
_SECRET_PARAMS = [
    "api_key",
    "apikey",
    "key",
    "token",
    "X-API-KEY",
    "Authorization",
    "Ocp-Apim-Subscription-Key",
    "X-Subscription-Token",
]

_last_hit: dict[str, float] = {}
_lock = threading.Lock()

# Requests actually served, split by where they came from. The framework has a
# cache of its own for tool *results*, which is a different thing measured on a
# different layer -- so this counts the HTTP layer directly rather than
# inferring it from tool events.
_traffic = {"served_from_cache": 0, "fetched_live": 0, "misses_offline": 0}


def offline() -> bool:
    """True when the run must be served entirely from the committed cache."""
    return os.getenv("DR_OFFLINE", "").strip().lower() in {"1", "true", "yes"}


def _throttle(url: str) -> None:
    host = urlsplit(url).netloc
    interval = HOST_MIN_INTERVAL.get(host, DEFAULT_MIN_INTERVAL)
    with _lock:
        elapsed = time.monotonic() - _last_hit.get(host, 0.0)
        wait = interval - elapsed
        if wait > 0:
            time.sleep(wait)
        _last_hit[host] = time.monotonic()


_session: CachedSession | None = None


def session() -> CachedSession:
    """The process-wide cached session."""
    global _session
    if _session is None:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _session = CachedSession(
            str(CACHE_PATH),
            backend="sqlite",
            # Serper and Tavily are POST APIs; POST is not cached by default.
            allowable_methods=("GET", "HEAD", "POST"),
            expire_after=60 * 60 * 24 * 30,
            ignored_parameters=_SECRET_PARAMS,
            # A rate-limited or unreachable upstream serves the cached copy
            # rather than failing the run.
            stale_if_error=True,
            only_if_cached=offline(),
        )
        _session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"})
    return _session


def get(url: str, *, params: dict | None = None, timeout: int = 20, **kw):
    """Throttled, cached GET. Returns None instead of raising on failure."""
    try:
        s = session()
        if not offline():
            _throttle(url)
        r = s.get(url, params=params, timeout=timeout, **kw)
        # 504 is what requests-cache returns for a cache miss in offline mode.
        if r.status_code == 504 and offline():
            _traffic["misses_offline"] += 1
            return None
        _record(r)
        if r.status_code >= 400:
            return None
        return r
    except Exception:
        return None


def post(url: str, *, json: dict | None = None, timeout: int = 20, **kw):
    """Throttled, cached POST. Returns None instead of raising on failure."""
    try:
        s = session()
        if not offline():
            _throttle(url)
        r = s.post(url, json=json, timeout=timeout, **kw)
        if r.status_code == 504 and offline():
            _traffic["misses_offline"] += 1
            return None
        _record(r)
        if r.status_code >= 400:
            return None
        return r
    except Exception:
        return None


def _record(response) -> None:
    key = "served_from_cache" if getattr(response, "from_cache", False) else "fetched_live"
    with _lock:
        _traffic[key] += 1


def traffic() -> dict:
    """How much of this run came off disk rather than the network."""
    return dict(_traffic)


def cache_stats() -> dict:
    try:
        s = session()
        return {"cached_responses": sum(1 for _ in s.cache.responses.keys()), "path": f"{CACHE_PATH}.sqlite"}
    except Exception:
        return {"cached_responses": 0, "path": f"{CACHE_PATH}.sqlite"}
