# Sample output

A complete run, committed so the results can be read without running anything.

| | |
|---|---|
| `market_investment_memo.md` | The memo |
| `market_investment_memo.html` | The same memo, styled and print-ready |
| `evidence_ledger.json` | Every typed object behind it: brief, external findings, internal assessment, the adopted-position test, fusion, audit |
| `run_manifest.json` | What the run did — findings, audit result, tokens, estimated cost |
| `run_trace.jsonl` | Append-only event trace, one line per phase, task, tool call and model call |

## How this one was produced

```bash
uv run deep_research --offline
```

**Offline on purpose.** It runs against the search results and API responses
committed to this repository, so it is what a fresh clone reproduces with an
`OPENAI_API_KEY` and no search credentials at all. A live run reaches more
sources; this one is the honest floor rather than the best case.

## What it came out as

| | |
|---|---|
| Recommendation | Hybrid, high confidence |
| Cross-source findings | 3 — each carrying both a retrievable source and resolvable records |
| Adopted positions contradicted | 2 |
| Gaps stated | 2 |
| Citation audit | Passed |
| Unresolved identifiers | 0 |
| Figures disagreeing with their records | 0 |
| Revisions required | 1 |
| Estimated cost | ~$0.44 |
| Wall clock | 149s |

The single revision is the gate working, not a defect: the first draft was
failed, corrected, and passed on the second pass. The trace shows it.

## Reading it critically

Three things are worth checking rather than taking on trust.

**Pick any bracketed identifier and resolve it.** Every one in the memo
resolves to a record in [`knowledge/`](../knowledge/):

```bash
uv run python -c "
from deep_research.tools.record_lookup import resolve
print(resolve('D-2201'))"
```

**Pick any aggregate figure and total its records.** They agree, because the
figure and the citation both come from the same computation:

```bash
uv run python -c "
from deep_research.tools.internal_analytics import total_for_records
print(total_for_records(['D-2201','D-2204','D-2207','D-2211','D-2214',
                         'D-2217','D-2222','D-2230','D-2233','D-2239']))"
```

**Read the signal matrix sceptically.** Each row is meant to be a conclusion
neither source supports alone. Cover the external column and ask whether the
row still tells an executive to act; then cover the internal column and ask the
same. That is the test the pipeline applies to itself, and it is the right test
to apply to its output.

## Two things this sample is not

**Not deterministic.** Re-running produces a different memo. Across runs the
recommendation has come out as hybrid, partner and build, with two to four
findings. What holds every time is structural — both sides cited, figures
reconciled, identifiers resolved — not the wording or the count.

**Not evidence that the fusion works on real data.** The internal-external
overlaps in this fixture were planted deliberately, and the top-level README
says so. What this demonstrates is the detection and the verification. Yield on
real customer data is unknown, which is why
[docs/PRODUCTION.md](../docs/PRODUCTION.md) proposes measuring it before
building anything further.
