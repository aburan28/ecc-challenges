#!/usr/bin/env python3
"""Build report.typ from results/tables.md, results/campaign_saving.md and
results/summary.json (recorded values only), then report.pdf with Typst.

    /path/to/report-venv/bin/python make_report.py
"""
import json
import os
import re
from pathlib import Path

import typst

HERE = Path(__file__).resolve().parent
results = Path(os.environ.get("STUDY_RESULTS", HERE / "results"))
OUT = Path(os.environ.get("STUDY_OUT", HERE))
summary = json.loads((results / "summary.json").read_text())
env = json.loads((results / "env.json").read_text())


def esc(text: str) -> str:
    return re.sub(r"([\\#$*_@<>\[\]`~])", r"\\\1", text)


def convert(markdown: str) -> str:
    out, table = [], []

    def flush():
        if not table:
            return
        header = [c.strip() for c in table[0].strip("|").split("|")]
        body = [[c.strip() for c in row.strip("|").split("|")] for row in table[2:]]
        cols = len(header)
        out.append(f"#text(size: 6.6pt)[#table(columns: {cols}, inset: 3pt, stroke: 0.4pt + luma(170), "
                   "fill: (x, y) => if y == 0 { luma(235) },")
        for cell in header:
            out.append(f"  table.cell[*{esc(cell)}*],")
        for row in body:
            for cell in row:
                out.append(f"  [{esc(cell)}],")
        out.append(")]\n")
        table.clear()

    for line in markdown.splitlines():
        if line.startswith("|"):
            table.append(line)
            continue
        flush()
        if line.startswith("## "):
            out.append(f"=== {esc(line[3:])}")
        elif line.strip():
            out.append(esc(line) + "\n")
    flush()
    return "\n".join(out)


tables = convert((results / "tables.md").read_text())
saving = convert((results / "campaign_saving.md").read_text())
solve = summary["solve"]
pooled = {arm: solve.get(f"pooled_primary_geometric_mean_{arm}_over_A") for arm in ("B", "B2", "B3")}
verdicts = {arm: solve.get(f"verdict_steps_{arm}", "") for arm in ("B", "B2", "B3")}


def pooled_text(label, p):
    if not p:
        return ""
    return f" Pooled geometric mean of {label} over the four primary sizes: {p['ratio']:.3f} (95% CI {p['ci'][0]:.3f} to {p['ci'][1]:.3f}); 1/sqrt2 = 0.707."


aa = "passed" if solve["control_AA_pass"] else "FAILED"
pos = "passed" if solve["control_positive_pass"] else "FAILED"
gate = "passed" if solve.get("reproduction_gate_pass") else "FAILED or missing"
source = f"""#set page(paper: "a4", margin: 14mm, numbering: "1")
#set text(font: "Libertinus Serif", size: 9pt)
#set par(justify: true)
#show heading: set block(above: 1em, below: 0.5em)

= Negation-class walk vs the v2 adding walk: steps per solve on planted toy curves

Study `verification/negation-walk-20261024`. Protocol `PROTOCOL.md` (frozen before any data), `PROTOCOL-AMENDMENT-1.md` and `PROTOCOL-AMENDMENT-2.md` (each written before its arm's data).
Frozen run (05:07 to 05:44 UTC, two sittings), amendment 1 (06:42 to 06:49), amendment 2 solves (07:09 to 07:15), rate (08:20 to 08:24), all 2026-10-24, on {esc(env["cpu"])} ({env["cores"]} vCPU, shared L0 cloud VM, no hardware PMU), {esc(env["compiler"])}.
Driver sha256 `{esc(env["driver_sha256"][:16])}...`. Every number is read from `results/summary.json` and `results/campaign_saving.json`
(measured: steps, ns, operation counters; derived: ratios, intervals, priced costs, projections).

*Pre-registered readout (frozen arm B).* {esc(verdicts["B"].capitalize())}.{esc(pooled_text("B/A", pooled["B"]))}
*Amendment 1, arm B2 (post hoc: the escape step that undoes itself, diagnosed from the frozen data, is fixed).* {esc(verdicts["B2"].capitalize())}.{esc(pooled_text("B2/A", pooled["B2"]))}
*Amendment 2, arm B3 (post hoc: the escape that rejoins the cycle through a three-step reflection, diagnosed from B2's data, is replaced by c\\*G; this is the shipped walk).* {esc(verdicts["B3"].capitalize())}.{esc(pooled_text("B3/A", pooled["B3"]))}
A/A control {aa}; positive control (negation walk without cycle handling) {pos}; reproduction gate (the shipped source reproduces the earlier arms) {gate}.
This is a CPU measurement of the shared walk code on toy curves; the CUDA kernel path was not compiled or run, and no GPU result is claimed.

#figure(image("steps-per-solve.svg", width: 100%), caption: [Left: mean steps per solve over sqrt(n), prime toy curves, with the two theory levels. Middle: mean steps of each negation walk over the v2 walk (A) for every measured size (95% bootstrap CI), A/A control in grey (99% CI). Lower left: microseconds per step on each curve (median, IQR of 15 alternating runs) and the rate loss of B3. Lower right: share of solves containing a trapped trail. Source: results/summary.json, drawn by figure.py.])

== Results (tables.md)
{tables}
== Campaign-level consequence (campaign_saving.md)
{saving}
== Scope and limits
- Steps are counted in lockstep time over 8 lanes (PROTOCOL.md section 4); look-ahead retries and escape moves are steps.
- Timings are L0 CPU timings of generic 12-word bit-serial arithmetic on a shared VM. The frozen rate measurement (M2, single-process alternating runs) is noisy here (IQR 5 to 10%, intervals include 0); the supplementary solve-phase timings (10^6 to 5x10^7 steps per arm and instance, two driver processes, arms interleaved, A2/A within 1%) are tighter and give the per-step ratio used for the campaign table. They are post hoc and labelled so.
- Toy curves (group orders 2^24 to 2^36) say nothing about distinguished-point overhead at production DP densities, and nothing about GPU rates.
- A negation-walk namespace starts empty: its points never combine with v2 points, so v2 work already done on a campaign is not credited.
"""
(OUT / "report.typ").write_text(source)
typst.compile(str(OUT / "report.typ"), output=str(OUT / "report.pdf"))
print("wrote report.typ and report.pdf")
