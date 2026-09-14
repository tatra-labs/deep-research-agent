"""The flow as a picture, and which part of it was running at a given moment.

The timeline answers "how long did each phase take". It cannot answer "what is
this system, and what does one part hand to the next" -- and that is the first
question from anyone seeing it for the first time. A waterfall of bars implies
a straight line through the phases, when the shape that matters here is a fork
(two research legs running independently), a join (the cross-check waits for
both), and a loop (the audit can send a draft back).

So this module holds the topology: the nodes, where they sit, what flows along
each edge, and -- given a trace and a moment in it -- which parts were running
then. It is data and arithmetic only. The drawing lives in the interface, the
same way `trace.py` shapes a run and leaves the rendering alone.

Two things are deliberately derived rather than restated. The agents in each
crew come from `trace.AGENT_CREW`, and every node is named for a phase the
trace actually records, so a renamed phase or a new agent cannot leave the
picture quietly wrong while the numbers beside it stay right.
"""

from __future__ import annotations

from . import trace as tracelib

CANVAS = (1320, 476)

# Short enough to fit in a box. The long-form labels in `trace.PHASE_LABELS`
# are for prose; these are for a diagram, where the surrounding structure
# already supplies most of the meaning.
SHORT_LABEL = {
    "intake": "Framing",
    "plan": "Scoping",
    "research_external": "Public sources",
    "assess_internal": "Internal records",
    "fuse": "Cross-check",
    "draft_and_audit": "Write & verify",
    "audit_gate": "Publish?",
    "revise": "Revise",
    "publish": "Publish",
}

# What each phase hands to the next. The names are the Pydantic classes in
# state.py rather than a narrative gloss, because "agents hand each other
# structured forms, not paragraphs" is a claim this view should let someone
# check rather than accept. The plain reading is the tooltip.
PAYLOAD_PLAIN = {
    "the question": "The topic and the customer, normalised and stamped.",
    "ResearchBrief": "Sub-questions, the segments to cover, and the search queries.",
    "ExternalFindings": "What the public record says, with a URL behind each claim.",
    "InternalAssessment": "What our own records say, with a record identifier behind each figure.",
    "FusionResult": "Only the findings that needed both sides, plus the ones rejected.",
    "Memo + audit": "The draft, and the auditor's verdict on every claim in it.",
    "sent back": "The audit refused the draft, so it goes round once.",
    "re-checked": "The revision passed its own audit.",
    "passed": "Every identifier resolved, and every figure matched its records.",
}

_HEAD_H = 34    # phase label plus crew name
_ROW_H = 15     # one agent
_PAD_B = 9
_STEP_H = 46    # a node with no crew behind it


def agents_of(crew: str) -> list[str]:
    """The agents in a crew, in the order they are declared."""
    return [a for a, c in tracelib.AGENT_CREW.items() if c == crew]


def _crew_node(node_id: str, crew: str, x: int, w: int, cy: int) -> dict:
    names = agents_of(crew)
    h = _HEAD_H + _ROW_H * len(names) + _PAD_B
    return {
        "id": node_id,
        "crew": crew,
        "agents": names,
        "x": x,
        "w": w,
        "h": h,
        "y": round(cy - h / 2),
        "cy": cy,
    }


def _step_node(node_id: str, x: int, w: int, cy: int) -> dict:
    return {
        "id": node_id,
        "crew": None,
        "agents": [],
        "x": x,
        "w": w,
        "h": _STEP_H,
        "y": round(cy - _STEP_H / 2),
        "cy": cy,
    }


# Four lanes. The two research phases sit above and below the spine because
# they run at the same time; putting them in a row would draw a sequence that
# does not happen.
_MID, _TOP, _LOW, _LOOP = 210, 72, 326, 418

