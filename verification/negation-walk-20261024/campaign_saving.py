#!/usr/bin/env python3
"""Expected campaign-level work under the v2 walk and the negation-class walk,
from the constants measured in results/summary.json (run after analyze.py).

Model (the one src/ecc_challenges/work.py uses): the number of steps to the
first collision is Rayleigh with mean E = c * sqrt(n). For a walk that has done
k steps without a collision the expected remaining work is
E * erfc(x) * exp(x^2) with x = sqrt(pi) * k / (2E). Work done under one walk
does not count towards another's collision (separate namespaces), so a switch
restarts the clock: the negation walk's remaining work is its full E.
"""
import json
import math
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
RESULTS = Path(os.environ.get("STUDY_RESULTS", HERE / "results"))
REPO = HERE.parents[1]
sys.path[:0] = [str(REPO / "src")]
from ecc_challenges.multicurve.instances import all_instances  # noqa: E402

summary = json.loads((RESULTS / "summary.json").read_text())
solve, rate = summary["solve"]["instances"], summary["rate"]
PRIMARY = [i for i in solve if i.startswith("toy-prime")]
c_a = sum(solve[i]["arms"]["A"]["mean_over_sqrt_n"] for i in PRIMARY) / len(PRIMARY)
c_frozen = sum(solve[i]["arms"]["B"]["mean_over_sqrt_n"] for i in PRIMARY) / len(PRIMARY)
c_b2 = sum(solve[i]["arms"]["B2"]["mean_over_sqrt_n"] for i in PRIMARY) / len(PRIMARY)
# The walk as shipped is the one of amendment 2 (B3); B and B2 are reported for the record.
c_b = sum(solve[i]["arms"]["B3"]["mean_over_sqrt_n"] for i in PRIMARY) / len(PRIMARY)
steps_ratio = c_b / c_a
time_ratios = {c: rate[c]["time_per_step_ratio_B3_over_A"] for c in ("eccp-79", "ecc2-79") if c in rate}
real_field_ratio = sum(time_ratios.values()) / len(time_ratios)
# Conservative: the mean of the seven solve-phase paired timings (about 10^6 steps per arm and instance, A2/A within 1%),
# which is larger than the noisy dedicated runs on the real 79-bit fields; the overhead share only falls as fields grow.
time_ratio = summary["solve"]["solve_phase_time_per_step_ratio_B3_over_A"]["mean"]

rows = []
for instance in all_instances():
    n = instance.n
    e_v2 = math.sqrt(math.pi * n / 2)
    e_neg = math.sqrt(math.pi * n / 4)
    koblitz = instance.id.startswith("ecc2k")
    row = {
        "campaign": instance.id, "log2_n": math.log2(n),
        "log2_E_v2_theory": math.log2(e_v2), "log2_E_neg_theory": math.log2(e_neg),
        # The measured quantity is the ratio; the absolute scale is theory, because the toy-size means include
        # ~7% of distinguished-point lag and finite-walk excess that production densities (2^-23 of E per point) shrink.
        "log2_E_v2_measured_constant": math.log2(e_v2),
        "log2_E_neg_measured_constant": math.log2(e_v2 * steps_ratio),
        "log2_E_neg_time_equivalent": math.log2(e_v2 * steps_ratio * time_ratio),
        "steps_factor": c_b / c_a, "time_factor": c_b / c_a * time_ratio,
    }
    if koblitz:
        m = instance.bits
        row["log2_E_frobenius_follow_up_theory"] = math.log2(math.sqrt(math.pi * n / (4 * m)))
        row["frobenius_extra_factor_sqrt_m"] = math.sqrt(m)
    if instance.id == "ecc2k-130":
        row["production_v1_sigma_log2_E"] = math.log2(math.sqrt(math.pi * n / (4 * 131)))
    rows.append(row)

def remaining_over_E(fraction):
    x = math.sqrt(math.pi) * fraction / 2
    return math.erfc(x) * math.exp(x * x)

progress = [{"fraction_of_E_v2_done": f, "v2_remaining_over_E_v2": remaining_over_E(f),
             "negation_remaining_over_E_v2_steps": steps_ratio,
             "negation_remaining_over_E_v2_time": steps_ratio * time_ratio}
            for f in (0.0, 0.05, 0.1, 0.2, 0.3, 0.5, 1.0)]
