#!/usr/bin/env python3
"""Translate measured worker and service rates into an admission capacity check."""

from __future__ import annotations

import argparse
import json
import math


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpus", type=int, required=True)
    parser.add_argument("--steps-per-gpu-second", type=float, required=True)
    parser.add_argument("--steps-per-dp", type=float, required=True)
    parser.add_argument("--index-rows-per-task-second", type=float, required=True)
    parser.add_argument("--audit-seconds-per-record", type=float, required=True)
    parser.add_argument("--new-runs-per-hour", type=float, required=True)
    parser.add_argument("--batch-records", type=int, default=32768)
    parser.add_argument("--audit-one-in", type=int, default=128)
    parser.add_argument("--headroom", type=float, default=2.0)
    args = parser.parse_args()
    if any(value <= 0 for value in (args.gpus, args.steps_per_gpu_second,
                                    args.steps_per_dp, args.index_rows_per_task_second,
                                    args.audit_seconds_per_record, args.batch_records,
                                    args.audit_one_in, args.headroom)) or args.new_runs_per_hour < 0:
        parser.error("rates and capacities must be positive; new runs may be zero")
    dps = args.gpus * args.steps_per_gpu_second / args.steps_per_dp
    batches = dps / args.batch_records
    # Every new run's first batch is selected. Later batches have a 1/N
    # selection probability. The estimate is conservative if a run is short.
    first = args.new_runs_per_hour / 3600
    selected = first + max(0, batches - first) / args.audit_one_in
    per_auditor = 1 / args.audit_seconds_per_record
    result = {
        "rates_supplied_by_operator": True,
        "dp_arrival_per_second": dps,
        "batch_arrival_per_second": batches,
        "s3_payload_mib_per_second": dps * 32 / 2**20,
        "selected_records_per_second": selected,
        "index_tasks_with_headroom": math.ceil(args.headroom * dps / args.index_rows_per_task_second),
        "audit_tasks_with_headroom": math.ceil(args.headroom * selected / per_auditor),
        "configured_audit_task_ceiling": 32,
        "configured_index_task_ceiling": 8,
        "sustainable_with_configured_ceilings": (
            args.headroom * dps <= 8 * args.index_rows_per_task_second and
            args.headroom * selected <= 32 * per_auditor
        ),
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