NODES: list[dict] = [
    _step_node("intake", 8, 92, _MID),
    _crew_node("plan", "ScopingCrew", 180, 150, _MID),
    _crew_node("research_external", "ExternalCrew", 364, 170, _TOP),
    _crew_node("assess_internal", "InternalCrew", 364, 170, _LOW),
    _crew_node("fuse", "FusionCrew", 568, 170, _MID),
    _crew_node("draft_and_audit", "WriterCrew", 818, 170, _MID),
    _step_node("audit_gate", 1068, 92, _MID),
    _crew_node("revise", "WriterCrew", 818, 170, _LOOP),
    _step_node("publish", 1206, 106, _MID),
]

NODE_BY_ID = {n["id"]: n for n in NODES}


def _right(node_id: str) -> tuple[int, int]:
    n = NODE_BY_ID[node_id]
    return n["x"] + n["w"], n["cy"]


def _left(node_id: str) -> tuple[int, int]:
    n = NODE_BY_ID[node_id]
    return n["x"], n["cy"]


def _bottom(node_id: str) -> tuple[int, int]:
    n = NODE_BY_ID[node_id]
    return n["x"] + n["w"] // 2, n["y"] + n["h"]


def _top(node_id: str) -> tuple[int, int]:
    n = NODE_BY_ID[node_id]
    return n["x"] + n["w"] // 2, n["y"]


def _straight(a: tuple[int, int], b: tuple[int, int]) -> str:
    return f"M {a[0]},{a[1]} L {b[0]},{b[1]}"


def _curve(a: tuple[int, int], b: tuple[int, int]) -> str:
    """A horizontal-tangent bend, so edges leave and arrive level."""
    mid = (a[0] + b[0]) / 2
    return f"M {a[0]},{a[1]} C {mid},{a[1]} {mid},{b[1]} {b[0]},{b[1]}"


def _loop_back() -> str:
    """Gate down and left into the revision, the one edge that goes backwards."""
    sx, sy = _bottom("audit_gate")
    tx, ty = _top("revise")
    return f"M {sx},{sy} C {sx},320 1020,{ty} {tx},{ty}"


def _loop_out() -> str:
    """Revision along the bottom and up into publish."""
    sx, sy = _right("revise")
    tx, ty = _bottom("publish")
    return f"M {sx},{sy} C 1120,{sy} {tx},{sy} {tx},{ty}"


# `at` is the phase whose start marks the payload arriving. An edge is drawn in
# flight while that phase is running and delivered once it has finished, which
# is what "the handover happened" means in a trace that records phases rather
# than messages.
EDGES: list[dict] = [
    {
        "from": "intake", "to": "plan", "at": "plan", "payload": "the question",
        "d": _straight(_right("intake"), _left("plan")), "label_xy": (140, 196),
    },
    {
        "from": "plan", "to": "research_external", "at": "research_external",
        "payload": "ResearchBrief",
        "d": _curve(_right("plan"), _left("research_external")), "label_xy": (300, 140),
    },
    {
        "from": "plan", "to": "assess_internal", "at": "assess_internal",
        "payload": "ResearchBrief",
        "d": _curve(_right("plan"), _left("assess_internal")), "label_xy": (300, 286),
    },
    {
        "from": "research_external", "to": "fuse", "at": "fuse",
        "payload": "ExternalFindings",
        "d": _curve(_right("research_external"), _left("fuse")), "label_xy": (612, 140),
    },
    {
        "from": "assess_internal", "to": "fuse", "at": "fuse",
        "payload": "InternalAssessment",
        "d": _curve(_right("assess_internal"), _left("fuse")), "label_xy": (612, 288),
    },
    {
        "from": "fuse", "to": "draft_and_audit", "at": "draft_and_audit",
        "payload": "FusionResult",
        "d": _straight(_right("fuse"), _left("draft_and_audit")), "label_xy": (778, 196),
    },
    {
        "from": "draft_and_audit", "to": "audit_gate", "at": "audit_gate",
        "payload": "Memo + audit",
        "d": _straight(_right("draft_and_audit"), _left("audit_gate")), "label_xy": (1028, 196),
    },
    {
        "from": "audit_gate", "to": "revise", "at": "revise", "payload": "sent back",
        "d": _loop_back(), "label_xy": (1182, 300), "only_if": "revise",
    },
    {
        "from": "revise", "to": "publish", "at": "publish", "payload": "re-checked",
        "d": _loop_out(), "label_xy": (1080, 442), "only_if": "revise",
    },
    {
        "from": "audit_gate", "to": "publish", "at": "publish", "payload": "passed",
        "d": _straight(_right("audit_gate"), _left("publish")), "label_xy": (1183, 196),
        "not_if": "revise",
    },
]


