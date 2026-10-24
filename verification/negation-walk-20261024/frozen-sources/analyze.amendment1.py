#!/usr/bin/env python3
"""Apply the decision rules frozen in PROTOCOL.md (arms A, B, A2, C) and
PROTOCOL-AMENDMENT-1.md (arm B2) to results/ and write results/summary.json and
results/tables.md. Reads recorded values only.

The bootstrap draws for the frozen arms come from one generator in the frozen
order (seed 1), so the frozen numbers do not move when arms are added; B2 draws
come from a second generator (seed 3)."""
from __future__ import annotations

import json
import math
from pathlib import Path
import statistics
import sys

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path[:0] = [str(HERE)]
import run_study as plan  # noqa: E402  (frozen parameters)

RESAMPLES, SEED, SEED_B2 = 10000, 1, 3
THEORY_RATIO = 1 / math.sqrt(2)
GAE_BAND = (0.0, 0.1, 1.0)
TRAP_ESCAPES = 200      # a solve with more escapes than this contained a trapped trail


def toys():
    rows = json.loads((REPO / "tests/fixtures/toy_instances.json").read_text())
    return {r["id"]: r for r in rows}


def load_solves(out: Path):
    data = {}
    for path in sorted((out / "solve").glob("*.jsonl")):
        instance, arm = path.name.split(".")[:2]
        data.setdefault((instance, arm), []).extend(json.loads(l) for l in path.read_text().splitlines())
    for rows in data.values():
        rows.sort(key=lambda r: r["solve"])
    return data


def boot_mean(rng, values, resamples=RESAMPLES):
    values = np.asarray(values, dtype=float)
    return values[rng.integers(0, len(values), (resamples, len(values)))].mean(axis=1)


def interval(draws, level=0.95):
    low = (1 - level) / 2 * 100
    return [float(np.percentile(draws, low)), float(np.percentile(draws, 100 - low))]


def arm_stats(rows, n, arm, ci):
    steps = [r["steps"] for r in rows]
    phys = sum(r["phys_steps"] for r in rows)
    adds = sum(r["adds"] for r in rows)
    return {
        "solves": len(rows), "mean_steps": statistics.fmean(steps),
        "median_steps": statistics.median(steps), "sd_steps": statistics.pstdev(steps),
        "mean_over_sqrt_n": statistics.fmean(steps) / math.sqrt(n), "mean_ci95": ci,
        "adds_per_step": adds / phys, "lookaheads_per_step": sum(r["lookaheads"] for r in rows) / phys,
        "escapes_per_million_steps": 1e6 * sum(r["escapes"] for r in rows) / phys,
        "solves_with_trapped_trail": sum(1 for r in rows if r["escapes"] > TRAP_ESCAPES),
        "cap_restarts_per_solve": statistics.fmean(r["cap_restarts"] for r in rows),
        "dp_per_solve": statistics.fmean(r["dps"] for r in rows),
        "walk_ns_per_step": sum(r["walk_ns"] for r in rows) / phys,
        "dp_bits": plan.dp_bits(n, arm),
    }


def priced(entry, arm, ratio):
    a_b = entry["arms"][arm]["adds_per_step"]
    q_b = 2 * a_b + 1                # digest per attempt + canonicalisation per attempt + window per step
    costs = {str(lam): ratio["ratio"] * (a_b + lam * q_b) / (1 + lam * 1.0) for lam in GAE_BAND}
    denominator = ratio["ratio"] * q_b - 1
    return {"cost_ratio": costs, "lambda_star": (1 - ratio["ratio"] * a_b) / denominator if denominator > 0 else None,
            "order_holds_across_band": all(v < 1 for v in costs.values())}


