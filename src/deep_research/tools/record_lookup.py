"""Resolve a citation tag back to the internal record it names.

This is what makes the report auditable. Every internal claim in the memo
carries a tag such as ``[CRM-DEAL D-2201]``; this module resolves that tag
against the raw source file. If it does not resolve, the claim is unsupported
and the citation audit fails the draft.

``verify_tags`` is the function the audit gate calls: give it the draft, get
back which tags resolved and which did not.

See docs/DECISIONS.md, D-07 and D-12.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

KNOWLEDGE_DIR = Path(__file__).resolve().parents[3] / "knowledge"

# Any bracketed token in the draft. Deliberately permissive: the shapes in use
# are [CRM-DEAL D-2201], [VENDOR V-102], [CAP CAP-02] and bare [WL-03] /
# [STRAT-01] / [PRIOR-02]. An earlier stricter pattern required whitespace
# before the number and silently skipped the bare forms, so every bracketed
# token is captured here and ID_PATTERN decides what is actually a citation.
TAG_PATTERN = re.compile(r"\[([^\]\[]{1,80})\]")
# The identifier inside a tag: D-2201, V-101, CAP-01, T-01, WL-03, STRAT-02.
ID_PATTERN = re.compile(r"\b([A-Z]{1,8}-\d{1,5})\b")


def _rows(filename: str) -> list[dict]:
    with (KNOWLEDGE_DIR / filename).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _md_records(filename: str) -> dict[str, str]:
    """Pull [TAG] -> text out of an already-tagged markdown file."""
    text = (KNOWLEDGE_DIR / filename).read_text(encoding="utf-8")
    parts = re.split(r"\*\*\[([A-Z]+-\d+)\]", text)
    out = {}
    for i in range(1, len(parts) - 1, 2):
        body = parts[i + 1]
        body = body.replace("**", "").replace("> ", "").replace("---", "")
        out[parts[i]] = " ".join(body.split()).strip().lstrip("-—– ").strip()
    return out


def resolve(identifier: str) -> dict:
    """Resolve a single record identifier against the raw source files."""
    ident = identifier.strip().strip("[]").upper()
    m = ID_PATTERN.search(ident)
    if not m:
        return {"identifier": identifier, "resolved": False, "reason": "not a record identifier"}
    key = m.group(1)

    if key.startswith("D-"):
        for r in _rows("crm_deals.csv"):
            if r["deal_id"].upper() == key:
                return {"identifier": key, "resolved": True, "record_type": "CRM deal",
                        "source_file": "knowledge/crm_deals.csv", "record": r}

    if key.startswith("V-"):
        for r in _rows("vendor_spend.csv"):
            if r["vendor_id"].upper() == key:
                return {"identifier": key, "resolved": True, "record_type": "Vendor spend",
                        "source_file": "knowledge/vendor_spend.csv", "record": r}

    if key.startswith("CAP-"):
        data = json.loads((KNOWLEDGE_DIR / "capability_inventory.json").read_text(encoding="utf-8"))
        for c in data["capabilities"]:
            if c["capability_id"].upper() == key:
                return {"identifier": key, "resolved": True, "record_type": "Capability",
                        "source_file": "knowledge/capability_inventory.json", "record": c}

    if key.startswith("T-"):
        data = json.loads((KNOWLEDGE_DIR / "cvc_watchlist.json").read_text(encoding="utf-8"))
        for t in data["targets"]:
            if t["target_id"].upper() == key:
                return {"identifier": key, "resolved": True, "record_type": "Investment target",
                        "source_file": "knowledge/cvc_watchlist.json", "record": t}

    for prefix, filename, label in (
        ("WL-", "win_loss_interviews.md", "Win/loss interview"),
        ("STRAT-", "strategy_priorities.md", "Strategic priority"),
        ("PRIOR-", "prior_market_view.md", "Prior adopted market view"),
    ):
        if key.startswith(prefix):
            recs = _md_records(filename)
            if key in recs:
                return {"identifier": key, "resolved": True, "record_type": label,
                        "source_file": f"knowledge/{filename}", "record": {"text": recs[key]}}

    return {"identifier": key, "resolved": False, "reason": "no matching record in any source file"}


def verify_tags(text: str) -> dict:
    """Check every citation tag in a draft. Used by the audit gate."""
    found, seen = [], set()
    for raw in TAG_PATTERN.findall(text or ""):
        m = ID_PATTERN.search(raw.upper())
        if not m:
            continue
        key = m.group(1)
        if key in seen:
            continue
        seen.add(key)
        found.append(key)

    resolved, unresolved = [], []
    for key in found:
        (resolved if resolve(key)["resolved"] else unresolved).append(key)

    return {
        "tags_found": len(found),
        "resolved": resolved,
        "unresolved": unresolved,
        "all_resolve": not unresolved,
    }


class LookupInput(BaseModel):
    identifier: str = Field(
        ...,
        description=(
            "An internal record identifier such as D-2201 (CRM deal), V-101 (vendor spend), "
            "CAP-02 (capability), T-01 (investment target), WL-03 (win/loss interview), "
            "STRAT-01 (strategic priority) or PRIOR-02 (prior adopted market view)."
        ),
    )


class RecordLookupTool(BaseTool):
    name: str = "Look up an internal record"
    description: str = (
        "Resolve an internal record identifier to the exact underlying record, straight from the "
        "source file. Use this to verify that a citation is real and that the claim it supports "
        "is accurate, or to read the full detail behind a figure. Returns resolved=false if no "
        "such record exists, which means the claim citing it must not be published."
    )
    args_schema: Type[BaseModel] = LookupInput

    def _run(self, identifier: str) -> str:
        return json.dumps(resolve(identifier), indent=2)[:4000]
