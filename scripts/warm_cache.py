"""Pre-warm the committed response cache.

Run this once, with network access, to populate `.cache/http.sqlite`. That file
is committed, which is what lets someone clone this repository and reproduce a
full report with no search credentials and no spend -- and what makes a
recorded demonstration fast and identical every time.

    uv run python scripts/warm_cache.py

Credentials are stripped from stored requests by the cache configuration, so
the resulting file is safe to commit. Verify before you do:

    uv run python scripts/warm_cache.py --audit
    uv run python scripts/warm_cache.py --prune

See docs/DECISIONS.md, D-11.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from deep_research.tools import http, keyless  # noqa: E402
from deep_research.tools.search import web_search  # noqa: E402

# Queries chosen to cover the demonstration topic and every segment the
# internal records are tagged with, since those are what the agents will reach
# for once the brief is written.
FILING_QUERIES = [
    '"industrial autonomy" OR "autonomous manufacturing"',
    '"agentic AI" AND "manufacturing"',
    '"robot fleet" OR "fleet management" AND robotics',
    '"predictive maintenance" AND "artificial intelligence"',
    '"unified namespace" OR "industrial data platform"',
    '"machine vision" AND "quality inspection"',
    '"foundation model" AND robotics',
]

WEB_QUERIES = [
    "agentic AI industrial manufacturing 2026",
    "industrial robot fleet orchestration software funding",
    "physical AI robot foundation model funding round",
    "predictive maintenance AI industrial market 2026",
    "industrial data fabric unified namespace vendors",
    "agentic AI operations plant workflow automation",
    "machine vision quality inspection AI manufacturing",
    "industrial automation new entrants startups 2026",
    "Siemens Rockwell ABB industrial AI strategy 2026",
    "manufacturing autonomy investment trends 2026",
]

RESEARCH_QUERIES = [
    "multi-robot coordination",
    "robot foundation models manipulation",
    "predictive maintenance deep learning",
    "industrial process control reinforcement learning",
    "vision based defect detection manufacturing",
    "large language model agents industrial automation",
]

COMMUNITY_QUERIES = [
    "industrial robotics automation",
    "robot fleet management software",
    "manufacturing AI agents",
    "machine vision inspection",
]

REFERENCE_QUERIES = [
    "industrial automation",
    "industrial robot",
    "predictive maintenance",
    "programmable logic controller",
]


def warm() -> None:
    calls = [
        ("regulatory filings", FILING_QUERIES, lambda q: keyless.edgar_fulltext(q)),
        ("regulatory filings (S-1)", FILING_QUERIES[:4],
         lambda q: keyless.edgar_fulltext(q, forms="S-1")),
        ("news feed", WEB_QUERIES, lambda q: keyless.news_rss(q)),
        ("web search", WEB_QUERIES, lambda q: web_search(q)),
        ("research preprints", RESEARCH_QUERIES, lambda q: keyless.arxiv_search(q)),
        ("community", COMMUNITY_QUERIES, lambda q: keyless.hn_search(q)),
        ("reference", REFERENCE_QUERIES, lambda q: keyless.wikipedia_search(q)),
    ]
    ok = fail = 0
    for label, queries, fn in calls:
        print(f"\n{label}")
        for q in queries:
            try:
                result = fn(q)
                available = result.get("available", True) if isinstance(result, dict) else True
                if isinstance(result, dict) and "results" in result:
                    available = bool(result["results"])
                print(f"  {'ok  ' if available else 'none'}  {q[:66]}")
                ok += 1 if available else 0
                fail += 0 if available else 1
            except Exception as exc:
                print(f"  ERR   {q[:52]} ({type(exc).__name__})")
                fail += 1

    stats = http.cache_stats()
    print(f"\n{ok} warmed, {fail} unavailable")
    print(f"cache now holds {stats['cached_responses']} responses")
    print(f"  {stats['path']}")


def audit() -> int:
    """Confirm no credential material was stored in the cache file."""
    import re
    import sqlite3

    path = Path(f"{http.CACHE_PATH}.sqlite")
    if not path.exists():
        print("no cache file yet")
        return 1
    # Credential SHAPES, not substrings. Loose needles produce false alarms --
    # "sk-" matches the SEC filename "task-2026proxystatement.htm" and the word
    # "risk-aware", and "authorization" matches any article about auth. An audit
    # that cries wolf gets ignored, which is worse than not having one.
    patterns = [
        (re.compile(r"sk-[A-Za-z0-9_\-]{20,}"), "OpenAI-style API key"),
        (re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"), "Anthropic API key"),
        (re.compile(r"tvly-[A-Za-z0-9_\-]{16,}"), "Tavily API key"),
        (re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}"), "bearer token"),
        (re.compile(r"X-API-KEY\s*[:=]\s*\S{12,}", re.I), "X-API-KEY header"),
        (re.compile(r"api[_-]?key\s*[:=]\s*[\"']?[A-Za-z0-9_\-]{16,}", re.I), "api_key value"),
    ]
    conn = sqlite3.connect(str(path))
    hits: list[str] = []
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        for table in tables:
            for row in conn.execute(f"SELECT * FROM {table}").fetchall():  # noqa: S608
                blob = " ".join(str(c) for c in row)
                for rx, label in patterns:
                    m = rx.search(blob)
                    if m:
                        hits.append(f"{table}: looks like a {label}")
                        break
    finally:
        conn.close()

    # The recorded search results are committed too, so they get the same
    # treatment. A second committed artifact needs a second look, not the
    # assumption that the first one's clean bill covers it.
    from deep_research.tools.search_cache import CACHE_PATH as SEARCH_CACHE

    print(f"cache file: {path}")
    print(f"size: {path.stat().st_size / 1024:.0f} KB")
    if SEARCH_CACHE.exists():
        text = SEARCH_CACHE.read_text(encoding="utf-8", errors="ignore")
        for rx, label in patterns:
            if rx.search(text):
                hits.append(f"search_results.json: looks like a {label}")
                break
        print(f"search results: {SEARCH_CACHE}")
        print(f"size: {SEARCH_CACHE.stat().st_size / 1024:.0f} KB")
    if hits:
        print("\nPOSSIBLE CREDENTIAL MATERIAL FOUND - do not commit:")
        for h in sorted(set(hits))[:20]:
            print(f"  {h}")
        return 1
    print("\nNo credential patterns found. Safe to commit.")
    return 0



MAX_COMMITTED_RESPONSE_BYTES = 60_000


def prune() -> int:
    """Drop oversized responses so the cache is fit to commit.

    Article bodies are what make this file large: a scraped page is often
    half a megabyte, and 156 of them turned a 1.8 MB cache into 68 MB. That
    is too big for a repository, and it carries a second problem -- a scraped
    page brings other people's embedded keys with it, which showed up in the
    credential audit as matches from third-party HTML rather than from
    anything of ours.

    What the offline path actually needs is the small stuff: the search result
    sets, and the JSON from EDGAR, arXiv, Hacker News, Wikipedia and the news
    feed. Those reproduce the research. The full text of every article does
    not, so it is dropped, and an offline run reports the pages it could not
    re-read instead of shipping them.
    """
    import sqlite3

    path = Path(f"{http.CACHE_PATH}.sqlite")
    if not path.exists():
        print(f"no cache at {path}")
        return 0
    before = path.stat().st_size
    con = sqlite3.connect(path)
    rows = con.execute("select key, length(value) from responses").fetchall()
    doomed = [k for k, n in rows if (n or 0) > MAX_COMMITTED_RESPONSE_BYTES]
    con.executemany("delete from responses where key = ?", [(k,) for k in doomed])
    con.commit()
    try:
        con.execute("delete from redirects where key not in (select key from responses)")
        con.commit()
    except sqlite3.Error:
        pass
    con.execute("vacuum")
    con.commit()
    con.close()
    after = path.stat().st_size
    print(f"pruned {len(doomed)} responses over {MAX_COMMITTED_RESPONSE_BYTES:,} bytes")
    print(f"kept   {len(rows) - len(doomed)} responses")
    print(f"size   {before / 1e6:.1f} MB -> {after / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    if "--prune" in sys.argv:
        prune()
        print()
        raise SystemExit(audit())
    if "--audit" in sys.argv:
        raise SystemExit(audit())
    warm()
    print()
    raise SystemExit(audit())
