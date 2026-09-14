"""Typed contracts between phases, and the flow's state.

Agents hand each other these objects rather than prose. Free text degrades at
every hop -- a citation becomes "according to a recent report", a hedge becomes
a fact -- so provenance is a required field here instead of a stylistic choice.
A missing source is a validation error, not something a reader has to notice.

See docs/DECISIONS.md, D-05.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Confidence = Literal["high", "medium", "low"]
Urgency = Literal["act_now", "decide_this_quarter", "monitor", "no_action"]
Recommendation = Literal["build", "partner", "buy", "hybrid", "exit", "no_action"]


# --------------------------------------------------------------------------
# Phase 1 -- scoping
# --------------------------------------------------------------------------
class ResearchBrief(BaseModel):
    """What we are actually going to investigate, and why."""

    topic: str
    decision_to_support: str = Field(
        ..., description="The specific decision this report has to enable."
    )
    sub_questions: list[str] = Field(..., description="4-7 answerable sub-questions.")
    segments: list[str] = Field(
        ..., description="Market segments to track, using the internal segment tags where known."
    )
    search_queries: list[str] = Field(
        ..., description="Concrete queries for the external research leg."
    )
    filing_queries: list[str] = Field(
        default_factory=list,
        description="Quoted-phrase queries suitable for regulatory filing full-text search.",
    )


# --------------------------------------------------------------------------
# Phase 2 -- external
# --------------------------------------------------------------------------
def is_real_source(url: str) -> bool:
    """Whether a source string is an actual retrievable location.

    A model asked for a URL when it has none will supply a plausible-looking
    placeholder -- "source unavailable", "N/A", "internal analysis" -- and a
    plain `str` field accepts every one of them. The schema said a claim
    without a URL is not evidence; this is what makes that true.
    """
    u = (url or "").strip().lower()
    if not u.startswith(("http://", "https://")):
        return False
    return len(u) > len("https://") and " " not in u


class Evidence(BaseModel):
    """One externally sourced claim. A claim without a URL is not evidence."""

    claim: str
    source_url: str = Field(..., description="The URL this claim came from.")
    source_title: str = ""
    source_type: str = Field(
        "web", description="e.g. regulatory_filing, news, research_preprint, community, reference"
    )
    published: str = Field("", description="Publication date if known, ISO format.")
    confidence: Confidence = "medium"
    entities: list[str] = Field(default_factory=list, description="Companies or bodies named.")
    segment: str = Field("", description="Segment tag this speaks to, if any.")


class ExternalFindings(BaseModel):
    whats_changing: list[Evidence] = Field(default_factory=list)
    incumbent_moves: list[Evidence] = Field(default_factory=list)
    new_entrants: list[Evidence] = Field(default_factory=list)
    capital_flows: list[Evidence] = Field(default_factory=list)
    summary: str = ""
    sources_unavailable: list[str] = Field(
        default_factory=list,
        description="Sources that could not be reached, so the report can say so honestly.",
    )


# --------------------------------------------------------------------------
# Phase 3 -- internal
# --------------------------------------------------------------------------
class InternalFinding(BaseModel):
    """One claim about our own position, traceable to exact records."""

    claim: str
    citation_tags: list[str] = Field(
        ..., description="Record identifiers backing this claim, e.g. ['D-2201', 'CAP-02']."
    )
    metric: str = Field("", description="The specific figure, if the claim is quantitative.")
    segment: str = ""
    implication: str = ""


class InternalAssessment(BaseModel):
    position_summary: str = ""
    findings: list[InternalFinding] = Field(default_factory=list)
    strongest_segments: list[str] = Field(default_factory=list)
    weakest_segments: list[str] = Field(default_factory=list)
    adopted_positions: list[InternalFinding] = Field(
        default_factory=list,
        description=(
            "What this company currently believes, quoted from the positions it "
            "has formally adopted, with record identifiers. A separate field "
            "because the cross-check needs something specific to contradict: "
            "asked for it as part of a general findings list, the analysis "
            "reports it only sometimes, and the contradiction is then found by "
            "luck rather than by design."
        ),
    )


# --------------------------------------------------------------------------
# Phase 4 -- fusion. This is the point of the whole system.
# --------------------------------------------------------------------------
class CrossSignal(BaseModel):
    """A conclusion that requires BOTH corpora.

    If a row here could have been written from the public web alone, or from
    the internal files alone, it does not belong in this list.
    """

    headline: str = Field(..., description="The finding, stated as a conclusion not a topic.")
    segment: str = ""
    entities: list[str] = Field(default_factory=list)
    external_evidence: list[Evidence] = Field(
        ..., description="At least one externally sourced item, with URLs."
    )
    internal_evidence: list[InternalFinding] = Field(
        ..., description="At least one internal finding, with record identifiers."
    )
    why_this_needs_both: str = Field(
        ..., description="What is knowable only by joining the two sides."
    )
    implication: str
    urgency: Urgency = "monitor"
    confidence: Confidence = "medium"


class PositionTest(BaseModel):
    """The result of testing what the company believes against the evidence.

    Its own task and its own contract because it kept losing to the findings
    list. Asked to produce findings AND test adopted positions in one step,
    the reviewer folds a contradiction into a finding and leaves the
    contradiction list empty -- so the report stops saying the one thing only
    this system can say. Separating the task, then merging the result in code,
    means a full findings list can no longer crowd it out.
    """

    contradicted: list[str] = Field(
        default_factory=list,
        description=(
            "Each adopted position the external evidence now contradicts: the "
            "position quoted, its record identifier, and what overtakes it."
        ),
    )
    upheld: list[str] = Field(
        default_factory=list,
        description="Adopted positions the evidence still supports, with identifiers.",
    )
    untestable: list[str] = Field(
        default_factory=list,
        description="Adopted positions this run found no evidence either way on.",
    )


class FusionResult(BaseModel):
    signals: list[CrossSignal] = Field(default_factory=list)
    contradictions: list[str] = Field(
        default_factory=list,
        description="Where external evidence contradicts an internally held position.",
    )
    gaps: list[str] = Field(
        default_factory=list, description="What we could not establish, stated plainly."
    )
    rejected: list[str] = Field(
        default_factory=list,
        description=(
            "Candidate findings dropped because their external half carried no "
            "retrievable source. Reported, not hidden -- a rejected finding is "
            "information about the run."
        ),
    )


# --------------------------------------------------------------------------
# Phase 5 -- the memo
# --------------------------------------------------------------------------
class Action(BaseModel):
    horizon: Literal["30_days", "60_days", "90_days"]
    action: str
    owner: str = Field("", description="Role or named internal owner from the records.")
    rationale: str = ""


class ExecutiveMemo(BaseModel):
    """The fixed report structure. Narrative fields are markdown."""

    title: str
    prepared_for: str = ""
    executive_summary: list[str] = Field(..., description="Exactly 5 bullets, each a conclusion.")
    bottom_line: str = Field(..., description="One paragraph. The answer, stated plainly.")
    recommendation: Recommendation
    recommendation_rationale: str
    confidence: Confidence
    whats_changing: str
    how_leaders_are_innovating: str
    new_entrants: str
    where_capital_is_flowing: str
    implications_for_customer: str
    risks_and_unknowns: str = Field(
        ..., description="Required. Contradictions, gaps and what would change the answer."
    )
    actions: list[Action] = Field(default_factory=list)


class AuditVerdict(BaseModel):
    """The reviewing agent's judgement on a draft.

    Semantic half of the audit gate. The deterministic half -- do the cited
    record identifiers actually resolve -- is computed in Python by
    `tools.record_lookup.verify_tags`, because that question has a right
    answer and should not be delegated to a model.
    """

    passed: bool = Field(..., description="False if any claim is unsupported.")
    unsupported_claims: list[str] = Field(
        default_factory=list, description="Verbatim claims lacking adequate support."
    )
    miscited_claims: list[str] = Field(
        default_factory=list,
        description="Claims whose cited record exists but does not say what is claimed.",
    )
    notes: str = Field("", description="Specific instructions for the revision.")


# --------------------------------------------------------------------------
# Flow state
# --------------------------------------------------------------------------
class ResearchState(BaseModel):
    """Structured flow state. The framework injects `id` automatically."""

    topic: str = ""
    customer: str = "Meridian Industrial Group - Corporate Strategy & Ventures"
    brief: ResearchBrief | None = None
    external: ExternalFindings | None = None
    internal: InternalAssessment | None = None
    fusion: FusionResult | None = None
    memo: ExecutiveMemo | None = None

    # Audit gate
    audit_passed: bool = False
    audit_notes: str = ""
    unresolved_citations: list[str] = Field(default_factory=list)
    audit_verdict: dict | None = None
    position_test: dict | None = None
    arithmetic_problems: list[str] = Field(default_factory=list)
    revisions: int = 0

    # Run accounting
    started_at: str = ""
    output_paths: dict[str, str] = Field(default_factory=dict)
