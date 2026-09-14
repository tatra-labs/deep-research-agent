"""Reshape raw internal records into citation-addressable knowledge chunks.

The framework's knowledge feature retrieves by meaning and returns text
chunks -- it does not return record identifiers. An executive report whose
internal claims cannot be traced to an exact row is not defensible, so the
corpus is rewritten here such that every chunk carries its own address.

Each output line is one record, prefixed with a stable citation tag such as
``[CRM-DEAL D-2201]``. When the writer cites that tag, ``record_lookup``
resolves it against the raw source file, and the citation audit can fail the
draft if it does not resolve.

See docs/DECISIONS.md, D-07.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "knowledge"
DERIVED_DIR = KNOWLEDGE_DIR / "derived"

# Markdown sources already carry inline [TAG-NN] ids; they are split on those
# rather than rewritten.
MARKDOWN_SOURCES = (
    "win_loss_interviews.md",
    "strategy_priorities.md",
    "prior_market_view.md",
)

# Tags appear either standalone ("**[WL-01]** -- text") or as the opening of
# a bold heading ("**[STRAT-01] Title.**"); anchor on the tag itself so both work.
_MD_TAG = re.compile(r"\*\*\[([A-Z]+-\d+)\]\s*(.*?)(?=\*\*\[[A-Z]+-\d+\]|\Z)", re.S)


def _money(value: str | int | None) -> str:
    try:
        return f"${int(value):,}"
    except (TypeError, ValueError):
        return "n/a"


def build_crm_deals() -> str:
    rows = list(csv.DictReader((KNOWLEDGE_DIR / "crm_deals.csv").open(encoding="utf-8")))
    lines = []
    for r in rows:
        parts = [
            f"[CRM-DEAL {r['deal_id']}]",
            f"{r['outcome']} deal",
            f"account: {r['account']}",
            f"business unit: {r['business_unit']}",
            f"region: {r['region']}",
            f"segment: {r['segment_tag']}",
            f"annual recurring revenue: {_money(r['arr_usd'])}",
            f"close date: {r['close_date']}",
        ]
        if r["competitor"]:
            parts.append(f"competitor: {r['competitor']}")
        if r["loss_reason_code"]:
            parts.append(f"loss reason: {r['loss_reason_code']}")
        lines.append(parts[0] + " " + " | ".join(parts[1:]))
    return "\n".join(lines)


def build_vendor_spend() -> str:
    rows = list(csv.DictReader((KNOWLEDGE_DIR / "vendor_spend.csv").open(encoding="utf-8")))
    lines = []
    for r in rows:
        parts = [
            f"[VENDOR {r['vendor_id']}]",
            f"vendor: {r['vendor']}",
            f"category: {r['category']}",
            f"segment: {r['segment_tag'] or 'not segment-tagged'}",
            f"business unit: {r['business_unit']}",
            f"annual spend: {_money(r['annual_spend_usd'])}",
            f"contract end: {r['contract_end'] or 'none recorded'}",
            f"renewal risk: {r['renewal_risk']}",
            f"internal owner: {r['internal_owner']}",
        ]
        if r["notes"]:
            parts.append(f"notes: {r['notes']}")
        lines.append(parts[0] + " " + " | ".join(parts[1:]))
    return "\n".join(lines)


def build_capabilities() -> str:
    data = json.loads((KNOWLEDGE_DIR / "capability_inventory.json").read_text(encoding="utf-8"))
    lines = [f"Maturity scale: {data['scale']}"]
    for c in data["capabilities"]:
        lines.append(
            f"[CAP {c['capability_id']}] "
            + " | ".join(
                [
                    f"capability: {c['name']}",
                    f"segment: {c['segment_tag'] or 'not segment-tagged'}",
                    f"maturity: {c['maturity']} of 5",
                    f"owning business unit: {c['owner_bu']}",
                    f"owner: {c['owner']}",
                    f"evidence: {c['evidence']}",
                    f"gaps: {c['gaps']}",
                ]
            )
        )
    return "\n".join(lines)


def build_watchlist() -> str:
    data = json.loads((KNOWLEDGE_DIR / "cvc_watchlist.json").read_text(encoding="utf-8"))
    lines = [f"Fund mandate: {data['mandate']}", f"Scoring method: {data['scoring']}"]
    for t in data["targets"]:
        parts = [
            f"[WATCHLIST {t['target_id']}]",
            f"company: {t['company']}",
            f"segment: {t['segment_tag']}",
            f"last reviewed: {t['last_reviewed']}",
            f"prior score: {t['prior_score']} of 100",
            f"status: {t['status']}",
            f"thesis at review: {t['thesis_at_review']}",
        ]
        if t.get("pass_rationale"):
            parts.append(f"pass rationale: {t['pass_rationale']}")
        lines.append(parts[0] + " " + " | ".join(parts[1:]))
    return "\n".join(lines)


def build_markdown(name: str) -> str:
    """Split an already-tagged markdown file into one chunk per record id."""
    text = (KNOWLEDGE_DIR / name).read_text(encoding="utf-8")
    out = []
    for tag, body in _MD_TAG.findall(text):
        # Strip markdown furniture so each chunk reads as prose to the retriever.
        flat = body.replace("**", "").replace("> ", "").replace("---", "")
        flat = " ".join(flat.split()).lstrip("- ").strip()
        out.append(f"[{tag}] {flat}")
    return "\n\n".join(out)


BUILDERS = {
    "crm_deals.md": build_crm_deals,
    "vendor_spend.md": build_vendor_spend,
    "capability_inventory.md": build_capabilities,
    "cvc_watchlist.md": build_watchlist,
}


def build(verbose: bool = True) -> list[Path]:
    DERIVED_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for filename, builder in BUILDERS.items():
        target = DERIVED_DIR / filename
        target.write_text(builder() + "\n", encoding="utf-8")
        written.append(target)
    for name in MARKDOWN_SOURCES:
        target = DERIVED_DIR / name
        target.write_text(build_markdown(name) + "\n", encoding="utf-8")
        written.append(target)
    if verbose:
        for p in written:
            n = sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip())
            print(f"  {p.relative_to(KNOWLEDGE_DIR.parent)}  ({n} chunks)")
    return written


# Paths relative to the knowledge/ directory, which is how the framework's
# knowledge sources expect to receive them.
DERIVED_RELATIVE = [f"derived/{n}" for n in list(BUILDERS) + list(MARKDOWN_SOURCES)]


if __name__ == "__main__":
    print("Building citation-addressable knowledge chunks:")
    build()