def solve_summary(data, instance_rows):
    rng, rng2 = np.random.default_rng(SEED), np.random.default_rng(SEED_B2)
    result = {"instances": {}, "integrity": {}}
    for instance in plan.PRIMARY + plan.SECONDARY:
        n = int(instance_rows[instance]["n"], 16)
        entry = {"n": n, "log2_n": math.log2(n), "field": instance_rows[instance]["field_kind"],
                 "E_v2": math.sqrt(math.pi * n / 2), "E_neg": math.sqrt(math.pi * n / 4), "arms": {}}
        draws = {}
        for arm in ("A", "B", "A2", "C", "B2"):
            rows = data.get((instance, arm))
            if not rows:
                continue
            draws[arm] = boot_mean(rng2 if arm == "B2" else rng, [r["steps"] for r in rows])
            entry["arms"][arm] = arm_stats(rows, n, arm, interval(draws[arm]))
            result["integrity"][f"{instance}.{arm}"] = {
                "solves": len(rows), "wrong_scalar": sum(1 - r["ok"] for r in rows if not r["gave_up"]),
                "gave_up": sum(r["gave_up"] for r in rows), "degenerate_collisions": sum(r["degenerate"] for r in rows),
                "expected_solves": next((c for i, a, c in plan.plan() if (i, a) == (instance, arm)), None)}

        def ratio(top, bottom, level=0.95):
            if top in draws and bottom in draws:
                r = draws[top] / draws[bottom]
                return {"ratio": float(entry["arms"][top]["mean_steps"] / entry["arms"][bottom]["mean_steps"]),
                        "ci": interval(r, level), "level": level}
        entry["ratio_B_over_A"] = ratio("B", "A")
        entry["ratio_A2_over_A_ci99"] = ratio("A2", "A", 0.99)
        entry["ratio_C_over_A"] = ratio("C", "A")
        entry["ratio_B2_over_A"] = ratio("B2", "A")
        entry["ratio_B2_over_B"] = ratio("B2", "B")
        for arm, key in (("B", "ratio_B_over_A"), ("B2", "ratio_B2_over_A")):
            r = entry[key]
            if r:
                suffix = "" if arm == "B" else "_B2"
                p = priced(entry, arm, r)
                entry["priced_cost_ratio" + suffix] = p["cost_ratio"]
                entry["lambda_star" + suffix] = p["lambda_star"]
                entry["priced_order_holds_across_band" + suffix] = p["order_holds_across_band"]
                entry["verified_step_reduction" + suffix] = r["ci"][1] < 1
                entry["covers_theory_1_over_sqrt2" + suffix] = r["ci"][0] <= THEORY_RATIO <= r["ci"][1]
        result["instances"][instance] = entry
        entry["_draws"] = draws

    def pooled(arm):
        if not all(arm in result["instances"][i]["_draws"] and "A" in result["instances"][i]["_draws"] for i in plan.PRIMARY):
            return None
        total = np.zeros(RESAMPLES)
        for i in plan.PRIMARY:
            d = result["instances"][i]["_draws"]
            total += np.log(d[arm] / d["A"]) / len(plan.PRIMARY)
        key = "ratio_B_over_A" if arm == "B" else "ratio_B2_over_A"
        point = math.exp(statistics.fmean(math.log(result["instances"][i][key]["ratio"]) for i in plan.PRIMARY))
        return {"ratio": point, "ci": [math.exp(v) for v in interval(total)]}
    result["pooled_primary_geometric_mean_B_over_A"] = pooled("B")
    result["pooled_primary_geometric_mean_B2_over_A"] = pooled("B2")
    if result["pooled_primary_geometric_mean_B_over_A"]:
        result["verdict_steps"] = ("verified step reduction at all four primary sizes"
                                   if all(result["instances"][i]["verified_step_reduction"] for i in plan.PRIMARY)
                                   else "step reduction not verified at every primary size")
    if result["pooled_primary_geometric_mean_B2_over_A"]:
        result["verdict_steps_B2"] = ("verified step reduction at all four primary sizes"
                                      if all(result["instances"][i]["verified_step_reduction_B2"] for i in plan.PRIMARY)
                                      else "step reduction not verified at every primary size")
    aa = [result["instances"][i]["ratio_A2_over_A_ci99"] for i in plan.PRIMARY if result["instances"][i]["ratio_A2_over_A_ci99"]]
    result["control_AA_pass"] = bool(aa) and all(r["ci"][0] <= 1 <= r["ci"][1] for r in aa) and len(aa) == len(plan.PRIMARY)
    pos = [result["instances"][i]["ratio_C_over_A"] for i in plan.POSITIVE if result["instances"][i]["ratio_C_over_A"]]
    result["control_positive_pass"] = bool(pos) and all(r["ci"][0] > 2 for r in pos) and len(pos) == len(plan.POSITIVE)
    result["calibration_A_in_0.8_1.5"] = all(
        0.8 <= result["instances"][i]["arms"]["A"]["mean_over_sqrt_n"] / math.sqrt(math.pi / 2) <= 1.5
        for i in plan.PRIMARY if "A" in result["instances"][i]["arms"])
    for entry in result["instances"].values():
        entry.pop("_draws")
    gate = HERE / "results" / "reproduction_gate.json"
    result["reproduction_gate_pass"] = json.loads(gate.read_text())["pass"] if gate.exists() else None
    return result


