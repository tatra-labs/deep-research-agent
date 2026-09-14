"""A readable record of what the searches returned.

The HTTP cache in `http.py` covers everything this project fetches itself, but
the keyless metasearch tier is a third-party library with its own HTTP client,
so its results never pass through that cache. That gap is not cosmetic: the
metasearch tier is the one that answers most general web queries, so without
this file `--offline` reproduces the internal analysis and almost none of the
external evidence -- while still reporting success. A reproducibility claim
that only holds for part of the pipeline is worse than one that is scoped
honestly, so this closes the gap rather than narrowing the claim.

It is JSON rather than another SQLite file on purpose. A reviewer can open it
and read the exact result set behind every external citation in the committed
sample report, which is a stronger form of provenance than "trust the binary".

Keys are normalised queries, so the same question asked with different
capitalisation or spacing hits the same entry.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

CACHE_PATH = Path(__file__).resolve().parents[3] / ".cache" / "search_results.json"

_lock = threading.Lock()
_store: dict | None = None


def _normalise(query: str) -> str:
    return re.sub(r"\s+", " ", (query or "").strip().lower())


def _load() -> dict:
    global _store
    if _store is None:
        try:
            _store = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            _store = {}
    return _store


def recall(query: str) -> list[dict] | None:
    """The stored result set for this query, if one was recorded."""
    entry = _load().get(_normalise(query))
    if not entry:
        return None
    results = entry.get("results") or None
    return results


def provider_for(query: str) -> str:
    entry = _load().get(_normalise(query)) or {}
    return entry.get("provider", "unknown")


def remember(query: str, provider: str, results: list[dict]) -> None:
    """Record a live result set. Silent on failure -- this is never load-bearing."""
    if not results:
        return
    try:
        store = _load()
        with _lock:
            store[_normalise(query)] = {
                "query": query,
                "provider": provider,
                "results": results,
            }
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            CACHE_PATH.write_text(
                json.dumps(store, indent=2, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
    except Exception:
        pass


def stats() -> dict:
    store = _load()
    return {"queries": len(store), "path": str(CACHE_PATH)}
