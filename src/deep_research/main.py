"""Command-line entry point.

    crewai run                      # the demonstration topic
    uv run deep_research --help     # options
    uv run deep_research --check    # verify configuration without spending money

`--offline` runs against the response cache committed to this repository, so
the whole pipeline can be reproduced with no search credentials and no spend.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _force_utf8_console() -> None:
    """Make the console accept the framework's output on Windows.

    The default Windows code page is cp1252, and the framework's progress
    display uses emoji. Every one of those raises inside an event handler,
    which the event bus catches and reports -- so the run appears to be
    failing in a dozen places while actually succeeding, and the real
    progress lines are interleaved with encoding errors.

    Reconfiguring the streams is enough; nothing downstream needs to know.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except Exception:
        # Environment variables set by the shell work equally well.
        pass


def _banner(topic: str, offline: bool) -> None:
    from .llms import synth_model, worker_model

    print()
    print("=" * 78)
    print("  DEEP RESEARCH AGENT")
    print("  External market signal, joined to internal records, in one memo.")
    print("=" * 78)
    print(f"  Topic     : {topic[:66]}")
    print(f"  Models    : {worker_model()} (extraction)")
    print(f"              {synth_model()} (synthesis, fusion, audit)")
    print(f"  Sources   : {'cached responses only' if offline else 'live, with cached fallback'}")
    print("=" * 78)
    print()


def check() -> int:
    """Verify configuration and data without calling a model."""
    _force_utf8_console()
    _load_env()
    from . import knowledge_build
    from .llms import probe
    from .tools import http, internal_analytics

    print("\nConfiguration check\n" + "-" * 40)
    report = probe()
    print(f"  API key present     : {report['api_key_present']}")
    print(f"  Worker model        : {report['worker']}")
    print(f"  Synthesis model     : {report['synth']}")
    print(f"  Embedding model     : {report['embed']}")
    if report["available"]:
        print(f"  Provider confirms   : {report['available']}")
    print(f"  Note                : {report['note']}")

    print("\nInternal records\n" + "-" * 40)
    chunks = knowledge_build.build(verbose=False)
    total = sum(
        1 for p in chunks for line in p.read_text(encoding="utf-8").splitlines() if line.strip()
    )
    print(f"  Knowledge chunks    : {total} across {len(chunks)} files")
    seg = internal_analytics.segment_stress()
    print(f"  Segments analysed   : {len(seg['segments'])}")
    worst = seg["segments"][0]
    print(
        f"  Largest exposure    : {worst['segment']} "
        f"(${worst['arr_lost_usd']:,} lost, capability {worst['capability_maturity_of_5']}/5)"
    )
    exp = internal_analytics.competitor_exposure()["totals"]
    print(f"  Supplier conflicts  : {exp['competitors_we_also_pay']} companies, "
          f"${exp['annual_spend_to_competitors_usd']:,}/yr")

    print("\nResponse cache\n" + "-" * 40)
    stats = http.cache_stats()
    from .tools import search_cache

    recorded = search_cache.stats()
    print(f"  Cached responses    : {stats['cached_responses']}")
    print(f"  Recorded searches   : {recorded['queries']} result sets")
    print(f"  Cache file          : {stats['path']}")
    print(f"  Offline mode        : {http.offline()}")
    if http.offline() and not recorded["queries"]:
        print("  WARNING             : offline with no recorded searches; run once online first")

    print("\nSearch providers\n" + "-" * 40)
    for name, var in (("Serper", "SERPER_API_KEY"), ("Tavily", "TAVILY_API_KEY")):
        print(f"  {name:<20}: {'configured' if os.getenv(var) else 'not configured (fine)'}")
    print("  Keyless sources     : SEC filings, arXiv, Hacker News, Wikipedia, news feed")
    print()
    return 0 if report["api_key_present"] else 1


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deep_research",
        description="Produce an executive market and investment memo.",
    )
    parser.add_argument("--topic", default=None, help="Research topic. Defaults to the demo topic.")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use only the committed response cache. No network, no search credentials needed.",
    )
    parser.add_argument(
        "--check", action="store_true", help="Verify configuration and data, then exit."
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress per-event progress output.")
    args = parser.parse_args(argv)

    if args.check:
        return check()

    if args.offline:
        os.environ["DR_OFFLINE"] = "1"

    _force_utf8_console()
    _load_env()

    from .flow import DEFAULT_TOPIC, DeepResearchFlow
    from .knowledge_build import build as build_knowledge
    from .llms import api_key_present

    if not api_key_present():
        print(
            "\nOPENAI_API_KEY is not set.\n\n"
            "  1. cp .env.example .env\n"
            "  2. add your key to .env\n\n"
            "Then run again. `--check` verifies everything else without a key.\n",
            file=sys.stderr,
        )
        return 2

    topic = args.topic or DEFAULT_TOPIC
    _banner(topic, args.offline)

    # Rebuild the citation-addressable corpus so retrieval and the raw records
    # can never drift apart.
    print("Preparing internal records:")
    build_knowledge()
    print()

    from .observability.listeners import install

    obs = install(trace_path=PROJECT_ROOT / "output" / "run_trace.jsonl", echo=not args.quiet)

    flow = DeepResearchFlow()
    paths = flow.kickoff(inputs={"topic": topic})

    print()
    print("=" * 78)
    print("  COMPLETE")
    print("=" * 78)
    summary = obs.summary()
    memo = flow.state.memo
    if memo is not None:
        print(f"  Recommendation      : {memo.recommendation.upper()} "
              f"(confidence: {memo.confidence})")
    if flow.state.fusion is not None:
        print(f"  Cross-source findings: {len(flow.state.fusion.signals)}")
        print(f"  Contradictions       : {len(flow.state.fusion.contradictions)}")
        print(f"  Stated gaps          : {len(flow.state.fusion.gaps)}")
    print(f"  Citation audit      : {'passed' if flow.state.audit_passed else 'exceptions'}")
    if flow.state.unresolved_citations:
        print(f"    unresolved        : {', '.join(flow.state.unresolved_citations)}")
    print(f"  Revisions           : {flow.state.revisions}")
    print(f"  Tool calls          : {summary['tool_calls']} "
          f"({summary['tool_cache_hits']} from cache, {summary['tool_failures']} failed)")
    print(f"  Tokens              : {summary['prompt_tokens']:,} in / "
          f"{summary['completion_tokens']:,} out")
    print(f"  Elapsed             : {summary['elapsed_seconds']:.0f}s")
    print()
    for label, path in (paths or {}).items():
        print(f"  {label:<20}: {path}")
    print()
    return 0


def kickoff(argv: list[str] | None = None) -> int:
    """Alias used by the framework's `run` command for flow projects."""
    return run(argv)


def plot() -> None:
    """Render the flow graph to an HTML file."""
    _force_utf8_console()
    _load_env()
    from .flow import DeepResearchFlow

    DeepResearchFlow().plot("output/flow")
    print("Wrote output/flow.html")


if __name__ == "__main__":
    raise SystemExit(run())
