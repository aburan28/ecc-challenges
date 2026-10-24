#!/usr/bin/env python3
"""Draw steps-per-solve.svg from results/summary.json (recorded values only).

    /path/to/venv/bin/python figure.py
"""
import json
import math
import os
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
RESULTS = Path(os.environ.get("STUDY_RESULTS", HERE / "results"))
OUT = Path(os.environ.get("STUDY_OUT", HERE))
summary = json.loads((RESULTS / "summary.json").read_text())
solve, rate = summary["solve"]["instances"], summary["rate"]

COLORS = {"A": "#1f6fb4", "B": "#d9730d", "A2": "#7a7a7a", "C": "#b3261e", "B2": "#8c6bb1", "B3": "#2a9d4b"}
LABELS = {"A": "A  v2 adding walk", "B": "B  frozen run: table-jump escape, no look-ahead",
          "B2": "B2  amendment 1: table-jump escape with look-ahead", "B3": "B3  amendment 2: c*G escape (shipped)",
          "A2": "A2  v2, independent seeds (A/A)", "C": "C  negation, no cycle handling"}
primary = [i for i in solve if i.startswith("toy-prime")]
secondary = [i for i in solve if i.startswith("toy-binary")]

plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
                     "svg.fonttype": "none"})
fig, grid = plt.subplots(2, 2, figsize=(11.5, 8.6), constrained_layout=True)
axes = [grid[0][0], grid[0][1], grid[1][0], grid[1][1]]

# Panel 1: mean steps per solve over sqrt(n) against log2 n, with the two theory levels.
ax = axes[0]
xs = [solve[i]["log2_n"] for i in primary]
for arm, shift in (("A", -0.24), ("A2", -0.12), ("B", 0.0), ("B2", 0.12), ("B3", 0.24)):
    pts = [(solve[i]["log2_n"] + shift, solve[i]["arms"][arm], math.sqrt(solve[i]["n"])) for i in primary
           if arm in solve[i]["arms"]]
    if not pts:
        continue
    ax.errorbar([p[0] for p in pts], [p[1]["mean_steps"] / p[2] for p in pts],
                yerr=[[(p[1]["mean_steps"] - p[1]["mean_ci95"][0]) / p[2] for p in pts],
                      [(p[1]["mean_ci95"][1] - p[1]["mean_steps"]) / p[2] for p in pts]],
                fmt="o-" if arm != "C" else "s--", color=COLORS[arm], label=LABELS[arm], capsize=3, lw=1.2, ms=4)
ax.axhline(math.sqrt(math.pi / 2), color=COLORS["A"], ls=":", lw=1)
ax.axhline(math.sqrt(math.pi / 4), color=COLORS["B"], ls=":", lw=1)
ax.text(xs[-1] + 0.1, math.sqrt(math.pi / 2) + 0.012, r"$\sqrt{\pi/2}$ (no quotient)", color=COLORS["A"], fontsize=8, ha="right")
ax.text(xs[-1] + 0.1, math.sqrt(math.pi / 4) - 0.04, r"$\sqrt{\pi/4}$ (classes of $\pm P$)", color=COLORS["B"], fontsize=8, ha="right")
ax.set_xlabel(r"$\log_2 n$ (prime toy curves)")
ax.set_ylabel(r"mean steps per solve / $\sqrt{n}$ (95% bootstrap CI)")
ax.set_ylim(0.55, 1.55)
ax.set_title("Steps per solve, normalised")
ax.legend(fontsize=7.2, loc="lower left", frameon=False)

# Panel 2: ratio B/A with its interval, all measured sizes.
ax = axes[1]
order = primary + secondary
for k, instance in enumerate(order):
    kind = "o" if instance in primary else "D"
    for key, arm, shift in (("ratio_B_over_A", "B", -0.24), ("ratio_B2_over_A", "B2", -0.08), ("ratio_B3_over_A", "B3", 0.08)):
        r = solve[instance].get(key)
        if r:
            ax.errorbar(k + shift, r["ratio"], yerr=[[r["ratio"] - r["ci"][0]], [r["ci"][1] - r["ratio"]]], fmt=kind,
                        color=COLORS[arm], capsize=3, ms=5)
    aa = solve[instance].get("ratio_A2_over_A_ci99")
    if aa:
        ax.errorbar(k + 0.24, aa["ratio"], yerr=[[aa["ratio"] - aa["ci"][0]], [aa["ci"][1] - aa["ratio"]]],
                    fmt="o", color=COLORS["A2"], capsize=3, ms=4)