# --------------------------------------------------------------------------
# What was happening at a given moment
# --------------------------------------------------------------------------
def agent_spans(records: list[dict]) -> dict[tuple[str, str], dict]:
    """When each agent was demonstrably working, keyed by (phase, agent).

    Derived from the agent's own events rather than from task boundaries. The
    framework reports `TaskStartedEvent.agent_role` as None in this version, so
    a task window cannot be attributed to an agent at all; the events an agent
    emits can. That makes a span a record of observed work rather than of
    declared work, which is the more defensible thing to draw.
    """
    spans: dict[tuple[str, str], dict] = {}
    for rec in records:
        name = tracelib.clean_agent(rec.get("agent"))
        phase = rec.get("_phase")
        if not name or not phase:
            continue
        t = float(rec.get("t", 0.0))
        span = spans.setdefault(
            (phase, name), {"first": t, "last": t, "tool_calls": 0, "llm_calls": 0}
        )
        span["first"] = min(span["first"], t)
        span["last"] = max(span["last"], t)
        if rec.get("kind") == "tool":
            span["tool_calls"] += 1
        elif rec.get("kind") == "llm":
            span["llm_calls"] += 1
    return spans


def agent_windows(node: dict, phase: dict, spans: dict) -> dict[str, dict]:
    """When each agent in a node was working, as a window rather than an instant.

    A span taken straight from the event times is unusable for this: tool calls
    arrive in bursts, so an agent that worked for thirty seconds can show four
    timestamps within the same tenth of a second and then nothing. Lighting it
    up for a tenth of a second would be technically true and completely
    useless.

    Two corrections, both derived from the events rather than assumed:

    Agents whose spans overlap were working at the same time -- that is what
    the external research crew's four analysts do -- so the whole overlapping
    group stays lit until the last of them stops. Ordering them would draw a
    sequence the framework did not run.

    An agent with no events at all still did work; it just did not call a tool,
    which is normal for the memo writer. Its window is taken from the gap its
    neighbours leave, and it is marked `inferred` so the interface can show it
    as the estimate it is.
    """
    order = node["agents"]
    known = {
        name: dict(spans[(node["id"], name)])
        for name in order
        if (node["id"], name) in spans
    }

    # Extend every overlapping group to the end of the group.
    timed = sorted(known.items(), key=lambda kv: kv[1]["first"])
    cluster_end = None
    cluster: list[str] = []
    for name, span in timed:
        if cluster and span["first"] <= cluster_end:
            cluster.append(name)
            cluster_end = max(cluster_end, span["last"])
        else:
            for member in cluster:
                known[member]["last"] = cluster_end
            cluster, cluster_end = [name], span["last"]
    for member in cluster:
        known[member]["last"] = cluster_end

    out: dict[str, dict] = {}
    for index, name in enumerate(order):
        if name in known:
            out[name] = {
                "start": known[name]["first"],
                "end": known[name]["last"],
                "tool_calls": known[name]["tool_calls"],
                "llm_calls": known[name]["llm_calls"],
                "inferred": False,
            }
            continue
        before = [known[n]["last"] for n in order[:index] if n in known]
        after = [known[n]["first"] for n in order[index + 1:] if n in known]
        out[name] = {
            "start": max(before) if before else phase["start"],
            "end": min(after) if after else phase["end"],
            "tool_calls": 0,
            "llm_calls": 0,
            "inferred": True,
        }
    return out