def rate_summary(out: Path, solve):
    rng = np.random.default_rng(SEED + 1)
    result = {}
    for instance in list(plan.RATE_TOY) + list(plan.RATE_REAL):
        reps = {"A": [], "B2": []}
        for arm in reps:
            for path in sorted((out / "rate").glob(f"{instance}.{arm}.*.json")):
                reps[arm].append(json.loads(path.read_text()))
        if not reps["A"] or not reps["B2"]:
            continue
        ns_a = np.array([r["ns_per_step"] for r in reps["A"]])
        ns_b = np.array([r["ns_per_step"] for r in reps["B2"]])
        draws_a = np.median(ns_a[rng.integers(0, len(ns_a), (RESAMPLES, len(ns_a)))], axis=1)
        draws_b = np.median(ns_b[rng.integers(0, len(ns_b), (RESAMPLES, len(ns_b)))], axis=1)
        ratio = float(np.median(ns_b) / np.median(ns_a))
        addtime = [json.loads(path.read_text()) for path in [out / "rate" / f"{instance}.addtime.json"]
                   if path.exists()]
        ns_add = statistics.median(r["ns_per_add"] for r in addtime[0]) if addtime else None
        eps = sum(r["lookaheads"] for r in reps["B2"]) / sum(r["steps"] for r in reps["B2"])
        entry = {
            "repetitions": [len(ns_a), len(ns_b)],
            "ns_per_step_A_median": float(np.median(ns_a)), "ns_per_step_B2_median": float(np.median(ns_b)),
            "ns_per_step_A_iqr": [float(np.percentile(ns_a, 25)), float(np.percentile(ns_a, 75))],
            "ns_per_step_B2_iqr": [float(np.percentile(ns_b, 25)), float(np.percentile(ns_b, 75))],
            "time_per_step_ratio_B2_over_A": ratio, "time_per_step_ratio_ci95": interval(draws_b / draws_a),
            "rate_loss_per_step": 1 - 1 / ratio,
            "rate_loss_ci95": [1 - 1 / v for v in interval(draws_b / draws_a)],
            "lookaheads_per_step_B2": eps,
            "ns_per_group_addition": ns_add,
        }
        if ns_add:
            overhead = (np.median(ns_b) - np.median(ns_a)) - eps * ns_add
            entry["native_overhead_gae_per_step"] = float(overhead / ns_add)
        if instance in solve["instances"] and solve["instances"][instance].get("ratio_B2_over_A"):
            r = solve["instances"][instance]["ratio_B2_over_A"]
            entry["steps_ratio_B2_over_A"] = r["ratio"]
            entry["net_time_ratio_B2_over_A"] = r["ratio"] * ratio
            entry["net_time_ratio_ci95"] = [r["ci"][0] * interval(draws_b / draws_a)[0],
                                            r["ci"][1] * interval(draws_b / draws_a)[1]]
        result[instance] = entry
    return result


def fmt_ratio(r):
    return "-" if not r else f"{r['ratio']:.3f} [{r['ci'][0]:.3f}, {r['ci'][1]:.3f}]"


