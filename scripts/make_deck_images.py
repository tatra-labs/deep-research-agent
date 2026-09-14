"""Render the deck's images from the run's own files.

    uv run python scripts/make_deck_images.py

Slides that describe an input or an output should show it. Retyping a CRM row
onto a slide would be faster and would also be the moment the deck starts
claiming something the repository does not contain, so every image here is
rendered from the real file: the internal records come out of `knowledge/`, and
the memo images are screenshots of the memo the run actually wrote, through its
own stylesheet.

Re-run it after a new run and the deck's pictures are correct again. That is
the same reason the walkthrough video is generated rather than screen-recorded;
this shares its browser plumbing.
"""

from __future__ import annotations

import csv
import html as html_lib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from deep_research.observability import graph as graphlib  # noqa: E402
from deep_research.observability import trace as tracelib  # noqa: E402
from deep_research.observability import views  # noqa: E402
from make_video import find_browser  # noqa: E402

RUN_DIR = ROOT / "sample_output"
KNOWLEDGE = ROOT / "knowledge"
OUT = ROOT / "deck" / "images"
WORK = ROOT / ".deck_images"

ACCENT = views.ACCENT
INK = "#12161C"
GROUND = "#FAFAF8"
RULE = "#E2E1DC"
MUTED = "#5B6472"
FONT = '-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif'
MONO = 'ui-monospace,SFMono-Regular,Menlo,Consolas,"Courier New",monospace'

SCALE = 1.5


def esc(value) -> str:
    return html_lib.escape(str(value), quote=True)