def snapshot(
    phases: list[dict],
    records: list[dict],
    cursor: float,
    *,
    min_window: float = 1.2,
) -> dict:
    """Node and edge states at `cursor` seconds into the run.

    `min_window` gives the instantaneous phases -- intake, the audit gate,
    publish -- a visible moment on screen. They really do take no measurable
    time, and drawing them as never having run would be a worse distortion than
    drawing them as briefly active.
    """
    by_phase = {p["phase"]: p for p in phases}
    spans = agent_spans(records)

    nodes: dict[str, dict] = {}
    for node in NODES:
        phase = by_phase.get(node["id"])
        if phase is None:
            nodes[node["id"]] = {
                "state": "skipped",
                "note": "not needed this run" if node["id"] == "revise" else "did not run",
                "tool_calls": 0,
                "llm_calls": 0,
                "duration": 0.0,
                "agents": {
                    name: {"state": "skipped", "inferred": False, "start": 0.0,
                           "end": 0.0, "tool_calls": 0}
                    for name in node["agents"]
                },
            }
            continue

        start = phase["start"]
        end = max(phase["end"], start + min_window)
        if cursor < start:
            state = "pending"
        elif cursor <= end:
            state = "active"
        else:
            state = "done"
        if state != "pending" and phase.get("problems"):
            state = "failed"

        seen = [r for r in phase["events"] if float(r.get("t", 0.0)) <= cursor]
        agents: dict[str, dict] = {}
        for name, window in agent_windows(node, phase, spans).items():
            if cursor < window["start"]:
                agent_state = "pending"
            elif cursor <= max(window["end"], window["start"] + min_window):
                agent_state = "active"
            else:
                agent_state = "done"
            agents[name] = {
                "state": agent_state,
                "inferred": window["inferred"],
                "start": window["start"],
                "end": window["end"],
                "tool_calls": sum(
                    1
                    for r in seen
                    if r.get("kind") == "tool"
                    and tracelib.clean_agent(r.get("agent")) == name
                ),
            }

        nodes[node["id"]] = {
            "state": state,
            "note": "",
            "tool_calls": sum(1 for r in seen if r.get("kind") == "tool"),
            "llm_calls": sum(1 for r in seen if r.get("kind") == "llm"),
            "duration": phase["duration"],
            "agents": agents,
        }

    # Phases the diagram has no box for -- `after_revision` is one, because it
    # is a router rather than work -- still need a state, so that the cards and
    # the graph agree about what was running. Drawing every router as a box
    # would double the diagram for no gain; leaving them stateless would let
    # the two views disagree, which is worse.
    for phase in phases:
        if phase["phase"] in nodes:
            continue
        start = phase["start"]
        end = max(phase["end"], start + min_window)
        nodes[phase["phase"]] = {
            "state": "pending" if cursor < start else ("active" if cursor <= end else "done"),
            "note": "",
            "tool_calls": phase["tool_calls"],
            "llm_calls": phase["llm_calls"],
            "duration": phase["duration"],
            "agents": {},
            "off_diagram": True,
        }

    edges: list[dict] = []
    for edge in EDGES:
        # The revision loop and the straight-through edge are alternatives.
        # Drawing both would show a route the run did not take.
        if edge.get("only_if") and edge["only_if"] not in by_phase:
            continue
        if edge.get("not_if") and edge["not_if"] in by_phase:
            continue
        target = nodes.get(edge["at"], {}).get("state", "skipped")
        source = nodes.get(edge["from"], {}).get("state", "skipped")
        if target == "active":
            state = "in_flight"
        elif target in {"done", "failed"}:
            state = "delivered"
        elif source in {"done", "failed"} and target == "pending":
            state = "in_flight"
        else:
            state = "idle"
        edges.append({**edge, "state": state})

    return {"nodes": nodes, "edges": edges, "cursor": cursor}


def active_phases(snap: dict) -> list[str]:
    """The phases running at the snapshot's moment, for a one-line caption."""
    return [
        tracelib.PHASE_LABELS.get(node_id, node_id)
        for node_id, info in snap["nodes"].items()
        if info["state"] in {"active", "failed"}
    ]


def active_agents(snap: dict) -> list[str]:
    """The agents working at the snapshot's moment."""
    out: list[str] = []
    for info in snap["nodes"].values():
        for name, agent in info["agents"].items():
            if agent["state"] == "active":
                out.append(name)
    return out