def markdown(solve, rate):
    inst = solve["instances"]
    lines = ["## Mean steps per solve (lockstep steps; N solves per arm)", "",
             "| instance | log2 n | N (A/B/B2) | mean A | mean B (frozen) | mean B2 (amended) | A / sqrt(pi n/2) | B / sqrt(pi n/4) | B2 / sqrt(pi n/4) |",
             "|---|---|---|---|---|---|---|---|---|"]
    for name, e in inst.items():
        a, b, b2 = (e["arms"].get(k) for k in ("A", "B", "B2"))
        if not (a and b):
            continue
        lines.append(
            f"| {name} | {e['log2_n']:.2f} | {a['solves']}/{b['solves']}/{b2['solves'] if b2 else '-'} | {a['mean_steps']:.0f} | {b['mean_steps']:.0f} "
            f"| {('%.0f' % b2['mean_steps']) if b2 else '-'} | {a['mean_steps'] / e['E_v2']:.3f} | {b['mean_steps'] / e['E_neg']:.3f} "
            f"| {('%.3f' % (b2['mean_steps'] / e['E_neg'])) if b2 else '-'} |")
    lines += ["", "## Step ratios, 95% bootstrap intervals (1/sqrt2 = 0.707)", "",
              "| instance | B/A (frozen) | covers 1/sqrt2 | B2/A (amended, post hoc) | covers 1/sqrt2 | B2/B | A2/A (99%) | C/A |",
              "|---|---|---|---|---|---|---|---|"]
    for name, e in inst.items():
        if not e["ratio_B_over_A"]:
            continue
        lines.append(
            f"| {name} | {fmt_ratio(e['ratio_B_over_A'])} | {'yes' if e['covers_theory_1_over_sqrt2'] else 'no'} "
            f"| {fmt_ratio(e['ratio_B2_over_A'])} | {('yes' if e.get('covers_theory_1_over_sqrt2_B2') else 'no') if e['ratio_B2_over_A'] else '-'} "
            f"| {fmt_ratio(e['ratio_B2_over_B'])} | {fmt_ratio(e['ratio_A2_over_A_ci99'])} | {fmt_ratio(e['ratio_C_over_A'])} |")
    for key, label in (("pooled_primary_geometric_mean_B_over_A", "B/A (frozen)"), ("pooled_primary_geometric_mean_B2_over_A", "B2/A (amended, post hoc)")):
        p = solve.get(key)
        if p:
            lines += ["", f"Pooled geometric mean of {label} over the four primary sizes: {p['ratio']:.3f} [{p['ci'][0]:.3f}, {p['ci'][1]:.3f}]."]
    lines += ["", f"Frozen verdict: {solve.get('verdict_steps', 'n/a')}. Controls: A/A pass = {solve['control_AA_pass']}; "
              f"positive control (C/A lower bound > 2) pass = {solve['control_positive_pass']}; A calibration in [0.8, 1.5] = {solve['calibration_A_in_0.8_1.5']}; "
              f"amended-source reproduction gate pass = {solve['reproduction_gate_pass']}.", "",
              "## Walk operations and trapped trails", "",
              "| instance | adds/step B / B2 | look-ahead retries/step B2 | escapes per 1e6 steps B / B2 | solves with a trapped trail (>200 escapes) B / B2 | trail restarts at the cap per solve A / B / B2 / C |",
              "|---|---|---|---|---|---|"]
    for name, e in inst.items():
        a, b, b2, c = (e["arms"].get(k) for k in ("A", "B", "B2", "C"))
        if not b:
            continue
        caps = " / ".join(("%.2f" % x["cap_restarts_per_solve"]) if x else "-" for x in (a, b, b2, c))
        lines.append(
            f"| {name} | {b['adds_per_step']:.4f} / {('%.4f' % b2['adds_per_step']) if b2 else '-'} "
            f"| {('%.4f' % b2['lookaheads_per_step']) if b2 else '-'} "
            f"| {b['escapes_per_million_steps']:.0f} / {('%.0f' % b2['escapes_per_million_steps']) if b2 else '-'} "
            f"| {b['solves_with_trapped_trail']} / {b2['solves_with_trapped_trail'] if b2 else '-'} | {caps} |")
    lines += ["", "## Priced comparison (group additions + lambda x native operations per solve; walk over A)", "",
              "| instance | arm | ratio at lambda=0 | lambda=0.1 | lambda=1 | break-even lambda | order holds across 0..1 |", "|---|---|---|---|---|---|---|"]
    for name, e in inst.items():
        for arm, suffix in (("B", ""), ("B2", "_B2")):
            if "priced_cost_ratio" + suffix in e:
                p, star = e["priced_cost_ratio" + suffix], e["lambda_star" + suffix]
                lines.append(f"| {name} | {arm} | {p['0.0']:.3f} | {p['0.1']:.3f} | {p['1.0']:.3f} | {('%.2f' % star) if star else 'none'} "
                             f"| {'yes' if e['priced_order_holds_across_band' + suffix] else 'no'} |")
    lines += ["", "## Rate of the amended walk B2 vs A (CPU, single thread, dp_bits=40, 15 alternating runs)", "",
              "| curve | ns/step A (IQR) | ns/step B2 (IQR) | B2/A time per step [95% CI] | rate loss per step | native overhead (GAE/step) | steps B2/A | net time B2/A |",
              "|---|---|---|---|---|---|---|---|"]
    for name, e in rate.items():
        lines.append(
            f"| {name} | {e['ns_per_step_A_median']:.0f} ({e['ns_per_step_A_iqr'][0]:.0f}-{e['ns_per_step_A_iqr'][1]:.0f}) "
            f"| {e['ns_per_step_B2_median']:.0f} ({e['ns_per_step_B2_iqr'][0]:.0f}-{e['ns_per_step_B2_iqr'][1]:.0f}) "
            f"| {e['time_per_step_ratio_B2_over_A']:.3f} [{e['time_per_step_ratio_ci95'][0]:.3f}, {e['time_per_step_ratio_ci95'][1]:.3f}] "
            f"| {100 * e['rate_loss_per_step']:.1f}% [{100 * e['rate_loss_ci95'][0]:.1f}, {100 * e['rate_loss_ci95'][1]:.1f}] "
            f"| {('%.3f' % e['native_overhead_gae_per_step']) if 'native_overhead_gae_per_step' in e else '-'} "
            f"| {('%.3f' % e['steps_ratio_B2_over_A']) if 'steps_ratio_B2_over_A' in e else '-'} "
            f"| {('%.3f [%.3f, %.3f]' % (e['net_time_ratio_B2_over_A'], *e['net_time_ratio_ci95'])) if 'net_time_ratio_B2_over_A' in e else '-'} |")
    return "\n".join(lines) + "\n"


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "results"
    data = load_solves(out)
    solve = solve_summary(data, toys())
    rate = rate_summary(out, solve)
    summary = {"solve": solve, "rate": rate}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    text = markdown(solve, rate)
    (out / "tables.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
