"""Bounded immutable compaction of verified affine DP batches.

Raw batches and verification receipts remain the recovery authority. Segments
preserve every record and its original batch/ordinal; compaction deletes nothing.
"""
from __future__ import annotations

import hashlib
import json
import re
import struct
from uuid import UUID

from .codec import MAX_BATCH_BYTES, PREFIX, canonical

MAGIC = b"ECCSEG02"
U32 = struct.Struct(">I")
REF_BYTES = 20
MAX_INPUT_BYTES = 256 * 1024 * 1024
MAX_RECEIPTS = 4096
MAX_RECEIPT_BYTES = 64 * 1024
MAX_HEADER_BYTES = 16 * 1024 * 1024
HEX = re.compile(r"^[0-9a-f]{64}$")
SORT = "affine-point,witness,record-prefix,batch-uuid,ordinal-bytes-v1"
CHECKS = frozenset({
    "version-pinned raw SHA-256 and length",
    "namespace codec canonicality",
    "finite affine point on assigned curve",
    "namespace distinguished-point predicate",
    "canonical a and b with point = aG + bQ",
})


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("integer is outside the format bound")
    return value


def _uuid(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("noncanonical UUID")
    return value


def _receipt_path(prefix, ns_id, kind, batch_id, key):
    expected = f"{prefix}/{kind}/{ns_id}/{batch_id}/"
    if not isinstance(key, str) or not key.startswith(expected):
        raise ValueError("receipt path has a different namespace or batch")
    tail = key[len(expected):]
    if not tail.endswith(".json") or HEX.fullmatch(tail[:-5]) is None:
        raise ValueError("invalid content-addressed receipt path")
    if len(key.encode()) > 1024:
        raise ValueError("receipt key exceeds the S3 key bound")
    return tail[:-5]


def _read(archive, key, limit, version=None):
    params = {"Bucket": archive.bucket, "Key": key}
    if version is not None:
        params["VersionId"] = version
    response = archive.s3.get_object(**params)
    body = response["Body"]
    try:
        length = response.get("ContentLength")
        _integer(length, 1, limit)
        data = body.read(limit + 1)
        if len(data) != length:
            raise ValueError("S3 object length differs from the bounded response")
        return data
    finally:
        body.close()


def _json_object(data):
    try:
        value = json.loads(data)
        if type(value) is not dict or canonical(value) != data:
            raise ValueError("object is not canonical JSON")
        return value
    except (UnicodeDecodeError, RecursionError) as exc:
        raise ValueError("invalid canonical JSON") from exc


def _source(prefix, ns, batch_id, value):
    if type(value) is not dict:
        raise ValueError("verification receipt has no source binding")
    required = {
        "batch_id", "namespace_id", "run_id", "object_key", "version_id",
        "sha256", "manifest_key", "admission_key", "byte_offset",
        "byte_length", "record_bytes", "records",
    }
    if not required.issubset(value):
        raise ValueError("incomplete verified source binding")
    if value["batch_id"] != batch_id or value["namespace_id"] != ns.id:
        raise ValueError("verified source belongs to another batch or namespace")
    run = _uuid(value["run_id"])
    if (
        type(value["record_bytes"]) is not int
        or value["record_bytes"] != ns.record_bytes
    ):
        raise ValueError("source record width differs from namespace")
    count = _integer(value["records"], 1, MAX_BATCH_BYTES // ns.record_bytes)
    length = _integer(value["byte_length"], 1, MAX_BATCH_BYTES)
    offset = _integer(value["byte_offset"], 0, (1 << 63) - 1)
    if length != count * ns.record_bytes or offset % ns.record_bytes:
        raise ValueError("source record count, width or offset differs")
    digest = value["sha256"]
    if not isinstance(digest, str) or HEX.fullmatch(digest) is None:
        raise ValueError("invalid source checksum")
    expected_raw = (
        f"{prefix}/raw/{ns.id}/shard={digest[:2]}/runs/{run}/"
        f"{offset:020d}-{digest}.bin"
    )
    if value["object_key"] != expected_raw:
        raise ValueError("raw key differs from its namespace/run/offset/digest")
    version = value["version_id"]
    if (
        not isinstance(version, str) or not version or version == "null"
        or len(version.encode()) > 1024
    ):
        raise ValueError("raw source does not bind a version")
    _receipt_path(prefix, ns.id, "batches", batch_id, value["manifest_key"])
    _receipt_path(prefix, ns.id, "admissions", batch_id, value["admission_key"])
    return value


def _verification(prefix, ns, key, receipt):
    if type(receipt) is not dict:
        raise ValueError("verification receipt must be a JSON object")
    path = f"{prefix}/verified/{ns.id}/"
    if not isinstance(key, str) or not key.startswith(path):
        raise ValueError("verification belongs to another archive or namespace")
    suffix = key[len(path):].split("/")
    if len(suffix) != 2:
        raise ValueError("invalid verification receipt path")
    batch_id = _uuid(suffix[0])
    digest = _receipt_path(prefix, ns.id, "verified", batch_id, key)
    raw = canonical(receipt)
    if (
        len(raw) > MAX_RECEIPT_BYTES
        or hashlib.sha256(raw).hexdigest() != digest
    ):
        raise ValueError("verification receipt differs from its content address")
    if (
        type(receipt.get("protocol")) is not int or receipt["protocol"] != 2
        or receipt.get("kind") != "batch-verification"
        or receipt.get("state") != "verified"
    ):
        raise ValueError("receipt does not attest full batch verification")
    source = _source(prefix, ns, batch_id, receipt.get("source"))
    if (
        type(receipt.get("verified_records")) is not int
        or receipt["verified_records"] != source["records"]
    ):
        raise ValueError("verification does not cover every source record")
    checks = receipt.get("checks")
    if (
        not isinstance(checks, list) or len(checks) > 32
        or any(not isinstance(check, str) for check in checks)
        or len(set(checks)) != len(checks) or not CHECKS.issubset(checks)
    ):
        raise ValueError("required verification checks are absent")
    verifier = receipt.get("verifier_id")
    verifier_sha = receipt.get("verifier_sha256")
    if (
        not isinstance(verifier, str) or not 1 <= len(verifier) <= 128
        or not isinstance(verifier_sha, str)
        or HEX.fullmatch(verifier_sha) is None
        or type(receipt.get("trail_steps_verified")) is not bool
    ):
        raise ValueError("invalid verifier identity or work-accounting scope")
    return source


def load_verified_sources(
    archive, ns, verified_keys, max_input_bytes=MAX_INPUT_BYTES,
):
    """Validate every receipt and the total byte budget before reading raw data."""
    _integer(max_input_bytes, 1, MAX_INPUT_BYTES)
    if (
        not isinstance(verified_keys, (list, tuple))
        or not 1 <= len(verified_keys) <= MAX_RECEIPTS
    ):
        raise ValueError("expected one through 4096 verification receipt keys")
    if any(not isinstance(key, str) for key in verified_keys):
        raise ValueError("verification keys must be strings")
    keys = sorted(verified_keys)
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate verification receipt")
    sources, batches, total = [], set(), 0
    for key in keys:
        path = f"{archive.prefix}/verified/{ns.id}/"
        if not key.startswith(path):
            raise ValueError("verification belongs to another archive or namespace")
        suffix = key[len(path):].split("/")
        if len(suffix) != 2:
            raise ValueError("invalid verification receipt path")
        _receipt_path(
            archive.prefix, ns.id, "verified", _uuid(suffix[0]), key,
        )
        raw = _read(archive, key, MAX_RECEIPT_BYTES)
        receipt = _json_object(raw)
        source = _verification(archive.prefix, ns, key, receipt)
        batch_id = source["batch_id"]
        if batch_id in batches:
            raise ValueError("multiple verification receipts for the same batch")
        batches.add(batch_id)
        total += source["byte_length"]
        if total > max_input_bytes:
            raise ValueError("compaction/rebuild input byte budget exceeded")
        sources.append({"key": key, "receipt": receipt})
    return sources


def _raw_records(archive, ns, source):
    raw = _read(
        archive, source["object_key"], source["byte_length"],
        source["version_id"],
    )
    if hashlib.sha256(raw).hexdigest() != source["sha256"]:
        raise ValueError("version-pinned raw checksum differs from verification")
    records = ns.decode(raw)
    if len(records) != source["records"]:
        raise ValueError("decoded raw count differs from verification")
    return raw, records


def iter_verified_records(
    archive, ns, verified_keys, max_input_bytes=MAX_INPUT_BYTES,
):
    """Yield immutable-source bindings after checksum and canonical decoding."""
    sources = load_verified_sources(
        archive, ns, verified_keys, max_input_bytes,
    )
    for entry in sources:
        source = entry["receipt"]["source"]
        _, records = _raw_records(archive, ns, source)
        for index, record in enumerate(records):
            yield {
                "record": record,
                "record_index": index,
                "verified_key": entry["key"],
                "source": source,
            }


def _sort_key(row, record_bytes):
    return (
        row[PREFIX.size:record_bytes]
        + row[:PREFIX.size]
        + row[record_bytes:]
    )


def compact(archive, ns, verified_keys, max_input_bytes=MAX_INPUT_BYTES):
    """Write one deterministic segment and its receipt; preserve all raw inputs.

    Memory includes Python sorting overhead and the output buffer. Select a
    smaller input bound on a worker whose memory cannot hold the full sort.
    """
    sources = load_verified_sources(
        archive, ns, verified_keys, max_input_bytes,
    )
    rows, total = [], 0
    for entry in sources:
        source = entry["receipt"]["source"]
        raw, records = _raw_records(archive, ns, source)
        batch_bytes = UUID(source["batch_id"]).bytes
        total += len(raw)
        for index, _ in enumerate(records):
            start = index * ns.record_bytes
            rows.append(
                raw[start:start + ns.record_bytes]
                + batch_bytes
                + U32.pack(index)
            )
    rows.sort(key=lambda row: _sort_key(row, ns.record_bytes))
    header = {
        "protocol": 2,
        "kind": "dp-segment",
        "namespace_id": ns.id,
        "archive_prefix": archive.prefix,
        "record_bytes": ns.record_bytes,
        "row_bytes": ns.record_bytes + REF_BYTES,
        "records": len(rows),
        "input_bytes": total,
        "sort": SORT,
        "verified": sources,
    }
    encoded_header = canonical(header)
    if len(encoded_header) > MAX_HEADER_BYTES:
        raise ValueError("segment header exceeds its size bound")
    body = (
        MAGIC
        + U32.pack(len(encoded_header))
        + encoded_header
        + b"".join(rows)
    )
    digest = hashlib.sha256(body).hexdigest()
    key = f"{archive.prefix}/segments/{ns.id}/{digest}.bin"
    archive.storage.put_immutable(key, body)
    receipt = {
        "protocol": 2,
        "kind": "segment-compaction",
        "state": "compacted",
        "namespace_id": ns.id,
        "verified_keys": [entry["key"] for entry in sources],
        "input_bytes": total,
        "records": len(rows),
        "segment_key": key,
        "segment_sha256": digest,
        "segment_bytes": len(body),
        "raw_deleted": False,
    }
    receipt_key = archive.put_receipt(
        ns.id, "compaction", digest, receipt,
    )
    return {**receipt, "receipt_key": receipt_key}


def parse_segment(ns, body, archive_prefix, expected_sha256=None):
    """Validate a segment and its complete raw-source reconstruction.

    The returned header is trusted only relative to the supplied expected
    content digest and the archive's trusted verification receipt namespace.
    """
    if not isinstance(body, bytes):
        raise ValueError("segment input must be immutable bytes")
    bound = (
        MAX_INPUT_BYTES
        + (MAX_INPUT_BYTES // ns.record_bytes) * REF_BYTES
    )
    if len(body) > bound + MAX_HEADER_BYTES + len(MAGIC) + U32.size:
        raise ValueError("segment exceeds its maximum encoded size")
    if expected_sha256 is not None:
        if (
            not isinstance(expected_sha256, str)
            or HEX.fullmatch(expected_sha256) is None
            or hashlib.sha256(body).hexdigest() != expected_sha256
        ):
            raise ValueError("segment checksum differs from its content address")
    if len(body) < 12 or body[:8] != MAGIC:
        raise ValueError("invalid segment magic or truncated header")
    length = U32.unpack_from(body, 8)[0]
    if not 1 <= length <= MAX_HEADER_BYTES or 12 + length > len(body):
        raise ValueError("segment header length exceeds its bound")
    header = _json_object(body[12:12 + length])
    if (
        type(header.get("protocol")) is not int or header["protocol"] != 2
        or header.get("kind") != "dp-segment"
        or header.get("namespace_id") != ns.id
        or header.get("archive_prefix") != archive_prefix
        or header.get("sort") != SORT
        or type(header.get("record_bytes")) is not int
        or header["record_bytes"] != ns.record_bytes
        or type(header.get("row_bytes")) is not int
        or header["row_bytes"] != ns.record_bytes + REF_BYTES
    ):
        raise ValueError("segment namespace, codec, or ordering differs")
    entries = header.get("verified")
    if (
        not isinstance(entries, list)
        or not 1 <= len(entries) <= MAX_RECEIPTS
    ):
        raise ValueError("segment has an invalid verification source count")
    sources, keys, total, count = {}, [], 0, 0
    for entry in entries:
        if type(entry) is not dict or set(entry) != {"key", "receipt"}:
            raise ValueError("invalid segment source reference")
        source = _verification(
            archive_prefix, ns, entry["key"], entry["receipt"],
        )
        batch_id = source["batch_id"]
        if batch_id in sources:
            raise ValueError("segment repeats a source batch")
        sources[batch_id] = source
        keys.append(entry["key"])
        total += source["byte_length"]
        count += source["records"]
    if keys != sorted(set(keys)) or total > MAX_INPUT_BYTES:
        raise ValueError("source keys or total input bytes exceed the format bound")
    if (
        type(header.get("input_bytes")) is not int
        or header["input_bytes"] != total
        or type(header.get("records")) is not int
        or header["records"] != count
        or len(body) - 12 - length != count * header["row_bytes"]
    ):
        raise ValueError("segment row count, width, or input bytes differ")
    raw_batches = {
        batch: bytearray(source["byte_length"])
        for batch, source in sources.items()
    }
    seen = {
        batch: bytearray(source["records"])
        for batch, source in sources.items()
    }
    previous = None
    start = 12 + length
    width = header["row_bytes"]
    for offset in range(start, len(body), width):
        row = body[offset:offset + width]
        order = _sort_key(row, ns.record_bytes)
        if previous is not None and order < previous:
            raise ValueError("segment rows are not sorted")
        previous = order
        ns.decode(row[:ns.record_bytes])
        batch = str(UUID(
            bytes=row[ns.record_bytes:ns.record_bytes + 16],
        ))
        ordinal = U32.unpack_from(row, ns.record_bytes + 16)[0]
        source = sources.get(batch)
        if (
            source is None
            or ordinal >= source["records"]
            or seen[batch][ordinal]
        ):
            raise ValueError(
                "segment row has an unknown or repeated source location"
            )
        seen[batch][ordinal] = 1
        raw_offset = ordinal * ns.record_bytes
        raw_batches[batch][raw_offset:raw_offset + ns.record_bytes] = (
            row[:ns.record_bytes]
        )
    for batch, source in sources.items():
        if (
            not all(seen[batch])
            or hashlib.sha256(raw_batches[batch]).hexdigest()
            != source["sha256"]
        ):
            raise ValueError(
                "segment rows do not reconstruct their verified raw source"
            )
    return header
