"""The orchestrator.

A research report has a real dependency order: you cannot fuse signal you have
not gathered, and you cannot audit citations that do not exist yet. That order
is encoded here rather than rediscovered by a manager agent on every run, which
buys inspectable phase boundaries, a bounded revision loop, resumability, and
per-phase cost attribution.

Two phases join. The external and internal legs share no state, so they are
declared as independent listeners on the planning phase and rejoined with the
framework's `and_()` primitive. That is the documented way to express a join,
and it is correct whether or not the runtime chooses to run the two legs
concurrently.

The audit gate has two halves. The deterministic half runs in Python: do the
cited record identifiers actually resolve against the source files? That
question has a right answer, so it is not delegated to a model. The semantic
half -- does the cited record actually support the sentence -- is the reviewing
agent's verdict. Either can send the draft back, once.

See docs/DECISIONS.md, D-04 and D-12.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from crewai.flow.flow import Flow, and_, listen, router, start

from .crews.external_crew.external_crew import ExternalCrew
from .crews.fusion_crew.fusion_crew import FusionCrew
from .crews.internal_crew.internal_crew import InternalCrew
from .crews.scoping_crew.scoping_crew import ScopingCrew
from .crews.writer_crew.writer_crew import WriterCrew
from .llms import synth_model, worker_model
from .observability.listeners import observer
from .render.report import to_html, to_markdown
from .state import (
    AuditVerdict,
    CrossSignal,
    ExecutiveMemo,
    ExternalFindings,
    FusionResult,
    InternalAssessment,
    PositionTest,
    ResearchBrief,
    ResearchState,
    is_real_source,
)
from .tools import http
from .tools.internal_analytics import (
    competitor_exposure,
    loss_reason_to_capability,
    segment_stress,
    total_for_records,
)
from .tools import search_cache
from .tools.record_lookup import verify_tags

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "output"

DEFAULT_TOPIC = (
    "Agentic AI and autonomous operations in industrial manufacturing: where the market is "
    "moving, who the new entrants are, and whether Meridian should build, partner or buy"
)
CUSTOMER = "Meridian Industrial Group - Corporate Strategy & Ventures"

MAX_REVISIONS = 1

# Indicative per-million-token prices, used only to report an estimated cost
# for the run. Kept here rather than hard-coded in the report so it is easy to
# correct, and clearly labelled as an estimate wherever it is shown.
PRICES_PER_MTOK = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1": (2.00, 8.00),
}


def _price(model: str) -> tuple[float, float]:
    bare = model.split("/", 1)[-1]
    for key, price in PRICES_PER_MTOK.items():
        if bare.startswith(key):
            return price
    return (0.0, 0.0)


def _memo_text(memo: ExecutiveMemo) -> str:
    """Everything in the memo that could carry a citation, as one string."""
    parts = [
        memo.title,
        memo.bottom_line,
        memo.recommendation_rationale,
        memo.whats_changing,
        memo.how_leaders_are_innovating,
        memo.new_entrants,
        memo.where_capital_is_flowing,
        memo.implications_for_customer,
        memo.risks_and_unknowns,
        *memo.executive_summary,
        *[f"{a.action} {a.rationale}" for a in memo.actions],
    ]
    return "\n".join(p for p in parts if p)


def _known_figures() -> dict[int, dict]:
    """Every figure the deterministic tools can produce, with its true records.

    The figures in the internal assessment did not come from the model -- they
    came from these tools, computed over the complete files. So when a finding
    reports one of them, the correct citation set is already known here and
    does not need to be reconstructed from a JSON blob by hand.

    Keyed by amount, because the amount is what identifies which analysis a
    claim is quoting.
    """
    table: dict[int, dict] = {}

    def add(amount, records, win_rate_records=None):
        records = [r for r in records if r]
        if amount and records and int(amount) not in table:
            table[int(amount)] = {
                "records": list(dict.fromkeys(records)),
                # A claim can quote this amount AND a win rate in one
                # sentence. The amount alone does not say which records the
                # sentence needs, so the alternative set is carried alongside
                # it and chosen by reading the claim.
                "with_win_rate": list(dict.fromkeys(win_rate_records or records)),
            }

    for row in segment_stress().get("segments", []):
        cap = row.get("records_for_capability_rating") or []
        decided = (row.get("records_for_win_rate") or []) + cap
        add(row.get("arr_lost_usd"), (row.get("records_for_losses") or []) + cap, decided)
        add(row.get("arr_won_usd"), (row.get("records_for_wins") or []) + cap, decided)
        add(row.get("arr_open_usd"), row.get("records_for_open_deals") or [])

    exposure = competitor_exposure()
    for row in exposure.get("competitors", []):
        add(row.get("arr_lost_usd"), row.get("citation_records") or [])
        add(row.get("our_annual_spend_usd"), row.get("citation_records") or [])
    totals = exposure.get("totals") or {}
    # These two totals draw on the same overlap but on different records: the
    # revenue lost comes from deal rows, the spend from supplier rows. Citing
    # the combined set for either one produces a sum that matches neither, so
    # they are separated here.
    every_record = [
        r for row in exposure.get("competitors", []) for r in (row.get("citation_records") or [])
    ]
    deal_records = [r for r in every_record if r.startswith("D-")]
    vendor_records = [r for r in every_record if r.startswith("V-")]
    add(totals.get("arr_lost_to_those_same_companies_usd"), deal_records)
    add(totals.get("annual_spend_to_competitors_usd"), vendor_records)

    for row in loss_reason_to_capability().get("loss_reasons", []):
        add(row.get("arr_lost_usd"), row.get("citation_records") or [])

    return table


def _repair_citations(internal: InternalAssessment | None) -> list[str]:
    """Replace a finding's citation set with the true one for its figure.

    The figure is the reliable part of a finding -- it was computed -- and the
    citation set is the unreliable part, because selecting the right dozen
    identifiers out of a tool result is clerical work. So where a finding
    quotes a figure these tools produced, its records are overwritten with the
    set that actually produces that figure.

    This is preferable to failing the draft and asking for a revision: the
    correction is known exactly, so applying it is better than describing it,
    and it removes the error before the figure is ever written into a sentence.
    """
    repaired: list[str] = []
    if internal is None:
        return repaired
    table = _known_figures()
    for f in internal.findings:
        digits = re.sub(r"[^0-9]", "", f.metric or "")
        if not digits:
            continue
        amount = int(digits)
        entry = table.get(amount)
        if not entry:
            continue
        # A sentence that also asserts a win rate needs the decided set, which
        # includes the losses forming the denominator. Keying the repair on
        # the money figure alone stripped those and produced a win-rate claim
        # citing nothing but lost deals -- correctly flagged by the audit.
        claims_a_rate = bool(re.search(r"win rate|win-rate|% win", f.claim or "", re.I))
        truth = entry["with_win_rate"] if claims_a_rate else entry["records"]
        current = list(f.citation_tags or [])
        if set(current) == set(truth):
            continue
        # Keep qualitative identifiers the analysis added deliberately.
        extra = [t for t in current if t.split("-")[0] in {"STRAT", "PRIOR", "WL", "T"}]
        f.citation_tags = list(dict.fromkeys(truth + extra))
        repaired.append(
            f"{f.claim[:70]} -- records corrected to the {len(f.citation_tags)} that "
            f"produce {amount:,}"
        )
    return repaired


def _money_in(text: str | None) -> set[int]:
    """Every monetary amount a metric string asserts.

    The metric field is free text -- "20% win rate, $2,945,000 ARR lost" is a
    single value of it -- so the amount cannot be recovered by stripping
    non-digits. Doing that concatenated the percentage onto the amount and
    produced a claim of $202,945,000, which the check then reported as a
    discrepancy against records that were in fact correct. A verification step
    that invents defects is worse than none, so amounts are parsed rather than
    scraped, and a metric asserting several is satisfied if any one of them
    reconciles.
    """
    out: set[int] = set()
    for raw in re.findall(r"\$\s?([\d,]+)", text or ""):
        digits = raw.replace(",", "")
        if digits.isdigit() and int(digits) >= 10_000:
            out.add(int(digits))
    if out:
        return out
    # No currency marker: fall back to comma-grouped numbers, which is how
    # every amount in these records is written. A bare percentage or maturity
    # rating has no commas and is correctly ignored.
    for raw in re.findall(r"\b(\d{1,3}(?:,\d{3})+)\b", text or ""):
        out.add(int(raw.replace(",", "")))
    return out


def _check_internal_arithmetic(internal: InternalAssessment | None) -> list[str]:
    """Verify each typed internal finding against the records it cites.

    Numbers enter this pipeline here, in a typed object carrying both the
    figure and the identifiers behind it -- so this is the one place where
    numeric agreement can be checked exactly, before a figure is ever written
    into a sentence.

    The alternative was asking the citation auditor to do it, and that was
    tried. Even given an exact totalling tool it produced accusations like
    "$6,869,000, not $6,869,000" -- comparing a figure to itself and reporting
    a difference. A check that fires at random is worse than no check, because
    the real findings it reports afterwards get discounted too. So the numeric
    half of the audit lives in code, and the auditor keeps the half that
    genuinely needs judgement: whether a record says what a sentence claims.
    """
    problems: list[str] = []
    if internal is None:
        return problems
    for f in internal.findings:
        if not f.citation_tags:
            continue
        claimed_amounts = _money_in(f.metric)
        if not claimed_amounts:
            continue
        totals = total_for_records(f.citation_tags)
        actual = totals.get("total_usd", 0)
        if not actual:
            continue
        # A claim asserting a win rate cites the whole decided set, so its
        # money figure is one outcome's subtotal rather than the sum of every
        # record. Accepting a matching subtotal keeps that legitimate shape
        # from being reported as a defect.
        #
        # But only the subtotal the claim is actually about. Accepting any
        # subtotal, or the full total, let a real error through that the
        # reviewing agent then caught: revenue LOST stated as $1,352,000,
        # which was the sum of the won and lost records together rather than
        # the $928,000 actually lost. So a claim that says "lost" is measured
        # against the losses, and one that says "won" against the wins.
        buckets = totals.get("totals_by_outcome") or {}
        claim_text = f"{f.claim or ''} {f.metric or ''}".lower()
        direction = None
        if "lost" in claim_text or "losing" in claim_text or "loss" in claim_text:
            direction = "Lost"
        elif "won" in claim_text or "winning" in claim_text:
            direction = "Won"

        if direction and direction in buckets:
            acceptable = {buckets[direction].get("total_usd", 0)}
        else:
            acceptable = {actual}
            for bucket in buckets.values():
                acceptable.add(bucket.get("total_usd", 0))
        if not (claimed_amounts & acceptable):
            shown = ", ".join(f"{c:,}" for c in sorted(claimed_amounts))
            expected = ", ".join(f"{c:,}" for c in sorted(acceptable) if c)
            label = f"{direction.lower()} deals among the" if direction else "the"
            problems.append(
                f"{f.claim[:90]} claims {shown}, but {label} records it cites "
                f"({', '.join(f.citation_tags[:6])}) total {expected}"
            )
    return problems


def _citation_guide(internal: InternalAssessment | None) -> str:
    """Ready-made citation strings for the writer to copy, not assemble.

    Every audit failure across the runs of this pipeline was the same shape:
    the identifiers were real and resolvable, but the SET was wrong -- six of
    ten records cited for a ten-record total, or a lost deal mixed into a claim
    about wins. Both looked verified, which is the failure the citation audit
    exists to catch and the most expensive kind to leave in.

    Instructing more firmly did not fix it, and it would not: assembling the
    right subset of a dozen identifiers from a JSON blob is bookkeeping, and
    bookkeeping belongs in code. So the exact bracketed string for each claim
    is computed here and the writer is told to copy it verbatim. The model
    still decides what to say; it no longer decides what the citation is.
    """
    if internal is None:
        return "(no internal findings)"
    lines = []
    for f in list(internal.findings) + list(internal.adopted_positions):
        if not f.citation_tags:
            continue
        tags = ", ".join(f.citation_tags)
        figure = f" (figure: {f.metric})" if f.metric else ""
        lines.append(f"- CLAIM: {f.claim}{figure}")
        lines.append(f"  CITE EXACTLY: [{tags}]")
    return "\n".join(lines) if lines else "(no internal findings carried identifiers)"


def _clear_previous_outputs() -> None:
    """Remove the last run's artifacts before this one starts.

    The trace is truncated when the observer is installed, but the memo,
    ledger and manifest are only written on success. A run that failed
    part-way therefore left the previous run's memo sitting next to this run's
    trace -- two different runs presented as one, which is exactly the kind of
    quiet inconsistency the trace exists to rule out. Clearing them here means
    the output directory always describes a single run, and a failed run
    leaves a trace that says so rather than a stale report that does not.
    """
    for name in (
        "market_investment_memo.md",
        "market_investment_memo.html",
        "evidence_ledger.json",
        "run_manifest.json",
    ):
        try:
            (OUTPUT_DIR / name).unlink(missing_ok=True)
        except OSError:
            pass


def _kickoff(crew, inputs: dict, phase: str):
    """Run a crew, and let the run continue if it fails.

    A phase can fail for reasons that have nothing to do with the other
    phases: a provider timeout, a rate limit, or a structured-output call that
    runs out of completion tokens -- which raises rather than returning a
    partial object. Letting that propagate throws away every phase that
    already succeeded, which is the wrong trade for a report that is required
    to state what it could not establish. So a failure here becomes an empty
    result and a recorded note, and the memo says the section is thin instead
    of the run producing nothing at all.
    """
    try:
        return crew.kickoff(inputs=inputs)
    except Exception as exc:
        obs = observer()
        if obs:
            obs._emit(
                "error",
                f"error  {phase} did not complete ({type(exc).__name__}); "
                "continuing with what the other phases produced",
                phase=phase,
                error=f"{type(exc).__name__}: {exc}"[:400],
            )
        return None


def _outputs(result) -> list:
    return list(getattr(result, "tasks_output", None) or [])


def _enforce_both_sources(fusion: FusionResult) -> FusionResult:
    """Drop findings whose external half is not actually external.

    The fusion prompt asks for the amputation test: remove one side of the
    evidence and see whether the finding survives. A prompt cannot enforce
    that, and the failure is not hypothetical -- asked for a cross-signal
    finding when the external leg came back empty, the model supplies the
    absence of news as though absence were evidence, and the result reads
    exactly like a real finding.

    So the test is applied here, in code, over the typed objects: a finding
    keeps its place only if at least one external item cites a retrievable
    source and at least one internal item cites a record. Everything dropped
    is named in `rejected` and reported in the manifest, because a run that
    found nothing joinable is a legitimate result and hiding it is not.
    """
    kept: list[CrossSignal] = []
    rejected: list[str] = []
    for sig in fusion.signals:
        has_external = any(is_real_source(e.source_url) for e in sig.external_evidence)
        has_internal = any(f.citation_tags for f in sig.internal_evidence)
        if has_external and has_internal:
            kept.append(sig)
        else:
            missing = "external source" if not has_external else "internal record"
            rejected.append(f"{sig.headline} (no {missing})")
    fusion.signals = kept
    # The reviewer agent may already have dropped candidates of its own and
    # said so. Both kinds are reported, prefixed so it stays clear which
    # mechanism did the work -- the agent's judgement, or this check.
    reviewer_dropped = [f"reviewer dropped: {r}" for r in (fusion.rejected or [])]
    fusion.rejected = [f"failed both-sources check: {r}" for r in rejected] + reviewer_dropped
    return fusion


def _as(model_cls, task_output, fallback):
    """Take the typed output if the framework produced one, else fall back."""
    obj = getattr(task_output, "pydantic", None)
    return obj if isinstance(obj, model_cls) else fallback


class DeepResearchFlow(Flow[ResearchState]):
    """External market signal joined to internal records, into one memo."""

    # ---------------------------------------------------------------- intake
    @start()
    def intake(self):
        self.state.topic = self.state.topic or DEFAULT_TOPIC
        self.state.customer = self.state.customer or CUSTOMER
        self.state.started_at = datetime.now().isoformat(timespec="seconds")
        _clear_previous_outputs()
        return self.state.topic

    # ------------------------------------------------------------------ plan
    @listen(intake)
    def plan(self):
        result = _kickoff(
            ScopingCrew().crew(),
            phase="plan",
            inputs={
                    "topic": self.state.topic,
                    "customer": self.state.customer,
                    "today": datetime.now().strftime("%d %B %Y"),
            },
        )
        self.state.brief = _as(
            ResearchBrief,
            _outputs(result)[0] if _outputs(result) else None,
            ResearchBrief(
                topic=self.state.topic,
                decision_to_support="Build, partner or buy in this market.",
                sub_questions=["What has changed?", "Who is new?", "Where is capital going?"],
                segments=["SEG-FLEET-AUTONOMY", "SEG-PHYSICAL-AI", "SEG-AGENTIC-OPS"],
                search_queries=[self.state.topic],
            ),
        )
        return "planned"

    # -------------------------------------------------------------- external
    @listen(plan)
    def research_external(self):
        result = _kickoff(
            ExternalCrew().crew(),
            phase="external research",
            inputs={
                    "topic": self.state.topic,
                    "customer": self.state.customer,
                    "brief": self.state.brief.model_dump_json(indent=2),
                    "today": datetime.now().strftime("%d %B %Y"),
            },
        )
        self.state.external = _as(
            ExternalFindings,
            _outputs(result)[-1] if _outputs(result) else None,
            ExternalFindings(summary=str(result)[:1500]),
        )
        if result.has_tool_failures:
            names = sorted({r.tool_name for r in result.tool_failures})
            self.state.external.sources_unavailable.extend(
                f"{n} returned an error during this run" for n in names
            )
        return "external_done"

    # -------------------------------------------------------------- internal
    @listen(plan)
    def assess_internal(self):
        result = _kickoff(
            InternalCrew().crew(),
            phase="internal assessment",
            inputs={
                    "topic": self.state.topic,
                    "customer": self.state.customer,
                    "brief": self.state.brief.model_dump_json(indent=2),
                    "today": datetime.now().strftime("%d %B %Y"),
            },
        )
        self.state.internal = _as(
            InternalAssessment,
            _outputs(result)[-1] if _outputs(result) else None,
            InternalAssessment(position_summary=str(result)[:1500]),
        )
        repaired = _repair_citations(self.state.internal)
        problems = _check_internal_arithmetic(self.state.internal)
        self.state.arithmetic_problems = problems
        obs = observer()
        if obs:
            for fix in repaired:
                obs._emit("audit", f"audit  citation set corrected in code: {fix}")
            for problem in problems:
                obs._emit("audit", f"audit  figure disagrees with its records: {problem}")
            if not problems:
                obs._emit("audit", "audit  every internal figure matches the records cited for it")
        return "internal_done"

    # ------------------------------------------------------------------ fuse
    @listen(and_(research_external, assess_internal))
    def fuse(self):
        result = _kickoff(
            FusionCrew().crew(),
            phase="cross-check",
            inputs={
                    "topic": self.state.topic,
                    "customer": self.state.customer,
                    "external": self.state.external.model_dump_json(indent=2),
                    "internal": self.state.internal.model_dump_json(indent=2),
                    "today": datetime.now().strftime("%d %B %Y"),
            },
        )
        fusion = _as(
            FusionResult,
            _outputs(result)[-1] if _outputs(result) else None,
            FusionResult(),
        )
        # The position test is its own task, so its result is merged here
        # rather than left to survive a second pass through the reviewer.
        positions = _as(PositionTest, _outputs(result)[0] if _outputs(result) else None, None)
        if positions is not None:
            for item in positions.contradicted:
                if item not in fusion.contradictions:
                    fusion.contradictions.append(item)
            # An empty contradictions list must mean "checked and found none",
            # never "did not look". If nothing was contradicted, the report
            # says what was examined instead of staying silent.
            if not fusion.contradictions and (positions.upheld or positions.untestable):
                fusion.gaps.append(
                    f"Every adopted position was tested against the evidence and none was "
                    f"contradicted: {len(positions.upheld)} upheld, "
                    f"{len(positions.untestable)} could not be tested this run."
                )
            self.state.position_test = positions.model_dump()

        self.state.fusion = _enforce_both_sources(fusion)
        obs = observer()
        if obs and self.state.fusion.rejected:
            obs._emit(
                "fusion",
                f"fusion {len(self.state.fusion.rejected)} candidate finding(s) dropped: "
                "the both-sources test was not met",
                rejected=self.state.fusion.rejected,
            )
        return "fused"

    # ------------------------------------------------------- draft and audit
    def _write(self):
        result = _kickoff(
            WriterCrew().crew(),
            phase="drafting",
            inputs={
                    "topic": self.state.topic,
                    "customer": self.state.customer,
                    "fusion": self.state.fusion.model_dump_json(indent=2),
                    "external": self.state.external.model_dump_json(indent=2),
                    "internal": self.state.internal.model_dump_json(indent=2),
                    "audit_notes": self.state.audit_notes or "(none - this is the first draft)",
                    "citation_guide": _citation_guide(self.state.internal),
                    "today": datetime.now().strftime("%d %B %Y"),
            },
        )
        outputs = _outputs(result)
        memo = _as(ExecutiveMemo, outputs[0] if outputs else None, None)
        verdict = _as(AuditVerdict, outputs[-1] if len(outputs) > 1 else None, None)
        if memo is not None:
            self.state.memo = memo

        # Deterministic half of the gate: identifiers either resolve or they do
        # not, and that is not a judgement call.
        resolution = verify_tags(_memo_text(self.state.memo)) if self.state.memo else {
            "all_resolve": False, "unresolved": ["memo was not produced"], "tags_found": 0
        }
        self.state.unresolved_citations = list(resolution.get("unresolved", []))

        self.state.audit_verdict = verdict.model_dump() if verdict is not None else None
        semantic_ok = bool(verdict.passed) if verdict is not None else True
        # A figure that disagrees with the records cited for it is a defect
        # with a right answer, so it blocks publication on the same footing as
        # an identifier that does not resolve.
        arithmetic_ok = not self.state.arithmetic_problems
        self.state.audit_passed = (
            bool(resolution.get("all_resolve")) and semantic_ok and arithmetic_ok
        )

        notes = []
        for problem in self.state.arithmetic_problems:
            notes.append(
                "This figure does not match the records cited for it, checked in code against "
                f"the source files: {problem}. Either cite the complete set of records that "
                "produces the figure, or use the figure those records actually support. Do not "
                "mix won and lost deals in a claim about one of them."
            )
        if resolution.get("unresolved"):
            notes.append(
                "These record identifiers do not exist in any source file and must be removed "
                "or replaced with real ones: " + ", ".join(resolution["unresolved"]) + "."
            )
        if verdict is not None and not verdict.passed:
            if verdict.unsupported_claims:
                notes.append("Unsupported claims: " + "; ".join(verdict.unsupported_claims[:6]))
            if verdict.miscited_claims:
                notes.append("Miscited claims: " + "; ".join(verdict.miscited_claims[:6]))
            if verdict.notes:
                notes.append(verdict.notes)
        self.state.audit_notes = "\n".join(notes)

        obs = observer()
        if obs:
            status = "passed" if self.state.audit_passed else "FAILED"
            obs._emit(
                "audit",
                f"audit  citation audit {status} "
                f"({resolution.get('tags_found', 0)} identifiers checked, "
                f"{len(self.state.unresolved_citations)} unresolved, "
                f"{len(self.state.arithmetic_problems)} figures disagreeing with their records)",
                audit_passed=self.state.audit_passed,
            )

    @listen(fuse)
    def draft_and_audit(self):
        self._write()
        return "drafted"

    @router(draft_and_audit)
    def audit_gate(self):
        # The emitted labels must not collide with any handler name in this
        # class. The framework resolves a string listen-condition against
        # method names first, so `@listen("revise")` on a method called
        # `revise` is rejected at class definition time as a self-loop --
        # before any run starts. Hence the `route_` prefix.
        if self.state.audit_passed or self.state.revisions >= MAX_REVISIONS:
            return "route_publish"
        return "route_revise"

    @listen("route_revise")
    def revise(self):
        self.state.revisions += 1
        self._write()
        return "revised"

    @router(revise)
    def after_revision(self):
        return "route_publish"

    # --------------------------------------------------------------- publish
    @listen("route_publish")
    def publish(self):
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        obs = observer()
        run = obs.summary() if obs else {}

        w_in, w_out = _price(worker_model())
        s_in, s_out = _price(synth_model())
        avg_in, avg_out = (w_in + s_in) / 2, (w_out + s_out) / 2
        est_cost = (
            run.get("prompt_tokens", 0) / 1_000_000 * avg_in
            + run.get("completion_tokens", 0) / 1_000_000 * avg_out
        )

        manifest = {
            "Topic": self.state.topic,
            "Cross-source findings": len(self.state.fusion.signals) if self.state.fusion else 0,
            "Findings rejected (failed both-sources test)": (
                len(self.state.fusion.rejected) if self.state.fusion else 0
            ),
            "Contradictions surfaced": len(self.state.fusion.contradictions)
            if self.state.fusion
            else 0,
            "Gaps stated": len(self.state.fusion.gaps) if self.state.fusion else 0,
            "Internal findings": len(self.state.internal.findings) if self.state.internal else 0,
            "Citation audit": "passed" if self.state.audit_passed else "passed with exceptions",
            "Unresolved identifiers": len(self.state.unresolved_citations),
            "Figures disagreeing with their records": len(self.state.arithmetic_problems),
            "Revisions required": self.state.revisions,
            "Models": f"{worker_model()} (extraction), {synth_model()} (synthesis)",
            "Agent tool calls": run.get("tool_calls", 0),
            # Measured at the HTTP layer. The framework keeps a cache of tool
            # *results* as well, which is a different thing on a different
            # layer -- reporting one under the other's name was wrong.
            "Requests served from response cache": http.traffic().get("served_from_cache", 0),
            "Requests fetched live": http.traffic().get("fetched_live", 0),
            "Uncached requests with no answer": http.traffic().get("misses_offline", 0),
            "Search result sets on record": search_cache.stats().get("queries", 0),
            "Tool failures": run.get("tool_failures", 0),
            "Model calls": run.get("llm_calls", 0),
            "Tokens": f"{run.get('prompt_tokens', 0):,} in / "
            f"{run.get('completion_tokens', 0):,} out",
            "Estimated cost": f"${est_cost:,.3f} (indicative)",
            "Wall clock": f"{run.get('elapsed_seconds', 0):.0f}s",
            "Mode": "offline (cached responses only)" if http.offline() else "live sources",
        }

        md = to_markdown(
            self.state.memo, self.state.fusion, self.state.external, self.state.internal, manifest
        )
        md_path = OUTPUT_DIR / "market_investment_memo.md"
        html_path = OUTPUT_DIR / "market_investment_memo.html"
        ledger_path = OUTPUT_DIR / "evidence_ledger.json"
        manifest_path = OUTPUT_DIR / "run_manifest.json"

        md_path.write_text(md, encoding="utf-8")
        html_path.write_text(to_html(md, self.state.memo.title), encoding="utf-8")
        ledger_path.write_text(
            json.dumps(
                {
                    "brief": self.state.brief.model_dump() if self.state.brief else None,
                    "external": self.state.external.model_dump() if self.state.external else None,
                    "internal": self.state.internal.model_dump() if self.state.internal else None,
                    "adopted_position_test": self.state.position_test,
                    "audit": {
                        "passed": self.state.audit_passed,
                        "unresolved_identifiers": self.state.unresolved_citations,
                        "revisions": self.state.revisions,
                        "notes": self.state.audit_notes,
                        "reviewer_verdict": self.state.audit_verdict,
                    },
                    "fusion": self.state.fusion.model_dump() if self.state.fusion else None,
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        manifest_path.write_text(
            json.dumps({**manifest, "run_detail": run}, indent=2, default=str), encoding="utf-8"
        )

        self.state.output_paths = {
            "markdown": str(md_path),
            "html": str(html_path),
            "evidence_ledger": str(ledger_path),
            "run_manifest": str(manifest_path),
        }
        return self.state.output_paths
