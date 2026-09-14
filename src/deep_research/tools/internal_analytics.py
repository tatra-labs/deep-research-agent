"""Deterministic analysis of the internal records.

Semantic retrieval answers "what did buyers say about autonomy". It cannot
answer "how many deals did we lose in that segment and what was the ARR",
because retrieval returns a handful of chunks and the agent never sees the
whole table. Asking a language model to aggregate a table it can only
partially see is how confident wrong numbers get into an executive report.

So the arithmetic happens here, in Python, over the complete files -- and every
figure is returned alongside the record identifiers behind it, so the citation
audit can verify the claim and the reader can trace it.

The division of labour is deliberate: computers count, models interpret.

See docs/DECISIONS.md, D-07 and D-20.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

KNOWLEDGE_DIR = Path(__file__).resolve().parents[3] / "knowledge"


# --------------------------------------------------------------------------
# Loaders
# --------------------------------------------------------------------------
def _deals() -> list[dict]:
    with (KNOWLEDGE_DIR / "crm_deals.csv").open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _vendors() -> list[dict]:
    with (KNOWLEDGE_DIR / "vendor_spend.csv").open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _capabilities() -> list[dict]:
    return json.loads((KNOWLEDGE_DIR / "capability_inventory.json").read_text(encoding="utf-8"))[
        "capabilities"
    ]


def _watchlist() -> list[dict]:
    return json.loads((KNOWLEDGE_DIR / "cvc_watchlist.json").read_text(encoding="utf-8"))["targets"]


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------------
# Analyses
# --------------------------------------------------------------------------
def segment_stress() -> dict:
    """Per market segment: commercial outcomes against internal readiness.

    This is the table the fusion step reasons over. It deliberately puts
    revenue lost, internal capability maturity and prior investment decisions
    in the same row, because no single source file contains all three.
    """
    deals = _deals()
    caps = {c["segment_tag"]: c for c in _capabilities() if c["segment_tag"]}
    watch = _watchlist()

    agg: dict[str, dict] = defaultdict(
        lambda: {
            "deals_won": 0,
            "deals_lost": 0,
            "deals_open": 0,
            "arr_won_usd": 0,
            "arr_lost_usd": 0,
            "arr_open_usd": 0,
            "lost_deal_ids": [],
            "won_deal_ids": [],
            "open_deal_ids": [],
            "loss_reasons": defaultdict(int),
            "competitors": defaultdict(int),
        }
    )

    for d in deals:
        seg = d["segment_tag"]
        row = agg[seg]
        arr = _int(d["arr_usd"])
        if d["outcome"] == "Won":
            row["deals_won"] += 1
            row["arr_won_usd"] += arr
            row["won_deal_ids"].append(d["deal_id"])
        elif d["outcome"] == "Lost":
            row["deals_lost"] += 1
            row["arr_lost_usd"] += arr
            row["lost_deal_ids"].append(d["deal_id"])
            if d["loss_reason_code"]:
                row["loss_reasons"][d["loss_reason_code"]] += 1
            if d["competitor"]:
                row["competitors"][d["competitor"]] += 1
        else:
            row["deals_open"] += 1
            row["arr_open_usd"] += arr
            row["open_deal_ids"].append(d["deal_id"])

    out = []
    for seg, row in agg.items():
        cap = caps.get(seg)
        decided = row["deals_won"] + row["deals_lost"]
        passed = [
            {"target_id": t["target_id"], "company": t["company"], "reviewed": t["last_reviewed"],
             "prior_score": t["prior_score"]}
            for t in watch
            if t["segment_tag"] == seg and t["status"] == "Pass"
        ]
        out.append(
            {
                "segment": seg,
                "deals_won": row["deals_won"],
                "deals_lost": row["deals_lost"],
                "win_rate_pct": round(100 * row["deals_won"] / decided) if decided else None,
                "arr_lost_usd": row["arr_lost_usd"],
                "arr_won_usd": row["arr_won_usd"],
                "arr_open_usd": row["arr_open_usd"],
                "dominant_loss_reason": max(row["loss_reasons"], key=row["loss_reasons"].get)
                if row["loss_reasons"]
                else None,
                "loss_reason_counts": dict(row["loss_reasons"]),
                "top_competitors": sorted(
                    row["competitors"].items(), key=lambda kv: -kv[1]
                )[:3],
                "internal_capability": cap["name"] if cap else None,
                "capability_id": cap["capability_id"] if cap else None,
                "capability_maturity_of_5": cap["maturity"] if cap else None,
                "capability_gaps": cap["gaps"] if cap else None,
                "investment_targets_passed": passed,
                # Records are partitioned by what they actually support. A
                # single list here caused a real miscitation: the losses-only
                # list was the only one present, so a claim about a 90% win
                # rate got "supported" by the segment's one lost deal. The
                # identifier resolved, which is exactly what made it dangerous
                # -- it looked verified. Every figure above now has a list that
                # backs it, and none of them mixes outcomes.
                "records_for_losses": row["lost_deal_ids"][:12],
                "records_for_wins": row["won_deal_ids"][:12],
                # A win rate is wins over DECIDED deals, so its denominator
                # includes the losses. Citing only the won deals for a win
                # rate is incomplete, and citing the whole decided set for it
                # is correct even though some of those records are losses --
                # a distinction the audit was right to press on.
                "records_for_win_rate": (row["won_deal_ids"] + row["lost_deal_ids"])[:24],
                "records_for_open_deals": row["open_deal_ids"][:12],
                "records_for_capability_rating": [cap["capability_id"]] if cap else [],
                "records_for_passed_targets": [t["target_id"] for t in passed],
                "citation_records": (
                    row["lost_deal_ids"][:12]
                    + row["won_deal_ids"][:12]
                    + ([cap["capability_id"]] if cap else [])
                ),
            }
        )

    out.sort(key=lambda r: -r["arr_lost_usd"])
    return {
        "analysis": "segment_stress",
        "description": (
            "Commercial outcomes joined to internal capability maturity and prior investment "
            "decisions, by market segment. Sorted by ARR lost."
        ),
        "segments": out,
        "sources": ["crm_deals.csv", "capability_inventory.json", "cvc_watchlist.json"],
    }


def competitor_exposure() -> dict:
    """Companies that take revenue from us -- and which of them we also pay.

    A competitor appearing in vendor spend is not automatically a problem;
    component suppliers routinely compete at the system level. It becomes a
    finding when the spend sits in the same segment as the losses.
    """
    deals = _deals()
    vendors = {v["vendor"]: v for v in _vendors()}

    lost: dict[str, dict] = defaultdict(
        lambda: {"deals_lost": 0, "arr_lost_usd": 0, "deal_ids": [], "segments": set(),
                 "loss_reasons": defaultdict(int)}
    )
    for d in deals:
        if d["outcome"] == "Lost" and d["competitor"]:
            e = lost[d["competitor"]]
            e["deals_lost"] += 1
            e["arr_lost_usd"] += _int(d["arr_usd"])
            e["deal_ids"].append(d["deal_id"])
            e["segments"].add(d["segment_tag"])
            if d["loss_reason_code"]:
                e["loss_reasons"][d["loss_reason_code"]] += 1

    rows = []
    for name, e in lost.items():
        v = vendors.get(name)
        same_segment = bool(v and v.get("segment_tag") in e["segments"])
        rows.append(
            {
                "competitor": name,
                "deals_lost": e["deals_lost"],
                "arr_lost_usd": e["arr_lost_usd"],
                "segments_where_they_beat_us": sorted(e["segments"]),
                "dominant_loss_reason": max(e["loss_reasons"], key=e["loss_reasons"].get)
                if e["loss_reasons"]
                else None,
                "we_also_pay_them": bool(v),
                "our_annual_spend_usd": _int(v["annual_spend_usd"]) if v else 0,
                "spend_in_same_segment_they_beat_us": same_segment,
                "our_contract_end": v["contract_end"] if v else None,
                "spend_category": v["category"] if v else None,
                "spend_business_unit": v["business_unit"] if v else None,
                "spend_notes": v["notes"] if v else None,
                "citation_records": e["deal_ids"] + ([v["vendor_id"]] if v else []),
            }
        )

    rows.sort(key=lambda r: -r["arr_lost_usd"])
    conflicted = [r for r in rows if r["we_also_pay_them"]]
    return {
        "analysis": "competitor_exposure",
        "description": (
            "For every company that has won business against us: revenue lost to them, and "
            "whether we are also paying them. Flags whether that spend sits in the same segment "
            "where they beat us, which is what makes it a conflict rather than a coincidence."
        ),
        "competitors": rows,
        "totals": {
            "competitors_we_also_pay": len(conflicted),
            "annual_spend_to_competitors_usd": sum(r["our_annual_spend_usd"] for r in conflicted),
            "arr_lost_to_those_same_companies_usd": sum(r["arr_lost_usd"] for r in conflicted),
            "of_which_same_segment": sum(
                1 for r in conflicted if r["spend_in_same_segment_they_beat_us"]
            ),
        },
        "sources": ["crm_deals.csv", "vendor_spend.csv"],
    }


def renewal_exposure(months_ahead: int = 9) -> dict:
    """Contracts coming up for renewal, worst first, with leverage context."""
    today = date.today()
    rows = []
    for v in _vendors():
        end = v.get("contract_end") or ""
        if not end:
            continue
        try:
            y, m, d = (int(x) for x in end.split("-"))
        except ValueError:
            continue
        months = (y - today.year) * 12 + (m - today.month)
        if 0 <= months <= months_ahead:
            rows.append(
                {
                    "vendor_id": v["vendor_id"],
                    "vendor": v["vendor"],
                    "category": v["category"],
                    "segment": v["segment_tag"] or None,
                    "annual_spend_usd": _int(v["annual_spend_usd"]),
                    "contract_end": end,
                    "months_until_renewal": months,
                    "renewal_risk": v["renewal_risk"],
                    "business_unit": v["business_unit"],
                    "internal_owner": v["internal_owner"],
                    "notes": v["notes"],
                    "citation_records": [v["vendor_id"]],
                }
            )
    rows.sort(key=lambda r: (r["contract_end"], -r["annual_spend_usd"]))
    return {
        "analysis": "renewal_exposure",
        "description": (
            f"Third-party contracts ending within {months_ahead} months. A renewal is a decision "
            "point, so external evidence that a supplier's position is weakening converts into "
            "negotiating leverage only if the timing is known."
        ),
        "window_months": months_ahead,
        "renewals": rows,
        "total_annual_spend_in_window_usd": sum(r["annual_spend_usd"] for r in rows),
        "sources": ["vendor_spend.csv"],
    }


def loss_reason_to_capability() -> dict:
    """Rank loss reasons and attach the capability each one implicates."""
    deals = _deals()
    caps = {c["segment_tag"]: c for c in _capabilities() if c["segment_tag"]}
    agg: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "arr_usd": 0, "deal_ids": [], "segments": defaultdict(int)}
    )
    for d in deals:
        if d["outcome"] == "Lost" and d["loss_reason_code"]:
            e = agg[d["loss_reason_code"]]
            e["count"] += 1
            e["arr_usd"] += _int(d["arr_usd"])
            e["deal_ids"].append(d["deal_id"])
            e["segments"][d["segment_tag"]] += 1

    rows = []
    for code, e in agg.items():
        primary_seg = max(e["segments"], key=e["segments"].get)
        cap = caps.get(primary_seg)
        rows.append(
            {
                "loss_reason_code": code,
                "deals_lost": e["count"],
                "arr_lost_usd": e["arr_usd"],
                "primary_segment": primary_seg,
                "implicated_capability": cap["name"] if cap else None,
                "capability_id": cap["capability_id"] if cap else None,
                "capability_maturity_of_5": cap["maturity"] if cap else None,
                "citation_records": e["deal_ids"][:12] + ([cap["capability_id"]] if cap else []),
            }
        )
    rows.sort(key=lambda r: -r["arr_lost_usd"])
    return {
        "analysis": "loss_reason_to_capability",
        "description": (
            "Why we lose, ranked by revenue, with the internal capability each reason implicates "
            "and how mature we rate that capability ourselves."
        ),
        "loss_reasons": rows,
        "sources": ["crm_deals.csv", "capability_inventory.json"],
    }


# --------------------------------------------------------------------------
# Agent-facing tools
# --------------------------------------------------------------------------
class NoInput(BaseModel):
    pass


class SegmentStressTool(BaseTool):
    name: str = "Analyse internal position by market segment"
    description: str = (
        "Returns exact figures, computed over the complete internal records, joining commercial "
        "outcomes (deals won and lost, ARR) to our own capability maturity ratings and to "
        "investment targets we previously passed on -- broken down by market segment. Use this "
        "first when assessing internal position: it is the only view that puts revenue lost, "
        "internal readiness and prior decisions in the same row. Each row carries its record "
        "IDs split by what they support -- records_for_wins, records_for_losses, "
        "records_for_open_deals, records_for_win_rate, records_for_capability_rating -- so "
        "cite the list that matches your claim, and cite the whole list for an aggregate "
        "figure. A win rate is wins over decided deals, so records_for_win_rate deliberately "
        "includes the lost deals that form its denominator."
    )
    args_schema: Type[BaseModel] = NoInput

    def _run(self) -> str:
        return json.dumps(segment_stress(), indent=2)[:9000]


class CompetitorExposureTool(BaseTool):
    name: str = "Analyse competitor and supplier overlap"
    description: str = (
        "Returns, for every company that has won business against us, the revenue we lost to "
        "them and whether we are also paying them as a supplier -- including whether that spend "
        "sits in the same market segment where they beat us. Use this to find conflicts between "
        "our procurement and our competitive position. Cite the returned record IDs."
    )
    args_schema: Type[BaseModel] = NoInput

    def _run(self) -> str:
        return json.dumps(competitor_exposure(), indent=2)[:9000]


class LossReasonTool(BaseTool):
    name: str = "Analyse why we lose deals"
    description: str = (
        "Ranks our recorded loss reasons by revenue lost and attaches the internal capability "
        "each reason implicates, together with our own maturity rating for it. Use this to "
        "connect commercial outcomes to capability gaps. Cite the returned record IDs."
    )
    args_schema: Type[BaseModel] = NoInput

    def _run(self) -> str:
        return json.dumps(loss_reason_to_capability(), indent=2)[:7000]


class RenewalInput(BaseModel):
    months_ahead: int = Field(9, description="How many months ahead to look. Default 9.")


class RenewalExposureTool(BaseTool):
    name: str = "List upcoming supplier renewals"
    description: str = (
        "Lists third-party contracts ending within a given number of months, with annual spend, "
        "risk rating and owner. Use this to turn external evidence about a supplier into timed, "
        "actionable negotiating leverage. Cite the returned record IDs."
    )
    args_schema: Type[BaseModel] = RenewalInput

    def _run(self, months_ahead: int = 9) -> str:
        return json.dumps(renewal_exposure(months_ahead), indent=2)[:7000]


# --------------------------------------------------------------------------
# Arithmetic for the auditor
# --------------------------------------------------------------------------
def total_for_records(identifiers: list[str]) -> dict:
    """Sum the money attached to a set of record identifiers, exactly.

    This exists because of a false positive that was worse than the error it
    was looking for. The citation auditor was asked to confirm that a figure
    matched the records cited for it -- which, for an aggregate, means adding
    up nine or ten numbers read out of retrieved text. It got the sum wrong and
    failed a draft whose citations were exactly right.

    A wrong accusation is more expensive than no check: it burns the revision,
    and it teaches whoever reads the audit trail to stop believing it. So the
    auditor no longer does the addition. It calls this, and compares.
    """
    deals = {d["deal_id"]: d for d in _deals()}
    vendors = {v["vendor_id"]: v for v in _vendors()}

    matched, unknown = [], []
    total = 0
    by_outcome: dict[str, dict] = {}
    for raw in identifiers or []:
        ident = str(raw).strip().strip("[]").strip()
        if not ident:
            continue
        if ident in deals:
            d = deals[ident]
            arr = _int(d["arr_usd"])
            total += arr
            outcome = d["outcome"]
            bucket = by_outcome.setdefault(outcome, {"count": 0, "total_usd": 0, "records": []})
            bucket["count"] += 1
            bucket["total_usd"] += arr
            bucket["records"].append(ident)
            matched.append(
                {"record": ident, "kind": "deal", "outcome": outcome,
                 "segment": d["segment_tag"], "amount_usd": arr}
            )
        elif ident in vendors:
            v = vendors[ident]
            spend = _int(v["annual_spend_usd"])
            total += spend
            bucket = by_outcome.setdefault(
                "Annual supplier spend", {"count": 0, "total_usd": 0, "records": []}
            )
            bucket["count"] += 1
            bucket["total_usd"] += spend
            bucket["records"].append(ident)
            matched.append(
                {"record": ident, "kind": "supplier_spend", "outcome": "n/a",
                 "segment": v["segment_tag"], "amount_usd": spend}
            )
        else:
            # Capability, watchlist, interview and position records carry no
            # money. Naming them is correct in a citation; they just do not
            # contribute to a total.
            unknown.append(ident)

    return {
        "analysis": "Exact total for a set of cited records",
        "description": (
            "Use this instead of adding figures yourself. `total_usd` is the sum over every "
            "cited record that carries an amount. Records without an amount (CAP-, T-, WL-, "
            "STRAT-, PRIOR-) are listed under `records_without_an_amount` and are legitimate "
            "citations -- their presence is not an error. Compare `total_usd` with the figure "
            "in the claim: if they match, the aggregate is cited correctly. If the claim mixes "
            "outcomes -- a win rate supported by a lost deal, say -- `totals_by_outcome` will "
            "show it."
        ),
        "records_counted": len(matched),
        "total_usd": total,
        "totals_by_outcome": by_outcome,
        "matched_records": matched,
        "records_without_an_amount": unknown,
    }


class RecordTotalInput(BaseModel):
    identifiers: list[str] = Field(
        ...,
        description=(
            "The record identifiers cited by the claim, e.g. "
            '["D-2202", "D-2206", "CAP-01"].'
        ),
    )


class RecordTotalTool(BaseTool):
    name: str = "Total the records cited by a claim"
    description: str = (
        "Given the record identifiers a claim cites, returns the exact sum of the amounts on "
        "those records, broken down by outcome (won, lost, open, supplier spend). ALWAYS use "
        "this to check an aggregate figure -- a segment total, a win rate, an annual spend -- "
        "rather than adding the numbers yourself. Never do the arithmetic in your head: this "
        "tool reads the complete records and is exact, and a mistaken accusation costs a "
        "revision and discredits the audit."
    )
    args_schema: Type[BaseModel] = RecordTotalInput

    def _run(self, identifiers: list[str]) -> str:
        return json.dumps(total_for_records(identifiers), indent=2)[:7000]
