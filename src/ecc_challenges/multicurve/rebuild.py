"""Explicit replay of verified S3 batches into a conditional exact point index."""
from __future__ import annotations

from .index import collision_lead_id
from .segments import MAX_INPUT_BYTES, iter_verified_records


def rebuild(
    archive,
    ns,
    verified_keys,
    point_index,
    *,
    durable_lead_handler,
    max_input_bytes=MAX_INPUT_BYTES,
):
    """Yield persisted collision candidates followed by one completion event.

    Exhaust the iterator to finish the rebuild. The handler must idempotently
    persist each complete candidate and return True only after durable commit.
    Exceptions, a false acknowledgement, or stopping iteration produce no
    completion event. Conditional index writes can safely be replayed.
    """
    if not callable(durable_lead_handler):
        raise ValueError("a durable collision lead handler is required")
    attempted = inserted = duplicates = collisions = 0
    for item in iter_verified_records(
        archive, ns, verified_keys, max_input_bytes,
    ):
        record = item["record"]
        source = item["source"]
        ns.verify(record)
        prior = point_index.put(
            ns,
            record,
            source["batch_id"],
            item["record_index"],
            item["verified_key"],
        )
        attempted += 1
        if prior is None:
            inserted += 1
            continue
        duplicates += 1
        lead_id = collision_lead_id(ns, record, prior)
        if lead_id is None:
            continue
        first = {
            "batch_id": prior["batch_id"],
            "record_index": prior["record_index"],
            "witness": bytes(prior["witness"]).hex(),
            "verified_key": prior["verified_key"],
        }
        second = {
            "batch_id": source["batch_id"],
            "record_index": item["record_index"],
            "witness": ns.witness(record).hex(),
            "verified_key": item["verified_key"],
        }
        if first["witness"] > second["witness"]:
            first, second = second, first
        candidate = {
            "protocol": 2,
            "kind": "collision-lead",
            "lead_id": lead_id,
            "namespace_id": ns.id,
            "point_key": ns.point_key(record).hex(),
            "first": first,
            "second": second,
        }
        if durable_lead_handler(candidate) is not True:
            raise RuntimeError(
                "collision lead has no durable acknowledgement"
            )
        collisions += 1
        yield candidate
    yield {
        "protocol": 2,
        "kind": "rebuild-complete",
        "namespace_id": ns.id,
        "records": attempted,
        "new_points": inserted,
        "existing_points": duplicates,
        "collision_candidates": collisions,
    }