ax.axhline(1 / math.sqrt(2), color="black", ls=":", lw=1)
ax.axhline(1, color="black", lw=0.8)
ax.text(len(order) - 0.6, 1 / math.sqrt(2) + 0.012, "1/√2 = 0.707", ha="right", fontsize=8)
ax.set_xticks(range(len(order)))
ax.set_xticklabels([f"{i.split('-')[1][:3]}\n{solve[i]['log2_n']:.1f}" for i in order], fontsize=8)
ax.set_xlabel("field kind and log2 n (circles: primary prime, diamonds: secondary binary)", fontsize=8.5)
ax.set_ylabel("mean steps of walk / mean steps A (95% CI)\norange B, purple B2, green B3, grey A2/A (99%)")
ax.set_ylim(0.55, 1.2)
ax.set_title("Step ratio, negation walk over v2 walk")

# Panel 3: ns per step A vs B (rate), per curve.
ax = axes[2]
curves = list(rate)
for k, curve in enumerate(curves):
    e = rate[curve]
    for arm, shift in (("A", -0.17), ("B3", 0.17)):
        iqr = e[f"ns_per_step_{arm}_iqr"]
        med = e[f"ns_per_step_{arm}_median"]
        ax.errorbar(k + shift, med / 1000, yerr=[[(med - iqr[0]) / 1000], [(iqr[1] - med) / 1000]], fmt="o",
                    color=COLORS[arm], capsize=3, ms=5, label=LABELS[arm] if k == 0 else None)
    ax.text(k, max(e["ns_per_step_B3_iqr"][1], e["ns_per_step_A_iqr"][1]) / 1000 * 1.03,
            f"{100 * e['rate_loss_per_step']:.1f}%", ha="center", fontsize=8)
ax.set_xticks(range(len(curves)))
ax.set_xticklabels(curves, rotation=20, fontsize=8)
ax.set_ylabel("µs per step, median and IQR over 15 runs")
ax.set_title("Cost per step (label: rate loss of B3)")
ax.legend(fontsize=7.5, frameon=False, loc="lower right")
ax.set_ylim(0, 1.18 * max(e["ns_per_step_B3_iqr"][1] for e in rate.values()) / 1000)

# Panel 4: share of solves containing a trapped trail (more than 200 escapes), frozen B vs amended B2.
ax = axes[3]
order = primary + secondary
for arm, shift in (("B", -0.26), ("B2", 0.0), ("B3", 0.26)):
    ys = [100 * solve[i]["arms"][arm]["solves_with_trapped_trail"] / solve[i]["arms"][arm]["solves"]
          if arm in solve[i]["arms"] else float("nan") for i in order]
    ax.bar([k + shift for k in range(len(order))], ys, width=0.25, color=COLORS[arm], label=LABELS[arm])
ax.set_xticks(range(len(order)))
ax.set_xticklabels([f"{i.split('-')[1][:3]}\n{solve[i]['log2_n']:.1f}" for i in order], fontsize=8)
ax.set_xlabel("field kind and log2 n")
ax.set_ylabel("% of solves with a trapped trail (> 200 escapes)")
ax.set_title("Trapped trails (escape loops) by escape rule")
ax.set_ylim(0, 80)
ax.legend(fontsize=7.5, frameon=False, loc="upper left")

n_main = [solve[i]["arms"]["A"]["solves"] for i in primary]
fig.suptitle("Negation-class walk vs v2 adding walk on planted toy curves\n"
             f"N = {n_main[0]} solves per arm and primary size, 8 lanes, CPU single thread, shared L0 VM; "
             "PROTOCOL.md + AMENDMENT-1 + AMENDMENT-2", fontsize=10)
fig.savefig(OUT / "steps-per-solve.svg")
print("wrote", OUT / "steps-per-solve.svg")
