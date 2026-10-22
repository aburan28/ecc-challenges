#!/usr/bin/env python3
"""Progress-metrics exporter for `ecc-challenges` (Certicom challenge pool).

Sources:
  https://ecc-challenges.com/v1/status     live gateway aggregates per campaign (public, cached 30 s)
  ~/Library/Application Support/ECC2K130/ingest/evidence-internal/macbook-publisher/status-*.json
                                            content-addressed copies of the DP-ingest status.json (local history)
  problems/                                 registered challenge count (committed)
Env: PM_ECC_STATUS_URL overrides the gateway URL; PM_ECC_INGEST_DIR overrides the snapshot directory.
Run: python3 tools/progress_metrics.py [--since YYYY-MM-DD]
"""
# ---- progress-metrics exporter boilerplate (copied verbatim into each repo's tools/progress_metrics.py) ----
# Contract: print one JSON object per line to stdout. Fields: metric, value, date (YYYY-MM-DD),
# problem, unit, better (higher|lower|neutral), kind (gauge|event), label, source.
# Reads committed state at --ref via `git show`; never the working tree. stdlib only.
import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(os.environ.get("PM_REPO_PATH") or Path(__file__).resolve().parents[1])
TODAY = dt.date.today().isoformat()


def _git(*args, check=True):
    p = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip()[:200]}")
    return p.stdout


def show(ref, path):
    p = subprocess.run(["git", "-C", str(REPO), "show", f"{ref}:{path}"], capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else None


def ls(ref, path=""):
    return [l for l in _git("ls-tree", "-r", "--name-only", ref, "--", path, check=False).splitlines() if l]


def file_history(ref, path, since):
    """Yield (date, commit, text) for the newest commit per day touching `path`, newest first."""
    out = _git("log", "--first-parent", ref, f"--since={since}", "--format=%H %cs", "--", path, check=False)
    seen = set()
    for line in out.splitlines():
        h, d = line.split()
        if d in seen:
            continue
        seen.add(d)
        txt = show(h, path)
        if txt is not None:
            yield d, h[:12], txt


def emit(metric, value, *, date=None, problem="misc", unit="", better="neutral", kind="gauge", label="", source=""):
    if value is None:
        return
    try:
        v = float(value)
    except (TypeError, ValueError):
        return
    if v != v:
        return
    print(json.dumps({"metric": metric, "value": v, "date": date or TODAY, "problem": problem, "unit": unit,
                      "better": better, "kind": kind, "label": label or metric, "source": source}))


def args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default=os.environ.get("PM_REF", "origin/main"))
    ap.add_argument("--since", default=os.environ.get("PM_SINCE") or (dt.date.today() - dt.timedelta(days=90)).isoformat())
    return ap.parse_args()
# ---- end boilerplate ----

import urllib.request

P_POOL = "certicom-pool"
P_RHO = "ecc2k130-rho-throughput"
STATUS_URL = os.environ.get("PM_ECC_STATUS_URL", "https://ecc-challenges.com/v1/status")
INGEST_DIR = Path(os.environ.get("PM_ECC_INGEST_DIR") or
                  Path.home() / "Library/Application Support/ECC2K130/ingest/evidence-internal/macbook-publisher")


def _fetch_json(url):
    last = None
    for _ in range(2):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            last = e
    try:  # some proxies truncate urllib's chunked reads; curl copes
        p = subprocess.run(["curl", "-fsS", "--max-time", "40", url], capture_output=True, text=True, timeout=60)
        if p.returncode == 0:
            return json.loads(p.stdout)
    except Exception as e:  # noqa: BLE001
        last = e
    raise RuntimeError(str(last))


