#!/usr/bin/env python3
"""Apply the decision rules frozen in PROTOCOL.md (arms A, B, A2, C),
PROTOCOL-AMENDMENT-1.md (arm B2) and PROTOCOL-AMENDMENT-2.md (arm B3) to
results/ and write results/summary.json and results/tables.md. Reads recorded
values only.

The bootstrap draws for the frozen arms come from one generator in the frozen
order (seed 1), so the frozen numbers do not move when arms are added; B2 draws
come from a second generator (seed 3), B3 draws from a third (seed 4)."""
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

RESAMPLES, SEED, SEED_B2, SEED_B3 = 10000, 1, 3, 4
WALKS = ("B", "B2", "B3")      # frozen run, amendment 1, amendment 2
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
        "trails_completed": sum(r["dps"] + r["cap_restarts"] for r in rows),
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
    rngs = {"B2": np.random.default_rng(SEED_B2), "B3": np.random.default_rng(SEED_B3)}
    rng = np.random.default_rng(SEED)
    result = {"instances": {}, "integrity": {}}
    for instance in plan.PRIMARY + plan.SECONDARY:
        n = int(instance_rows[instance]["n"], 16)
        entry = {"n": n, "log2_n": math.log2(n), "field": instance_rows[instance]["field_kind"],
                 "E_v2": math.sqrt(math.pi * n / 2), "E_neg": math.sqrt(math.pi * n / 4), "arms": {}}
        draws = {}
        for arm in ("A", "B", "A2", "C", "B2", "B3"):
            rows = data.get((instance, arm))
            if not rows:
                continue
            draws[arm] = boot_mean(rngs.get(arm, rng), [r["steps"] for r in rows])
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
        entry["ratio_A2_over_A_ci99"] = ratio("A2", "A", 0.99)
        entry["ratio_C_over_A"] = ratio("C", "A")
        for arm in WALKS:
            entry[f"ratio_{arm}_over_A"] = ratio(arm, "A")
            r = entry[f"ratio_{arm}_over_A"]
            if r:
                p = priced(entry, arm, r)
                entry[f"priced_cost_ratio_{arm}"] = p["cost_ratio"]
                entry[f"lambda_star_{arm}"] = p["lambda_star"]
                entry[f"priced_order_holds_across_band_{arm}"] = p["order_holds_across_band"]
                entry[f"verified_step_reduction_{arm}"] = r["ci"][1] < 1
                entry[f"covers_theory_1_over_sqrt2_{arm}"] = r["ci"][0] <= THEORY_RATIO <= r["ci"][1]
        entry["ratio_B2_over_B"] = ratio("B2", "B")
        entry["ratio_B3_over_B2"] = ratio("B3", "B2")
        entry["ratio_B3_over_B"] = ratio("B3", "B")
        result["instances"][instance] = entry
        entry["_draws"] = draws

    def pooled(arm):
        if not all(arm in result["instances"][i]["_draws"] and "A" in result["instances"][i]["_draws"] for i in plan.PRIMARY):
            return None
        total = np.zeros(RESAMPLES)
        for i in plan.PRIMARY:
            d = result["instances"][i]["_draws"]
            total += np.log(d[arm] / d["A"]) / len(plan.PRIMARY)
        point = math.exp(statistics.fmean(math.log(result["instances"][i][f"ratio_{arm}_over_A"]["ratio"]) for i in plan.PRIMARY))
        return {"ratio": point, "ci": [math.exp(v) for v in interval(total)]}
    for arm in WALKS:
        result[f"pooled_primary_geometric_mean_{arm}_over_A"] = pooled(arm)
        if result[f"pooled_primary_geometric_mean_{arm}_over_A"]:
            result[f"verdict_steps_{arm}"] = ("verified step reduction at all four primary sizes"
                                              if all(result["instances"][i][f"verified_step_reduction_{arm}"] for i in plan.PRIMARY)
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
        reps = {"A": [], "B3": []}
        for arm in reps:
            for path in sorted((out / "rate").glob(f"{instance}.{arm}.*.json")):
                reps[arm].append(json.loads(path.read_text()))
        if not reps["A"] or not reps["B3"]:
            continue
        ns_a = np.array([r["ns_per_step"] for r in reps["A"]])
        ns_b = np.array([r["ns_per_step"] for r in reps["B3"]])
        draws_a = np.median(ns_a[rng.integers(0, len(ns_a), (RESAMPLES, len(ns_a)))], axis=1)
        draws_b = np.median(ns_b[rng.integers(0, len(ns_b), (RESAMPLES, len(ns_b)))], axis=1)
        ratio = float(np.median(ns_b) / np.median(ns_a))
        addtime = [json.loads(path.read_text()) for path in [out / "rate" / f"{instance}.addtime.json"]
                   if path.exists()]
        ns_add = statistics.median(r["ns_per_add"] for r in addtime[0]) if addtime else None
        eps = sum(r["lookaheads"] for r in reps["B3"]) / sum(r["steps"] for r in reps["B3"])
        entry = {
            "repetitions": [len(ns_a), len(ns_b)],
            "ns_per_step_A_median": float(np.median(ns_a)), "ns_per_step_B3_median": float(np.median(ns_b)),
            "ns_per_step_A_iqr": [float(np.percentile(ns_a, 25)), float(np.percentile(ns_a, 75))],
            "ns_per_step_B3_iqr": [float(np.percentile(ns_b, 25)), float(np.percentile(ns_b, 75))],
            "time_per_step_ratio_B3_over_A": ratio, "time_per_step_ratio_ci95": interval(draws_b / draws_a),
            "rate_loss_per_step": 1 - 1 / ratio,
            "rate_loss_ci95": [1 - 1 / v for v in interval(draws_b / draws_a)],
            "lookaheads_per_step_B3": eps,
            "ns_per_group_addition": ns_add,
        }
        if ns_add:
            overhead = (np.median(ns_b) - np.median(ns_a)) - eps * ns_add
            entry["native_overhead_gae_per_step"] = float(overhead / ns_add)
        if instance in solve["instances"] and solve["instances"][instance].get("ratio_B3_over_A"):
            r = solve["instances"][instance]["ratio_B3_over_A"]
            entry["steps_ratio_B3_over_A"] = r["ratio"]
            entry["net_time_ratio_B3_over_A"] = r["ratio"] * ratio
            entry["net_time_ratio_ci95"] = [r["ci"][0] * interval(draws_b / draws_a)[0],
                                            r["ci"][1] * interval(draws_b / draws_a)[1]]
        result[instance] = entry
    return result


def fmt_ratio(r):
    return "-" if not r else f"{r['ratio']:.3f} [{r['ci'][0]:.3f}, {r['ci'][1]:.3f}]"


def longtrail_summary(out: Path):
    result = {}
    for path in sorted((out / "longtrail").glob("*.json")) if (out / "longtrail").exists() else []:
        instance, arm = path.name.split(".")[:2]
        result.setdefault(instance, {})[arm] = json.loads(path.read_text())
    return result


def markdown(solve, rate, longtrail):
    inst = solve["instances"]
    lines = ["## Mean steps per solve (lockstep steps; N solves per arm)", "",
             "B = walk as first measured (frozen run), B2 = amendment 1, B3 = amendment 2 (the shipped walk).", "",
             "| instance | log2 n | N (A/B/B2/B3) | mean A | mean B | mean B2 | mean B3 | A / sqrt(pi n/2) | B / sqrt(pi n/4) | B2 / sqrt(pi n/4) | B3 / sqrt(pi n/4) |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, e in inst.items():
        arms = {k: e["arms"].get(k) for k in ("A", "B", "B2", "B3")}
        if not (arms["A"] and arms["B"]):
            continue
        counts = "/".join(str(arms[k]["solves"]) if arms[k] else "-" for k in ("A", "B", "B2", "B3"))
        means = " | ".join(("%.0f" % arms[k]["mean_steps"]) if arms[k] else "-" for k in ("A", "B", "B2", "B3"))
        norms = " | ".join(("%.3f" % (arms[k]["mean_steps"] / e["E_neg"])) if arms[k] else "-" for k in ("B", "B2", "B3"))
        lines.append(f"| {name} | {e['log2_n']:.2f} | {counts} | {means} | {arms['A']['mean_steps'] / e['E_v2']:.3f} | {norms} |")
    lines += ["", "## Step ratios to the v2 walk A, 95% bootstrap intervals (1/sqrt2 = 0.707)", "",
              "| instance | B/A (frozen) | B2/A (amend. 1) | B3/A (amend. 2) | covers 1/sqrt2 (B, B2, B3) | B3/B2 | A2/A (99%) | C/A |",
              "|---|---|---|---|---|---|---|---|"]
    for name, e in inst.items():
        if not e["ratio_B_over_A"]:
            continue
        cover = ", ".join(("yes" if e.get(f"covers_theory_1_over_sqrt2_{k}") else "no") if e[f"ratio_{k}_over_A"] else "-" for k in WALKS)
        lines.append(
            f"| {name} | {fmt_ratio(e['ratio_B_over_A'])} | {fmt_ratio(e['ratio_B2_over_A'])} | {fmt_ratio(e['ratio_B3_over_A'])} | {cover} "
            f"| {fmt_ratio(e['ratio_B3_over_B2'])} | {fmt_ratio(e['ratio_A2_over_A_ci99'])} | {fmt_ratio(e['ratio_C_over_A'])} |")
    for arm, label in (("B", "B/A (frozen)"), ("B2", "B2/A (amendment 1, post hoc)"), ("B3", "B3/A (amendment 2, post hoc)")):
        p = solve.get(f"pooled_primary_geometric_mean_{arm}_over_A")
        if p:
            lines += ["", f"Pooled geometric mean of {label} over the four primary sizes: {p['ratio']:.3f} [{p['ci'][0]:.3f}, {p['ci'][1]:.3f}]; "
                      f"{solve.get('verdict_steps_' + arm, '')}."]
    lines += ["", f"Controls: A/A pass = {solve['control_AA_pass']}; positive control (C/A lower bound > 2) pass = {solve['control_positive_pass']}; "
              f"A calibration in [0.8, 1.5] = {solve['calibration_A_in_0.8_1.5']}; reproduction gate (amended source reproduces the earlier arms) pass = {solve['reproduction_gate_pass']}.", "",
              "## Walk operations and trapped trails", "",
              "| instance | adds/step B / B2 / B3 | escapes per 1e6 steps B / B2 / B3 | solves with a trapped trail (>200 escapes) B / B2 / B3 | trails completed (DPs + cap restarts), B3 | trail restarts at the cap per solve A / B / B2 / B3 / C |",
              "|---|---|---|---|---|---|"]
    for name, e in inst.items():
        a, b, b2, b3, c = (e["arms"].get(k) for k in ("A", "B", "B2", "B3", "C"))
        if not b:
            continue
        f = lambda x, key, fmt: (fmt % x[key]) if x else "-"
        lines.append(
            f"| {name} | {' / '.join(f(x, 'adds_per_step', '%.4f') for x in (b, b2, b3))} "
            f"| {' / '.join(f(x, 'escapes_per_million_steps', '%.0f') for x in (b, b2, b3))} "
            f"| {' / '.join(f(x, 'solves_with_trapped_trail', '%d') for x in (b, b2, b3))} "
            f"| {f(b3, 'trails_completed', '%d')} "
            f"| {' / '.join(f(x, 'cap_restarts_per_solve', '%.2f') for x in (a, b, b2, b3, c))} |")
    if longtrail:
        lines += ["", "## Long-trail trap check (16 lanes x 2^20 steps, no distinguished points)", "",
                  "Descriptive. Only 16 trails per run: B2's trapped trails start early in a trail (ages 256 to 6656 steps in the solve phase, about 1 in 900 trails at 2^34.5: 25 trapped solves in 22,720 trails), so this check cannot see them (expected 0.02 per run); it shows only that no lane entered a trap later in life. The evidence on trapping is the solve phase above.", "",
                  "| curve | arm | steps | escapes per 1e6 steps | lane-rounds with more than 12 escapes (trapped) | max escapes in one lane-round |", "|---|---|---|---|---|---|"]
        for name, arms in longtrail.items():
            for arm, r in arms.items():
                lines.append(f"| {name} | {arm} | {r['steps']} | {1e6 * r['escapes'] / r['steps']:.0f} | {r['trapped_lane_rounds']} | {r['max_lane_round_escapes']} |")
    lines += ["", "## Supplementary per-step timing from the solve phase (post hoc, not the frozen M2)", "",
              "ns per step = walk time / physical steps over all solves of an arm (two driver processes at a time, arms interleaved in time; A2/A shows the noise level).", "",
              "| instance | A ns/step | A2/A | B/A | B2/A | B3/A |", "|---|---|---|---|---|---|"]
    ratios = []
    for name, e in inst.items():
        a = e["arms"]["A"]["walk_ns_per_step"]
        cells = [("%.3f" % (e["arms"][k]["walk_ns_per_step"] / a)) if k in e["arms"] else "-" for k in ("A2", "B", "B2", "B3")]
        if "B3" in e["arms"]:
            ratios.append(e["arms"]["B3"]["walk_ns_per_step"] / a)
        lines.append(f"| {name} | {a:.0f} | " + " | ".join(cells) + " |")
    if ratios:
        solve["solve_phase_time_per_step_ratio_B3_over_A"] = {"mean": statistics.fmean(ratios), "min": min(ratios), "max": max(ratios)}
        lines += ["", f"B3/A time per step over the {len(ratios)} instances: mean {statistics.fmean(ratios):.3f}, range {min(ratios):.3f} to {max(ratios):.3f}."]
    lines += ["", "## Priced comparison (group additions + lambda x native operations per solve; walk over A)", "",
              "| instance | arm | ratio at lambda=0 | lambda=0.1 | lambda=1 | break-even lambda | order holds across 0..1 |", "|---|---|---|---|---|---|---|"]
    for name, e in inst.items():
        for arm in WALKS:
            if f"priced_cost_ratio_{arm}" in e:
                p, star = e[f"priced_cost_ratio_{arm}"], e[f"lambda_star_{arm}"]
                lines.append(f"| {name} | {arm} | {p['0.0']:.3f} | {p['0.1']:.3f} | {p['1.0']:.3f} | {('%.2f' % star) if star else 'none'} "
                             f"| {'yes' if e[f'priced_order_holds_across_band_{arm}'] else 'no'} |")
    lines += ["", "## Rate of the shipped walk B3 vs A (CPU, single thread, dp_bits=40, 15 alternating runs)", "",
              "| curve | ns/step A (IQR) | ns/step B3 (IQR) | B3/A time per step [95% CI] | rate loss per step | native overhead (GAE/step) | steps B3/A | net time B3/A |",
              "|---|---|---|---|---|---|---|---|"]
    for name, e in rate.items():
        lines.append(
            f"| {name} | {e['ns_per_step_A_median']:.0f} ({e['ns_per_step_A_iqr'][0]:.0f}-{e['ns_per_step_A_iqr'][1]:.0f}) "
            f"| {e['ns_per_step_B3_median']:.0f} ({e['ns_per_step_B3_iqr'][0]:.0f}-{e['ns_per_step_B3_iqr'][1]:.0f}) "
            f"| {e['time_per_step_ratio_B3_over_A']:.3f} [{e['time_per_step_ratio_ci95'][0]:.3f}, {e['time_per_step_ratio_ci95'][1]:.3f}] "
            f"| {100 * e['rate_loss_per_step']:.1f}% [{100 * e['rate_loss_ci95'][0]:.1f}, {100 * e['rate_loss_ci95'][1]:.1f}] "
            f"| {('%.3f' % e['native_overhead_gae_per_step']) if 'native_overhead_gae_per_step' in e else '-'} "
            f"| {('%.3f' % e['steps_ratio_B3_over_A']) if 'steps_ratio_B3_over_A' in e else '-'} "
            f"| {('%.3f [%.3f, %.3f]' % (e['net_time_ratio_B3_over_A'], *e['net_time_ratio_ci95'])) if 'net_time_ratio_B3_over_A' in e else '-'} |")
    return "\n".join(lines) + "\n"


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "results"
    data = load_solves(out)
    solve = solve_summary(data, toys())
    rate = rate_summary(out, solve)
    longtrail = longtrail_summary(out)
    text = markdown(solve, rate, longtrail)          # also records the supplementary timing in `solve`
    summary = {"solve": solve, "rate": rate, "longtrail": longtrail}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    (out / "tables.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
