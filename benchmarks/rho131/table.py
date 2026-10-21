#!/usr/bin/env python3
"""Render benchmarks/rho131/results/*.jsonl as the Markdown tables in README.md."""
import json
import sys
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"


def rows(path):
    out, meta = [], {}
    for line in path.read_text().splitlines():
        if line.startswith("{"):
            item = json.loads(line)
            if "meta" in item:
                meta = item["meta"]
            else:
                out.append(item)
    return out, meta


def fmt(n):
    return f"{n:,.0f}"


def table(title, data, meta, cpu, runs):
    if not data:
        return ""
    base = next((r for r in data if r["stage"] == "S0"), None)
    key = "steps_per_engine_cpu_s" if cpu else "steps_per_s"
    best = "best_" + key
    head = ("| Stage | Walk | Engine CPU-s median | Engine CPU-s best | Wall, with Python checks | vs S0 (wall) |"
            if cpu else "| Stage | Walk | Steps/s median | Steps/s best | vs S0 |")
    lines = [f"### {title}", "", head, "|" + " --- |" * (6 if cpu else 5)]
    for r in data:
        ratio = f"{r['steps_per_s'] / base['steps_per_s']:.1f}x" if base else ""
        if cpu:
            lines.append(f"| {r['stage']} {r['label']} | {r['walk']} | {fmt(r[key])} | {fmt(r[best])} "
                         f"| {fmt(r['steps_per_s'])} | {ratio} |")
        else:
            lines.append(f"| {r['stage']} {r['label']} | {r['walk']} | {fmt(r['steps_per_s'])} "
                         f"| {fmt(r['best_steps_per_s'])} | {ratio} |")
    first = data[0]
    lines += ["", f"{first['threads']} thread(s), {first['lanes']} lanes, {first['round_steps']} steps per round; "
              f"best of {runs} run(s) per row; host load average at the end of a run {meta.get('load', ['?'])[0]:.0f}.", ""]
    return "\n".join(lines)


def merged(paths):
    """Per row, the run that did best: the least disturbed one on a shared host."""
    best, meta = {}, {}
    for path in paths:
        data, run_meta = rows(path)
        meta = meta or run_meta
        for r in data:
            key = (r["stage"], r["label"])
            score = r["best_steps_per_engine_cpu_s"] or r["best_steps_per_s"]
            if key not in best or score > best[key][0]:
                best[key] = (score, r)
    return [best[k][1] for k in best], meta


def main():
    groups = {}
    for path in sorted(RESULTS.glob("m4pro-*.jsonl")):
        groups.setdefault(path.stem.rsplit("-run", 1)[0], []).append(path)
    out = []
    for name, paths in groups.items():
        data, meta = merged(paths)
        out.append(table(name, data, meta, cpu="-cpu-" in name, runs=len(paths)))
    print("\n".join(out))


if __name__ == "__main__":
    sys.exit(main())