def gateway():
    try:
        d = _fetch_json(STATUS_URL)
    except Exception as e:  # noqa: BLE001
        print(f"gateway unavailable: {e}", file=sys.stderr)
        return
    src = STATUS_URL
    emit("pool.total.archived_points", d.get("total_archived_points"), problem=P_POOL, unit="DPs", better="higher",
         label="pool: archived distinguished points (all campaigns)", source=src)
    emit("pool.total.points_per_hour", d.get("total_points_per_hour"), problem=P_POOL, unit="DP/h", better="higher",
         label="pool: distinguished points per hour (24h window)", source=src)
    emit("pool.total.estimated_work_log2", d.get("total_estimated_work_log2"), problem=P_POOL, unit="log2 ops",
         better="higher", label="pool: estimated work done, public runs", source=src)
    emit("pool.total.estimated_work_log2_incl_hist", d.get("total_estimated_work_log2_including_historical"),
         problem=P_POOL, unit="log2 ops", better="higher", label="pool: estimated work incl. historical import", source=src)
    emit("pool.compute_ready", d.get("compute_ready"), problem=P_POOL, unit="count", better="higher",
         label="pool: campaigns compute-ready", source=src)
    for c in d.get("campaigns") or []:
        cid = str(c.get("id", "")).replace("_", "-")
        active = (c.get("received_points") or 0) >= 100 or (c.get("points_per_hour") or 0) > 0 \
            or (c.get("recent_runs_24h") or 0) > 0
        if not cid or not active:
            continue
        pr = P_RHO if cid == "ecc2k-130" else P_POOL
        emit(f"pool.{cid}.received_points", c.get("received_points"), problem=pr, unit="DPs", better="higher",
             label=f"{cid}: distinguished points received", source=src)
        emit(f"pool.{cid}.points_per_hour", c.get("points_per_hour"), problem=pr, unit="DP/h", better="higher",
             label=f"{cid}: points per hour (24h)", source=src)
        emit(f"pool.{cid}.ops_per_second_log2", c.get("operations_per_second_log2"), problem=pr, unit="log2 ops/s",
             better="higher", label=f"{cid}: aggregate walk rate (log2 ops/s)", source=src)
        emit(f"pool.{cid}.expected_work_pct", c.get("expected_work_percent"), problem=pr, unit="%", better="higher",
             label=f"{cid}: % of expected rho work done", source=src)
        emit(f"pool.{cid}.hours_to_expected_work", c.get("hours_to_expected_work"), problem=pr, unit="h", better="lower",
             label=f"{cid}: hours to expected work at current rate", source=src)
        emit(f"pool.{cid}.runs_24h", c.get("recent_runs_24h"), problem=pr, unit="count", better="higher",
             label=f"{cid}: worker runs seen in last 24h", source=src)
        emit(f"pool.{cid}.total_runs", c.get("total_runs"), problem=pr, unit="count", better="higher",
             label=f"{cid}: worker runs, total", source=src)
    v = d.get("verification") or {}
    emit("pool.verification.failed_batches", v.get("failed_batches"), problem=P_POOL, unit="count", better="lower",
         label="pool: failed verification batches", source=src)
    emit("pool.verification.unverified_batches", v.get("unverified_batches"), problem=P_POOL, unit="count",
         better="lower", label="pool: batches awaiting verification", source=src)


def ingest_history(since):
    if not INGEST_DIR.is_dir():
        print(f"no ingest snapshot dir {INGEST_DIR}", file=sys.stderr)
        return
    last, best_rate, lag_min = {}, {}, {}
    for f in INGEST_DIR.glob("status-*.json"):
        try:
            d = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        day = (d.get("generated_at") or "")[:10]
        if not day or day < since:
            continue
        ts = d.get("generated_at")
        if day not in last or ts > last[day][0]:
            last[day] = (ts, d)
        rate = ((d.get("walk_rate") or {}).get("iterations_per_second")) or 0
        best_rate[day] = max(best_rate.get(day, 0), rate)
        lag = (d.get("ingest") or {}).get("lag_seconds")
        if lag is not None:
            lag_min[day] = min(lag_min.get(day, lag), lag)
    src = "ECC2K130 dp_ingest status.json snapshots"
    for day, (_ts, d) in last.items():
        w = d.get("work") or {}
        emit("ingest.ecc2k130.dps", d.get("dps"), date=day, problem=P_RHO, unit="DPs", better="higher",
             label="ECC2K-130 ingest: distinguished points stored", source=src)
        emit("ingest.ecc2k130.dps_last_day", d.get("dps_last_day"), date=day, problem=P_RHO, unit="DP/day",
             better="higher", label="ECC2K-130 ingest: DPs in trailing 24h", source=src)
        emit("ingest.ecc2k130.iterations_log2", w.get("iterations_log2"), date=day, problem=P_RHO, unit="log2 it",
             better="higher", label="ECC2K-130 ingest: total walk iterations (log2)", source=src)
        emit("ingest.ecc2k130.walkers", d.get("walkers"), date=day, problem=P_RHO, unit="count", better="higher",
             label="ECC2K-130 ingest: walkers reporting (end of day)", source=src)
        emit("ingest.ecc2k130.walking_slots", w.get("walking_slots"), date=day, problem=P_RHO, unit="count",
             better="higher", label="ECC2K-130 ingest: walking slots (end of day)", source=src)
        emit("ingest.ecc2k130.collisions", d.get("collisions"), date=day, problem=P_RHO, unit="count", better="higher",
             label="ECC2K-130: collisions found", source=src)
    for day, r in best_rate.items():
        emit("ingest.ecc2k130.walk_rate_ips_peak", r, date=day, problem=P_RHO, unit="it/s", better="higher",
             label="ECC2K-130 ingest: peak measured walk rate that day", source=src)
    for day, l in lag_min.items():
        emit("ingest.ecc2k130.lag_seconds_min", l, date=day, problem=P_RHO, unit="s", better="lower",
             label="ECC2K-130 ingest: best ingest lag that day", source=src)


def problems(ref):
    n = len({p.split("/")[1] for p in ls(ref, "problems") if "/" in p})
    emit("challenges.registered", n, problem=P_POOL, unit="count", better="higher",
         label="challenge problems registered in repo", source="problems/")


def main():
    a = args()
    problems(a.ref)
    gateway()
    ingest_history(a.since)


if __name__ == "__main__":
    main()