# Largest v2 progress (fraction of E_v2) at which switching still lowers the remaining steps / time.
def break_even(target):
    low, high = 0.0, 5.0
    for _ in range(80):
        mid = (low + high) / 2
        low, high = (mid, high) if remaining_over_E(mid) > target else (low, mid)
    return low
out = {"constant_v2_over_sqrt_n": c_a, "constant_negation_over_sqrt_n": c_b,
       "constant_frozen_arm_B_over_sqrt_n": c_frozen, "steps_ratio_frozen_arm_B": c_frozen / c_a,
       "constant_arm_B2_over_sqrt_n": c_b2, "steps_ratio_arm_B2": c_b2 / c_a, "steps_ratio": steps_ratio,
       "time_per_step_ratio_real_fields": time_ratios, "time_per_step_ratio_real_fields_mean": real_field_ratio,
       "time_ratio_used": time_ratio,
       "net_time_ratio": steps_ratio * time_ratio,
       "break_even_fraction_steps": break_even(steps_ratio),
       "break_even_fraction_time": break_even(steps_ratio * time_ratio) if steps_ratio * time_ratio < 1 else None,
       "progress_table": progress, "campaigns": rows}
(RESULTS / "campaign_saving.json").write_text(json.dumps(out, indent=1) + "\n")
lines = [f"Measured constants (mean over the four primary prime sizes; they sit about 7% above theory at these toy sizes (distinguished-point lag, finite walk), so the tables below scale the theoretical E by the measured ratios): v2 mean steps = {c_a:.3f} sqrt(n) (theory sqrt(pi/2) = 1.253), "
         f"shipped negation walk B3 {c_b:.3f} sqrt(n) (theory sqrt(pi/4) = 0.886); earlier arms B {c_frozen:.3f}, B2 {c_b2:.3f} sqrt(n). "
         f"Steps ratio B3/A {steps_ratio:.3f} (B/A {c_frozen / c_a:.3f}, B2/A {c_b2 / c_a:.3f}); time per step ratio {time_ratio:.3f} (mean of the seven solve-phase paired timings; the noisier dedicated runs on the 79-bit fields give {real_field_ratio:.3f}); net time ratio {steps_ratio * time_ratio:.3f}.",
         "", "| campaign | log2 E v2 (sqrt(pi n/2)) | log2 E negation (v2 E x measured steps ratio) | steps saved | log2 negation in v2-step time units | note |", "|---|---|---|---|---|---|"]
for r in rows:
    note = ""
    if "log2_E_frobenius_follow_up_theory" in r:
        note = f"Frobenius follow-up: log2 E = {r['log2_E_frobenius_follow_up_theory']:.2f}"
    if "production_v1_sigma_log2_E" in r:
        note += f"; production v1 sigma walk log2 E = {r['production_v1_sigma_log2_E']:.2f} (the negation-only walk is worse here)"
    lines.append(f"| {r['campaign']} | {r['log2_E_v2_measured_constant']:.2f} | {r['log2_E_neg_measured_constant']:.2f} | {100 * (1 - r['steps_factor']):.0f}% | {r['log2_E_neg_time_equivalent']:.2f} | {note} |")
lines += ["", "| v2 work done (fraction of E_v2) | v2 remaining (units of E_v2) | negation walk from scratch (steps) | (time) |", "|---|---|---|---|"]
for p in progress:
    lines.append(f"| {p['fraction_of_E_v2_done']:.2f} | {p['v2_remaining_over_E_v2']:.3f} | {p['negation_remaining_over_E_v2_steps']:.3f} | {p['negation_remaining_over_E_v2_time']:.3f} |")
lines += ["", f"Switching a campaign to the negation walk lowers the remaining expected steps only while v2 has done less than {break_even(steps_ratio):.2f} of its E_v2"
          + (f", and the remaining time while less than {break_even(steps_ratio * time_ratio):.2f}." if steps_ratio * time_ratio < 1 else "; it never lowers the remaining time.")]
(RESULTS / "campaign_saving.md").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
