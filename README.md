# Deep Research Agent

A multi-agent research system that reads the public record **and** a customer's
own data, and produces an executive memo in which every claim is traceable to
the record it came from.

Built on [CrewAI](https://docs.crewai.com) 1.15.21.

---

## The point of it

Anyone can generate a market summary. A corporate strategy team already has
those, and a better one does not change a decision.

What changes a decision is the sentence that puts an external fact and an
internal record side by side — because that sentence is one no analyst and no
vendor could have written alone. So this system is built around a step that
*only keeps a finding if the finding needs both sources*, and a reviewing agent
that refuses to publish anything it cannot trace.

A finding from the demonstration dataset, as an illustration of the shape:

> **You are funding the companies that are beating you.**
> $3.6M/yr flows to ten suppliers who have taken $13.2M of ARR from you, and
> every one of them competes in the same segment where they beat you.
>
> *A market report cannot say this — it needs your supplier ledger. Your ledger
> cannot say it either — on its own it just looks like procurement.*

Four overlaps of that shape are present in the demonstration dataset, planted
deliberately (see Honest disclosures below) — the pipeline's job is to find
them, and the run manifest reports how many it did. Every report is also
required to state what it could not establish, because an executive who
discovers an unstated gap themselves discounts the whole page.

---

## Quick start

**Requires:** Python 3.10–3.13, [uv](https://docs.astral.sh/uv/), and **one**
API credential.

```bash
uv sync                       # install
cp .env.example .env          # add OPENAI_API_KEY
uv run deep_research --check  # verify setup without spending anything
uv run deep_research          # produce the memo
```

Other ways in:

```bash
crewai run                              # framework-native entry point
uv run deep_research --offline          # cached sources only; no search keys needed
uv run deep_research --topic "..."      # your own question
uv run streamlit run app.py             # the interface: memo, findings, and the trace
uv run python -m deep_research.deck     # rebuild deck, script and decision register
```

**One credential runs everything.** `OPENAI_API_KEY` covers both inference and
embeddings. Search credentials (`SERPER_API_KEY`, `TAVILY_API_KEY`) are
**optional** — without them the system falls back to keyless primary sources and
then to a response cache committed to this repository, so a fresh clone still
produces a full report at no cost.

`--check` validates configuration, data and cache without calling a model.

---

## What you get

| Output | |
|---|---|
| `output/market_investment_memo.md` | The memo |
| `output/market_investment_memo.html` | Styled, print-ready |
| `output/evidence_ledger.json` | Every typed evidence item, with sources |
| `output/run_manifest.json` | Findings, audit result, tokens, estimated cost |
| `output/run_trace.jsonl` | Append-only event trace — every phase, tool call and model call |

A real run is committed to [`sample_output/`](sample_output/) so results can be
inspected without running anything. It was produced by `uv run deep_research
--offline`, so it is exactly what a fresh clone reproduces: three cross-source
findings, two contradicted positions, citation audit passed, one revision.

---

## How it works

Five focused crews, orchestrated by a deterministic flow.

```
question ─▶ scope ─▶ ┌ public record ┐ ─▶ cross-check ─▶ write ─▶ audit ─▶ memo
                     └ internal files ┘                             │
                                                           fails ───┘ one revision
```

The two research legs run as independent listeners rejoined with the
framework's `and_()` primitive. The audit is a **router**, not a warning: an
unsupported claim sends the draft back rather than reaching the reader.

The gate runs three checks, and which check belongs where was learned by
getting it wrong:

| Check | Runs in | Because |
|---|---|---|
| Does the identifier exist? | Python | It has a right answer |
| Does the figure match its records? | Python | Also has a right answer — and a model asked to add ten numbers from retrieved text got it wrong, once reporting a discrepancy between $6,869,000 and $6,869,000 |
| Does the record say what the sentence claims? | A separate agent | This one genuinely needs judgement |

### Internal data is read two ways, on purpose

Aggregating 48 deal rows is arithmetic. Semantic retrieval returns a handful of
fragments, so an agent asked to *count* from snippets produces confident wrong
numbers — silently, because the number looks like all the others.

So the split follows the line of "does this question have a right answer":

- **Deterministic tools** compute over the complete files and return exact
  figures *with the record identifiers behind them*.
- **Semantic retrieval** answers what the numbers cannot — what buyers actually
  said, what position was last adopted.

Retrieval runs over a corpus rewritten so every chunk carries its own citation
tag, which is what makes the audit possible:

```
[CRM-DEAL D-2201] Lost deal | account: Midwest Auto Group | segment: SEG-FLEET-AUTONOMY
| annual recurring revenue: $480,000 | competitor: Nexara Robotics
| loss reason: LR-AUTONOMY-ROADMAP
```

---

## Seeing what it did

```bash
uv run streamlit run app.py
```

It opens on the committed sample run, so there is something to look at before
anything has been run and before anything has been spent. Five views:

| | |
|---|---|
| **How it ran** | The run as a timeline, as a live crew diagram, or both — with per-phase attribution of tool calls, model calls and tokens, and which tools did the work |
| **Findings that needed both sides** | Each cross-source finding with its external and internal evidence side by side, plus the candidates that were rejected |
| **Verification** | The three checks, what the audit said, and every adopted position tested against the evidence |
| **The memo** | The report itself, downloadable as Markdown or a print-ready page |
| **Raw run data** | The filterable event stream, the manifest and the full evidence ledger |

Two things about it are deliberate. Everything is rendered **from the run's own
files**, so the interface cannot flatter a run — what it shows is what a
reviewer would find in the repository, and a run from last week renders through
the same code as a live one. And the **trace is a first-class view**, not a
debug panel, because "how do I know it actually did that" is the first question
anyone sensible asks.

### Two ways to watch it, one cursor

The **timeline** is a waterfall of phases. The **crew graph** is the same run
drawn as the system: every crew and the agents inside it, the typed object that
travels along each arrow, and the parts that were running highlighted. Switch
between them at any point, during a run or after it.

Both read one cursor, so moving it moves the highlighted nodes, the marker on
the timeline and which card is open together. That is what lets the two be
checked against each other: the graph claims the two research legs run at the
same time, and the timeline shows the overlapping bars that prove it. Clicking
a box in the graph opens that phase's card.

Each phase is a card that opens on click: what it handed to the next phase, the
agents that did the work and the tools each one called, and every event it
emitted, in order.

### A rendered walkthrough

```bash
uv run python scripts/make_video.py     # deck/run_walkthrough.mp4
```

For anyone who is not driving: a fixed walkthrough of the committed run — the
raw input, then the raw event stream, then every view over it, then the memo
itself. It is generated rather than screen-recorded, frame by frame through the
same renderer the interface uses, so it cannot drift from the repository. Needs
Chrome and ffmpeg; nothing else in the project does.

---

## Putting it online

The interface is a Streamlit app, so it needs somewhere that can hold a process
open. Streamlit keeps a websocket per session to push each rerun, and one
research run takes minutes; a serverless host gives you neither a socket nor
that long, so platforms in that shape -- Vercel and the like -- cannot run this
whatever the configuration says.

**Streamlit Community Cloud** is the fit. From
[share.streamlit.io](https://share.streamlit.io):

| | |
|---|---|
| Repository | `tatra-labs/deep-research-agent` |
| Branch | `master` |
| Main file | `app.py` |
| Python version | 3.12 |
| Secret | `OPENAI_API_KEY = "sk-..."` |

Two files exist only for that host. `requirements.txt` is the resolved lock
exported from `uv.lock`, because Community Cloud reads neither `uv.lock` nor a
`pyproject.toml` that is not Poetry's. `.python-version` pins the interpreter.
Regenerate the first after any dependency change:

```bash
uv export --no-hashes --no-dev --no-emit-project > requirements.txt
```

Nothing else changes. The committed cache and the sample run are in the
repository, so the app opens on a finished run with its full trace before
anyone spends anything.

**A deployed app is a button that spends money.** Anyone with the link can
start a run against your credentials. Adding `DR_DISABLE_RUN = "1"` to the
app's secrets turns the interface into a reader of the runs already committed
-- every view, every trace, no spend -- and takes effect without a redeploy.

---

## Design decisions


| | Decision | Trade-off accepted |
|---|---|---|
| D-04 | Deterministic flow, not one autonomous crew | Less emergent behaviour, in exchange for predictable structure and cost |
| D-05 | Typed contracts between phases, not prose handoffs | More schema to maintain; provenance becomes a required field |
| D-06 | Two model tiers — cheap reads, expensive thinking | Tier assignment is reasoned, not yet measured |
| D-07 | Semantic retrieval over self-citing chunks | We own the chunking instead of using defaults |
| D-08 | Search degrades to zero keys | Breadth narrows when a source dies, rather than the run failing |
| D-09 | Primary sources over general search | Narrower coverage, far higher evidential weight |
| D-10 | Replaced the framework's bundled scraper | One more dependency, roughly half the tokens per source |
| D-11 | Response cache committed to the repo | A stale artifact in git, in exchange for zero-cost reproduction |
| D-12 | Citation audit blocks publication | A failed draft costs a revision |
| D-20 | Arithmetic in code, interpretation in the model | Analyses must be written; the numbers are then checkable |

Four worth calling out, because they came from being wrong:

- **A search provider discontinued its free tier during the build.** I had
  assumed one would carry the external leg. It now degrades through several
  sources to a local cache instead of failing.
- **The framework had moved a full major version** past what I was familiar
  with — memory types replaced, scaffolding format changed, a bundled tool
  removed. Checking first saved a rewrite. Relatedly, `Agent.max_iter` defaults
  to **25** in the installed package, so it is set
  explicitly.
- **The SEC rejects any User-Agent containing a URL** with an unexplained 403.
  Not documented anywhere; found by probing. Contact details are given as an
  email, which satisfies both it and Wikipedia's stricter format policy.
- **The documented embedder config key is silently discarded.** Passing the
  embedding model as `model` — as the published example does — is accepted and
  then dropped by validation, leaving the embedder on a default. The key is
  `model_name`. Caught by asserting on what the framework *kept* rather than
  what it was handed, which is now a startup check.

Four more came from running it, which no amount of reading would have found:

- **The model offered the absence of evidence as evidence.** With the external
  leg empty, it filled the required external half with "no new entrants were
  found", cited to `source unavailable` — a non-empty string, so validation
  passed. Every finding looked complete and the report was internal-only in a
  fusion-shaped table. The both-sources test moved out of the prompt and into
  code, where it is a property rather than a request.
- **My own reviewer was discarding the best finding, by applying my test
  correctly.** "We pay a supplier who is beating us" survives having its
  external evidence removed, so the amputation test dropped it. But stripped of
  market context it degrades from a strategic emergency to a procurement note.
  The test had to ask whether the *conclusion* keeps its force, not whether a
  fact survives.
- **Every citation error was a real identifier in the wrong set** — six of ten
  records cited for a ten-record total, a lost deal cited for a 90% win rate.
  Both resolve, which is what makes them dangerous. Assembling the right subset
  of a dozen identifiers is clerical work, so it moved into code: the exact
  citation string is computed and the writer copies it.
- **The credential audit I built to protect my own keys caught other
  people's.** Scraped article bodies had grown the committed cache to 68 MB and
  brought third-party embedded API keys with them. Pruning page bodies took it
  to 2.2 MB with the audit clean — and the small material is what actually
  reproduces the research.

---

## Verifying the claims

Each of these is checkable rather than asserted.

```bash
# 1. Runs with one credential, from a clean clone
uv sync && uv run deep_research --check

# 2. Runs with NO search credentials, against the committed cache
uv run deep_research --offline

# 3. The audit gate actually catches fabricated citations
uv run python -c "
from deep_research.tools.record_lookup import verify_tags
print(verify_tags('Real [CRM-DEAL D-2201] and invented [CRM-DEAL D-8888]'))"
# -> unresolved: ['D-8888'], all_resolve: False

# 4. The internal figures are computed, not generated
uv run python -c "
from deep_research.tools.internal_analytics import competitor_exposure
print(competitor_exposure()['totals'])"

# 4b. A figure and the records cited for it must agree
uv run python -c "
from deep_research.tools.internal_analytics import total_for_records
print(total_for_records(['D-2202','D-2206','D-2210','D-2216','D-2220',
                         'D-2223','D-2234','D-2238','D-2241'])['total_usd'])"
# -> 4665000, the segment's ARR won, summed from the records themselves

# 4c. Absence of evidence is rejected as evidence
uv run python -c "
from deep_research.state import is_real_source
print([is_real_source(x) for x in
       ['source unavailable','N/A','','https://www.sec.gov/x.htm']])"
# -> [False, False, False, True]

# 5. Both committed cache artifacts contain no credentials
uv run python scripts/warm_cache.py --audit
```

---

## Repository map

```
knowledge/              Mocked internal records (all entities fictional)
  derived/              Generated citation-addressable chunks
src/deep_research/
  flow.py               The orchestrator
  state.py              Typed contracts between phases
  llms.py               Model tiers, with a runtime availability probe
  knowledge_build.py    Raw records -> self-citing chunks
  crews/                Five crews, each with agents.yaml + tasks.yaml
  tools/
    http.py             Cached, throttled, politely identified HTTP
    keyless.py          SEC filings, arXiv, Hacker News, Wikipedia, news
    search.py           Tiered search + agent-facing tools
    read_url.py         Article extraction
    search_cache.py     Recorded search results, so offline replays them
    internal_analytics.py  Deterministic joins over the complete records
    record_lookup.py    Citation resolution and the audit gate
  render/report.py      Markdown + print-ready HTML
    citations.py        Numbered references; readable identifier runs
  deck/build.py         Deck, speaker notes and register from YAML
  observability/        One event listener, three consumers
    trace.py            The flat event stream, shaped into phases and totals
    graph.py            The flow's topology, and what was running when
    views.py            The markup for every view, shared by app and video
requirements.txt        Resolved lock, for hosts that cannot read uv.lock
scripts/warm_cache.py   Pre-warm and audit the response cache
  make_video.py         The rendered walkthrough, frame by frame
  make_deck_images.py   The deck's pictures, rendered from the same files
app.py                  Demonstration interface
.cache/http.sqlite      Committed API responses (~2 MB, audited)
  search_results.json   Recorded search result sets, readable JSON
```