def shell(body: str, width: int, height: int, *, pad: int = 34) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
  html,body{{margin:0;padding:0;background:{GROUND};color:{INK};font-family:{FONT};
    -webkit-font-smoothing:antialiased;width:{width}px;height:{height}px;overflow:hidden}}
  .wrap{{padding:{pad}px}}
  .h{{font-size:16px;letter-spacing:.11em;text-transform:uppercase;color:{ACCENT};
     font-weight:700;margin-bottom:10px}}
  table{{border-collapse:collapse;width:100%;font-size:19px}}
  th{{text-align:left;font-size:14px;letter-spacing:.06em;text-transform:uppercase;
     color:{MUTED};font-weight:700;padding:0 14px 8px 0;border-bottom:1px solid {RULE}}}
  td{{padding:9px 14px 9px 0;border-bottom:1px solid #EFEEE9;vertical-align:top}}
  .mono{{font-family:{MONO};font-size:17px}}
  .tag{{font-family:{MONO};font-size:16px;color:{ACCENT}}}
  .note{{font-size:17px;color:{MUTED};margin-top:14px;line-height:1.5}}
  .hit{{background:rgba(180,71,42,.10);border-radius:4px;padding:1px 5px;
       font-weight:650;color:{ACCENT};font-size:19px}}
</style></head><body><div class="wrap">{body}</div></body></html>"""


def shoot(browser: str, name: str, markup: str, width: int, height: int) -> Path:
    html_path = WORK / f"{name}.html"
    png = OUT / f"{name}.png"
    html_path.write_text(markup, encoding="utf-8")
    subprocess.run(
        [
            browser, "--headless", "--disable-gpu", "--hide-scrollbars", "--no-sandbox",
            f"--user-data-dir={WORK / 'profile'}",
            f"--window-size={width},{height}",
            f"--force-device-scale-factor={SCALE}",
            "--virtual-time-budget=1500",
            f"--screenshot={png}",
            html_path.as_uri(),
        ],
        check=False,
        capture_output=True,
    )
    if not png.exists():
        raise SystemExit(f"image {name} did not render")
    print(f"  {png.relative_to(ROOT)}  ({png.stat().st_size // 1024} KB)")
    return png


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------
def input_records() -> str:
    """Three of the internal files, as they actually are.

    Nexara Robotics is highlighted in two of them, because that is the whole
    argument: it is a competitor in the deal ledger and a supplier in the
    spend ledger, and no single file can see both.
    """
    deals = list(csv.DictReader((KNOWLEDGE / "crm_deals.csv").open(encoding="utf-8")))
    spend = list(csv.DictReader((KNOWLEDGE / "vendor_spend.csv").open(encoding="utf-8")))
    caps = json.loads((KNOWLEDGE / "capability_inventory.json").read_text(encoding="utf-8"))

    def mark(value: str) -> str:
        return (
            f'<span class="hit">{esc(value)}</span>'
            if value == "Nexara Robotics"
            else esc(value)
        )

    lost = [d for d in deals if d["outcome"] == "Lost"][:4]
    deal_rows = "".join(
        f'<tr><td class="tag">{esc(d["deal_id"])}</td><td>{esc(d["account"])}</td>'
        f'<td class="mono">{esc(d["segment_tag"])}</td>'
        f'<td class="mono">${int(d["arr_usd"]):,}</td>'
        f"<td>{mark(d['competitor'])}</td>"
        f'<td class="mono">{esc(d["loss_reason_code"])}</td></tr>'
        for d in lost
    )

    picked = [v for v in spend if v["vendor"] == "Nexara Robotics"]
    picked += [v for v in spend if v["vendor"] != "Nexara Robotics"][:2]
    spend_rows = "".join(
        f'<tr><td class="tag">{esc(v["vendor_id"])}</td><td>{mark(v["vendor"])}</td>'
        f'<td>{esc(v["category"])}</td>'
        f'<td class="mono">${int(v["annual_spend_usd"]):,}/yr</td>'
        f'<td class="mono">{esc(v["contract_end"])}</td>'
        f'<td>{esc(v["renewal_risk"])}</td></tr>'
        for v in picked[:3]
    )

    cap_rows = "".join(
        f'<tr><td class="tag">{esc(c["capability_id"])}</td><td>{esc(c["name"])}</td>'
        f'<td class="mono">{esc(c["segment_tag"])}</td>'
        f'<td class="mono">{c["maturity"]} / 5</td><td>{esc(c["owner_bu"])}</td></tr>'
        for c in caps["capabilities"][:3]
    )

    return shell(
        f"""
<div style="display:grid;grid-template-rows:auto auto auto;gap:24px">
  <div>
    <div class="h">crm_deals.csv &nbsp;·&nbsp; deal outcomes</div>
    <table><tr><th>Deal</th><th>Account</th><th>Segment</th><th>ARR</th>
      <th>Competitor</th><th>Loss reason</th></tr>{deal_rows}</table>
  </div>
  <div>
    <div class="h">vendor_spend.csv &nbsp;·&nbsp; what we pay, and to whom</div>
    <table><tr><th>Vendor</th><th>Name</th><th>Category</th><th>Spend</th>
      <th>Contract ends</th><th>Renewal risk</th></tr>{spend_rows}</table>
  </div>
  <div>
    <div class="h">capability_inventory.json &nbsp;·&nbsp; what we can actually do</div>
    <table><tr><th>Id</th><th>Capability</th><th>Segment</th><th>Maturity</th>
      <th>Owner</th></tr>{cap_rows}</table>
    <div class="note">The same company appears as a competitor we lose to and as a
      supplier we pay. Neither file can see that on its own, and no market report
      can see either file.</div>
  </div>
</div>""",
        1700,
        700,
    )


# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------
def memo_parts() -> tuple[str, str, str]:
    """The memo's stylesheet, its opening, and its signal-matrix section."""
    raw = (RUN_DIR / "market_investment_memo.html").read_text(encoding="utf-8")
    style = "".join(re.findall(r"<style>.*?</style>", raw, re.S))
    body = re.search(r"<body[^>]*>(.*)</body>", raw, re.S).group(1)

    sections = re.split(r"(?=<h2)", body)
    opening = "".join(sections[:3])
    matrix = next(
        (s for s in sections if "signals combined" in s.lower()),
        "",
    )
    return style, opening, matrix


def memo_page(style: str, inner: str, width: int, height: int, *, scale: float = 1.0) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8">{style}
<style>html,body{{width:{width}px;height:{height}px;overflow:hidden;margin:0}}
  .z{{transform:scale({scale});transform-origin:top left;width:{width / scale:.0f}px}}
</style></head><body><div class="z">{inner}</div></body></html>"""


def output_artifacts() -> str:
    """What the run leaves on disk, with the real shape of each file."""
    manifest = json.loads((RUN_DIR / "run_manifest.json").read_text(encoding="utf-8"))
    ledger = json.loads((RUN_DIR / "evidence_ledger.json").read_text(encoding="utf-8"))
    signals = (ledger.get("fusion") or {}).get("signals") or []
    sample = json.dumps(
        {
            "entity": signals[0].get("entity") if signals else "",
            "external_evidence": [
                {
                    "claim": (signals[0]["external_evidence"][0]["claim"][:64] + "…")
                    if signals and signals[0].get("external_evidence")
                    else "",
                    "source_url": "https://…",
                }
            ],
            "internal_evidence": [
                {
                    "claim": (signals[0]["internal_evidence"][0]["claim"][:64] + "…")
                    if signals and signals[0].get("internal_evidence")
                    else "",
                    "citation_tags": (signals[0]["internal_evidence"][0].get("citation_tags") or [])[:4]
                    if signals and signals[0].get("internal_evidence")
                    else [],
                }
            ],
        },
        indent=2,
    )
    rows = "".join(
        f'<tr><td class="tag">{esc(k)}</td><td class="mono">{esc(v)}</td></tr>'
        for k, v in list(manifest.items())[:9]
        if k != "run_detail"
    )
    return shell(
        f"""
<div style="display:grid;grid-template-columns:1.05fr 1fr;gap:36px">
  <div>
    <div class="h">evidence_ledger.json &nbsp;·&nbsp; one cross-source finding</div>
    <pre class="mono" style="font-size:12.5px;line-height:1.55;margin:0;
      background:rgba(127,127,127,.06);border:1px solid {RULE};border-radius:8px;
      padding:14px 16px;white-space:pre-wrap">{esc(sample)}</pre>
    <div class="note">Every finding carries a reachable source on one side and a
      resolvable record identifier on the other. A finding missing either one is
      rejected in code before it can reach the memo.</div>
  </div>
  <div>
    <div class="h">run_manifest.json &nbsp;·&nbsp; the run, measured by itself</div>
    <table>{rows}</table>
  </div>
</div>""",
        1700,
        700,
    )


def main() -> None:
    browser = find_browser()
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    OUT.mkdir(parents=True, exist_ok=True)

    records = tracelib.load(RUN_DIR / "run_trace.jsonl")
    phases = tracelib.phases(records)
    total = max(tracelib.totals(records)["elapsed_seconds"], 0.1)

    print("rendering deck images")
    shoot(browser, "input_records", input_records(), 1700, 700)

    style, opening, matrix = memo_parts()
    shoot(browser, "output_memo", memo_page(style, opening, 1120, 900, scale=1.12),
          1120, 900)
    shoot(browser, "output_matrix", memo_page(style, matrix, 1120, 782, scale=1.12),
          1120, 782)
    shoot(browser, "output_artifacts", output_artifacts(), 1700, 700)

    # The crew graph at the moment both research legs are running, which is the
    # one frame that shows the fork doing its job.
    snap = graphlib.snapshot(phases, records, 22.0)
    shoot(
        browser,
        "system_graph",
        shell(
            views.graph_svg(snap, animate=False, motion=0.45)
            + views.graph_legend_html(snap, 22.0, total),
            1700, 700, pad=22,
        ),
        1700,
        700,
    )
    shoot(
        browser,
        "system_timeline",
        shell(views.timeline_html(phases, total, cursor=total), 1700, 560, pad=26),
        1700,
        560,
    )

    shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    main()
