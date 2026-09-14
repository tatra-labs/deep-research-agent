"""Model tiers.

Research work is not uniform. Reading many sources is high-volume, shallow
extraction; deciding what the sources mean is low-volume and high-stakes. Those
deserve different models, so there are two tiers and the metric is cost per
completed memo rather than cost per request -- a cheaper model that needs more
retries to produce a usable report is not cheaper.

Both tiers are environment variables. The defaults are deliberately
conservative, widely-available model identifiers so that a fresh clone runs
without anyone having to discover which model names are current; newer and
cheaper tiers are one variable away. `probe()` checks the configured models
actually exist against the provider before a run, rather than assuming.

See docs/DECISIONS.md, D-06 and D-15.
"""

from __future__ import annotations

import os

from crewai import LLM

# The framework requires a provider prefix on model identifiers.
DEFAULT_WORKER = "openai/gpt-4o-mini"
DEFAULT_SYNTH = "openai/gpt-4o"
DEFAULT_EMBED = "text-embedding-3-small"


def worker_model() -> str:
    return os.getenv("DR_WORKER_MODEL", DEFAULT_WORKER)


def synth_model() -> str:
    return os.getenv("DR_SYNTH_MODEL", DEFAULT_SYNTH)


def embed_model() -> str:
    return os.getenv("DR_EMBED_MODEL", DEFAULT_EMBED)


def worker_llm() -> LLM:
    """High call volume: scouting, per-source reading, extraction.

    The completion budget is sized above what these tasks need rather than
    tight to it. A structured-output call that exhausts its completion budget
    does not come back truncated -- it raises, and the typed object is lost
    entirely. Paying for headroom is cheaper than losing a phase.
    """
    return LLM(model=worker_model(), temperature=0.2, max_completion_tokens=6000)


def synth_llm() -> LLM:
    """Low volume, high stakes: planning, fusion, writing, citation audit."""
    return LLM(model=synth_model(), temperature=0.3, max_completion_tokens=8000)


def embedder() -> dict:
    """Set explicitly at crew level, on purpose.

    The framework's knowledge and memory features ship *different* embedding
    defaults. Leaving both implicit creates two collections at mismatched
    dimensionality, which fails confusingly and late.
    """
    # The key is `model_name`, not `model`. The published example uses `model`,
    # which this version's validation silently DISCARDS -- leaving the embedder
    # on a default and reintroducing exactly the dimension mismatch this
    # function exists to prevent. Verified by round-tripping the config through
    # Crew validation; see docs/DECISIONS.md, D-23.
    return {"provider": "openai", "config": {"model_name": embed_model()}}


def api_key_present() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def probe() -> dict:
    """Check the configured models exist. Returns a report, never raises."""
    report = {
        "api_key_present": api_key_present(),
        "worker": worker_model(),
        "synth": synth_model(),
        "embed": embed_model(),
        "verified": False,
        "available": {},
        "note": "",
    }
    if not report["api_key_present"]:
        report["note"] = "OPENAI_API_KEY is not set; models could not be verified."
        return report
    try:
        import openai

        names = {m.id for m in openai.OpenAI().models.list()}
        report["verified"] = True
        for label, model in (
            ("worker", worker_model()),
            ("synth", synth_model()),
            ("embed", embed_model()),
        ):
            bare = model.split("/", 1)[-1]
            report["available"][label] = bare in names
        missing = [k for k, v in report["available"].items() if not v]
        if missing:
            report["note"] = (
                "These configured models were not returned by the provider: "
                + ", ".join(missing)
                + ". Set DR_WORKER_MODEL / DR_SYNTH_MODEL / DR_EMBED_MODEL to available ones."
            )
        else:
            report["note"] = "All configured models are available."
    except Exception as exc:  # network, auth, SDK shape
        report["note"] = f"Model list could not be retrieved ({type(exc).__name__}); continuing."
    return report


if __name__ == "__main__":
    import json

    print(json.dumps(probe(), indent=2))
