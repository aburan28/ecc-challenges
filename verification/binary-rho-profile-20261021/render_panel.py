"""Render receipt-derived worker timing plots and a compact PDF record."""
from __future__ import annotations

import json
from pathlib import Path
import statistics
import subprocess
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def render(run: Path, report: Path) -> None:
    receipt = json.loads((run / "receipt.json").read_text())
    rows = receipt["comparisons"]
    report.mkdir(parents=True, exist_ok=False)
    labels = [f"{row['campaign']} r{row['repeat']}" for row in rows]
    full = [row["candidate_over_baseline_median"] for row in rows]
    kernel = [row["candidate_kernel_ms"] /
              statistics.median(row["baseline_kernel_bracket_ms"]) for row in rows]
    stable = [max(row["baseline_bracket_ns"]) / min(row["baseline_bracket_ns"]) <= 1.05
              for row in rows]
    fig, axes = plt.subplots(2, 1, sharex=True, figsize=(11, 6.2), layout="constrained")
    for axis, values, title in zip(axes, (full, kernel),
                                   ("Full worker process", "CUDA kernel event")):
        for i, (value, is_stable) in enumerate(zip(values, stable)):
            axis.scatter(i, value, marker="o" if is_stable else "x",
                         color="#16618c" if is_stable else "#bb5137", s=42)
        axis.axhline(1.0, color="#444444", linewidth=1, linestyle="--")
        axis.set_ylabel("Candidate / baseline time")
        axis.set_title(title)
        axis.grid(axis="y", alpha=0.25)
    axes[-1].set_xticks(range(len(labels)), labels, rotation=55, ha="right", fontsize=7)
    fig.suptitle(f"{run.name}: 9 curves, 18 matched pairs, 256 lanes × 512 steps")
    fig.savefig(report / "ratio.svg")
    plt.close(fig)
    stable_full = [value for value, ok in zip(full, stable) if ok]
    median_stable = f"{statistics.median(stable_full):.3f}" if stable_full else "missing"
    typ = f'''#set page(width: 11in, height: 8.5in, margin: 0.6in)
#set text(size: 10pt)
= Binary rho L4 measurement: {run.name}

Observation record. Receipt: `{run.name}/receipt.json`; raw archive: `{run.name}/raw.tar.gz`.
This document derives values only from the frozen receipt and records no scientific status change.

#image("ratio.svg", width: 100%)

Circle: baseline bracket drift at most 1.05; cross: larger drift. Dashed line: equal time.
Full-process median across stable pairs: {median_stable} (n={len(stable_full)}).
Kernel median across all pairs: {statistics.median(kernel):.3f} (n={len(kernel)}).
All ratios divide candidate time by the median of its two adjacent baseline times.
Workload and controls: nine pinned ECCJOB03 curves, 256 lanes, 512 steps, two repeats.
The receipt and raw archive contain exact output, distinguished-point and step checks.
Full campaign costs are outside this measured boundary.
'''
    (report / "report.typ").write_text(typ)
    subprocess.run(["typst", "compile", "report.typ", "report.pdf"],
                   cwd=report, check=True, timeout=60)


if __name__ == "__main__":
    render(Path(sys.argv[1]), Path(sys.argv[2]))
