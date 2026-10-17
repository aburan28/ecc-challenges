"""Compact point index: 16-byte entries in sorted S3 parts, merged in generations.

An entry is fingerprint (8 bytes) || locator (8 bytes):

- fingerprint: the first 8 bytes of SHA-256(x||y), the digest the walk already
  uses for partitions and the DP predicate;
- locator: index_seq (40 bits) << 24 | record index (24 bits), big-endian,
  where index_seq is the number a batch receives when it is indexed.

Entries sort bytewise, so a part is ordered by fingerprint, then locator. Part
p holds the entries whose top 12 fingerprint bits equal p.

Indexing a verified batch only assigns its index_seq. A merge reads each
pending batch's raw upload (pinned by version and SHA-256), adds one entry per
distinct (point, witness), rewrites the parts that received entries and looks
up every new fingerprint. A fingerprint shared with another entry is resolved
from the full records: an equal point with another witness becomes a collision
lead through the pipeline, an equal witness is duplicate work (its entry is
dropped and the batch's counters move it to deduplicated) and a different point
is a fingerprint false positive. A generation commits by compare-and-swap
on dp_point_index, so two mergers cannot both advance one namespace.

Raw batches remain the authority. The index is derived: rebuild() discards it
and the next merges recreate it from every indexed batch.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
import hashlib
import logging
import re

from .codec import PREFIX

LOG = logging.getLogger(__name__)

FORMAT = "ecc-point-index-v1"
ENTRY_BYTES = 16
FINGERPRINT_BYTES = 8
PART_BITS = 12
MAX_SEQ = (1 << 40) - 1
MAX_RECORD_INDEX = (1 << 24) - 1
MAX_BATCHES_PER_MERGE = 256
PART_NAME = re.compile(r"^[0-9a-f]{3}$")
HEX = re.compile(r"^[0-9a-f]{64}$")


class ConcurrentMerge(RuntimeError):
    """Another merge advanced or reset this namespace first."""


def locator(seq: int, index: int) -> bytes:
    if not 1 <= seq <= MAX_SEQ or not 0 <= index <= MAX_RECORD_INDEX:
        raise ValueError("locator is outside its 40/24-bit bounds")
    return ((seq << 24) | index).to_bytes(8, "big")


def split_locator(entry: bytes) -> tuple[int, int]:
    value = int.from_bytes(entry[FINGERPRINT_BYTES:ENTRY_BYTES], "big")
    return value >> 24, value & MAX_RECORD_INDEX


def fingerprint(point_key: bytes) -> bytes:
    return hashlib.sha256(point_key).digest()[:FINGERPRINT_BYTES]


def part_of(entry: bytes) -> str:
    return f"{int.from_bytes(entry[:2], 'big') >> (16 - PART_BITS):03x}"


class CompactPointIndex:
    """S3 storage for parts. Parts live outside v2/, which forbids deletes."""

    mode = "compact"

    def __init__(self, archive):
        self.archive = archive
        base, _, version = archive.prefix.rpartition("/")
        if version != "v2":
            raise ValueError("archive prefix does not end in v2")
        self.prefix = f"{base}/pointindex"

    def part_key(self, namespace_id: str, part: str, digest: str) -> str:
        if not HEX.fullmatch(namespace_id) or not PART_NAME.fullmatch(part) or not HEX.fullmatch(digest):
            raise ValueError("invalid point index part identity")
        return f"{self.prefix}/{namespace_id}/parts/{part}-{digest}.bin"

    def write_part(self, namespace_id: str, part: str, body: bytes) -> dict:
        if not body or len(body) % ENTRY_BYTES:
            raise ValueError("a part holds whole entries")
        digest = hashlib.sha256(body).hexdigest()
        key = self.part_key(namespace_id, part, digest)
        self.archive.storage.put_immutable(key, body)
        return {"key": key, "sha256": digest, "entries": len(body) // ENTRY_BYTES}

    def read_part(self, namespace_id: str, part: str, info: dict) -> bytes:
        key = self.part_key(namespace_id, part, info["sha256"])
        if info.get("key") != key or type(info.get("entries")) is not int:
            raise RuntimeError("point index part metadata is not canonical")
        body = self.archive.storage.get(key)
        if len(body) != info["entries"] * ENTRY_BYTES or hashlib.sha256(body).hexdigest() != info["sha256"]:
            raise RuntimeError("point index part differs from its manifest")
        return body

    def delete_part(self, key: str) -> None:
        if not key.startswith(self.prefix + "/"):
            raise ValueError("refusing to delete outside the point index prefix")
        self.archive.s3.delete_object(Bucket=self.archive.bucket, Key=key)


@dataclass
class _Member:
    entry: bytes
    seq: int
    index: int
    batch: dict
    point_key: bytes
    witness: bytes
    record: object


class Merger:
    def __init__(self, pipeline, point_index: CompactPointIndex, *,
                 max_batches: int = MAX_BATCHES_PER_MERGE):
        if not 1 <= max_batches <= 4096:
            raise ValueError("invalid merge batch limit")
        self.pipeline = pipeline
        self.db = pipeline.db
        self.index = point_index
        self.max_batches = max_batches

    def pending_namespaces(self) -> list[str]:
        with self.db.connection() as conn:
            rows = conn.execute("""
                SELECT DISTINCT namespace_id FROM ecc_public.dp_batches
                WHERE state='indexed' AND index_seq IS NOT NULL AND index_generation IS NULL
                ORDER BY namespace_id
            """).fetchall()
        return [row["namespace_id"] for row in rows]

    def merge_all(self) -> list[dict]:
        """Merge every namespace until nothing is pending. Returns manifests."""
        results = []
        for namespace_id in self.pending_namespaces():
            while True:
                try:
                    result = self.merge(namespace_id)
                except ConcurrentMerge:
                    LOG.warning("point index merge for %s lost to another merger", namespace_id)
                    break
                if result is None:
                    break
                results.append(result)
                if result["merged"]["batches"] < self.max_batches:
                    break
        return results

    def _manifest(self, ns, current) -> dict | None:
        if current is None:
            return None
        manifest = self.pipeline._read_receipt(
            current["manifest_key"], "pointindex", ns.id, f"generation-{current['generation']}")
        if (manifest.get("kind") != "point-index-generation" or manifest.get("format") != FORMAT
                or manifest.get("namespace_id") != ns.id
                or manifest.get("generation") != current["generation"]
                or manifest.get("entries") != current["entries"]
                or type(manifest.get("parts")) is not dict):
            raise RuntimeError("point index manifest differs from its metadata")
        return manifest

    def merge(self, namespace_id: str) -> dict | None:
        with self.db.connection() as conn:
            current = conn.execute("""
                SELECT generation,manifest_key,entries FROM ecc_public.dp_point_index
                WHERE namespace_id=%s
            """, (namespace_id,)).fetchone()
            pending = conn.execute("""
                SELECT b.*,n.campaign_id,n.manifest AS namespace_manifest
                FROM ecc_public.dp_batches b
                JOIN ecc_public.dp_namespaces n ON n.id=b.namespace_id
                WHERE b.namespace_id=%s AND b.state='indexed'
                  AND b.index_seq IS NOT NULL AND b.index_generation IS NULL
                ORDER BY b.index_seq LIMIT %s
            """, (namespace_id, self.max_batches)).fetchall()
        if not pending:
            return None
        ns = self.pipeline._namespace(pending[0])
        generation = current["generation"] if current else 0
        old = self._manifest(ns, current)
        old_parts = dict(old["parts"]) if old else {}
        by_seq = {batch["index_seq"]: batch for batch in pending}

        fresh: dict[str, list[bytes]] = {}
        for batch in pending:
            for entry in self._entries(ns, batch):
                fresh.setdefault(part_of(entry), []).append(entry)

        parts = dict(old_parts)
        duplicates: dict[str, int] = {}
        leads: set[str] = set()
        groups = false_positive_groups = 0
        raw_cache: dict[int, bytes] = {}
        for part in sorted(fresh):
            entries = fresh[part]
            entries.sort()
            body = self.index.read_part(ns.id, part, old_parts[part]) if part in old_parts else b""
            merged = [body[i:i + ENTRY_BYTES] for i in range(0, len(body), ENTRY_BYTES)]
            merged.extend(entries)
            merged.sort()  # two sorted runs: timsort merges them in linear time
            # A part is a fingerprint range, so every match lies inside it.
            dropped: set[bytes] = set()
            done: set[bytes] = set()
            for entry in entries:
                fp = entry[:FINGERPRINT_BYTES]
                if fp in done:
                    continue
                done.add(fp)
                start = end = bisect_left(merged, fp)
                while end < len(merged) and merged[end][:FINGERPRINT_BYTES] == fp:
                    end += 1
                if end - start < 2:
                    continue
                groups += 1
                found = self._resolve_group(ns, merged[start:end], by_seq, raw_cache)
                false_positive_groups += found["false_positive"]
                leads.update(found["leads"])
                for member in found["duplicates"]:
                    dropped.add(member.entry)
                    key = str(member.batch["id"])
                    duplicates[key] = duplicates.get(key, 0) + 1
            if dropped:
                merged = [entry for entry in merged if entry not in dropped]
            parts[part] = self.index.write_part(ns.id, part, b"".join(merged))

        total = sum(info["entries"] for info in parts.values())
        ids = sorted(str(batch["id"]) for batch in pending)
        manifest = {
            "protocol": 2,
            "kind": "point-index-generation",
            "format": FORMAT,
            "namespace_id": ns.id,
            "generation": generation + 1,
            "previous": current["manifest_key"] if current else None,
            "entry": {
                "bytes": ENTRY_BYTES,
                "fingerprint": "sha256(x-field-bytes-BE||y-field-bytes-BE)[0:8]",
                "locator": "uint40 index_seq || uint24 record index, big-endian",
                "part": f"fingerprint top {PART_BITS} bits",
                "order": "bytewise ascending",
            },
            "parts": parts,
            "entries": total,
            "merged": {
                "batches": len(pending),
                "first_index_seq": pending[0]["index_seq"],
                "last_index_seq": pending[-1]["index_seq"],
                "batch_ids_sha256": hashlib.sha256("\n".join(ids).encode()).hexdigest(),
                "new_entries": sum(len(v) for v in fresh.values()) - sum(duplicates.values()),
            },
            "matches": {
                "fingerprint_groups": groups,
                "false_positive_groups": false_positive_groups,
                "duplicates": sum(duplicates.values()),
                "collision_leads": sorted(leads),
            },
        }
        key = self.pipeline.archive.put_receipt(
            ns.id, "pointindex", f"generation-{generation + 1}", manifest)
        self._commit(ns, pending, generation, key, total, duplicates)
        for part, info in old_parts.items():
            if parts[part]["key"] != info["key"]:
                try:
                    self.index.delete_part(info["key"])
                except Exception:
                    LOG.exception("superseded point index part kept: %s", info["key"])
        manifest["manifest_key"] = key
        return manifest

    def _resolve_group(self, ns, group: list[bytes], by_seq: dict, raw_cache: dict) -> dict:
        """Compare the full records behind one fingerprint. New entries that
        repeat an earlier witness are duplicates and leave the index."""
        by_point: dict[bytes, list[_Member]] = {}
        for entry in group:
            member = self._member(ns, entry, by_seq, raw_cache)
            by_point.setdefault(member.point_key, []).append(member)
        found = {"false_positive": len(by_point) > 1, "leads": set(), "duplicates": []}
        for members in by_point.values():
            # Members are in locator order. The prior is the earliest entry
            # already merged, else the earliest new one.
            earlier = [m for m in members if m.seq not in by_seq]
            prior = earlier[0] if earlier else members[0]
            for member in members:
                if member is prior or member.seq not in by_seq:
                    continue
                lead = self.pipeline._lead(member.batch, ns, member.record, member.index, {
                    "namespace_id": ns.id,
                    "point_key": prior.point_key,
                    "witness": prior.witness,
                    "batch_id": str(prior.batch["id"]),
                    "record_index": prior.index,
                    "verified_key": prior.batch["verified_key"],
                })
                if lead is None:
                    found["duplicates"].append(member)
                else:
                    found["leads"].add(lead)
        return found

    def _entries(self, ns, batch: dict) -> list[bytes]:
        seq = batch["index_seq"]
        if batch["record_bytes"] != ns.record_bytes or batch["records"] > MAX_RECORD_INDEX + 1:
            raise RuntimeError("indexed batch does not fit the locator format")
        raw = self.pipeline._raw(batch)
        width = ns.record_bytes
        if len(raw) != batch["records"] * width:
            raise RuntimeError("indexed batch length differs from its record count")
        point_end = PREFIX.size + 2 * ns.field_bytes
        seen = set()
        entries = []
        for index in range(batch["records"]):
            record = raw[index * width:(index + 1) * width]
            point_key, witness = record[PREFIX.size:point_end], record[point_end:]
            if (point_key, witness) in seen:
                continue  # counted as a within-batch duplicate at indexing
            seen.add((point_key, witness))
            entries.append(fingerprint(point_key) + locator(seq, index))
        return entries

    def _member(self, ns, entry: bytes, by_seq: dict, raw_cache: dict) -> _Member:
        seq, index = split_locator(entry)
        batch = by_seq.get(seq)
        if batch is None:
            with self.db.connection() as conn:
                batch = conn.execute("""
                    SELECT b.*,n.campaign_id,n.manifest AS namespace_manifest
                    FROM ecc_public.dp_batches b
                    JOIN ecc_public.dp_namespaces n ON n.id=b.namespace_id
                    WHERE b.index_seq=%s AND b.namespace_id=%s AND b.state='indexed'
                """, (seq, ns.id)).fetchone()
            if batch is None:
                raise RuntimeError("point index entry names no indexed batch")
        if seq not in raw_cache:
            raw_cache[seq] = self.pipeline._raw(batch)
        width = ns.record_bytes
        if index >= batch["records"]:
            raise RuntimeError("point index entry names a record past its batch")
        raw = raw_cache[seq][index * width:(index + 1) * width]
        record = ns.decode(raw)[0]
        if fingerprint(ns.point_key(record)) != entry[:FINGERPRINT_BYTES]:
            raise RuntimeError("point index entry differs from its record")
        return _Member(entry, seq, index, batch, ns.point_key(record), ns.witness(record), record)

    def _commit(self, ns, pending, generation, key, total, duplicates) -> None:
        with self.db.connection() as conn:
            row = conn.execute("""
                SELECT generation FROM ecc_public.dp_point_index
                WHERE namespace_id=%s FOR UPDATE
            """, (ns.id,)).fetchone()
            if (row["generation"] if row else 0) != generation:
                raise ConcurrentMerge("point index generation changed during merge")
            if row:
                conn.execute("""
                    UPDATE ecc_public.dp_point_index
                    SET generation=%s,manifest_key=%s,entries=%s,updated_at=clock_timestamp()
                    WHERE namespace_id=%s
                """, (generation + 1, key, total, ns.id))
            elif not conn.execute("""
                    INSERT INTO ecc_public.dp_point_index(namespace_id,generation,manifest_key,entries)
                    VALUES (%s,%s,%s,%s) ON CONFLICT(namespace_id) DO NOTHING RETURNING namespace_id
                """, (ns.id, generation + 1, key, total)).fetchone():
                raise ConcurrentMerge("another merge created this point index")
            ids = [batch["id"] for batch in pending]
            locked = {r["id"]: r for r in conn.execute("""
                SELECT id,index_seq,index_duplicates,pressure_shard FROM ecc_public.dp_batches
                WHERE id=ANY(%s) AND state='indexed' AND index_generation IS NULL
                FOR UPDATE
            """, (ids,)).fetchall()}
            if len(locked) != len(ids):
                raise ConcurrentMerge("a pending batch changed during merge")
            shards: dict[int, int] = {}
            for batch in pending:
                current = locked[batch["id"]]
                if current["index_seq"] != batch["index_seq"]:
                    raise ConcurrentMerge("a pending batch changed its index sequence")
                count = duplicates.get(str(batch["id"]), 0)
                delta = count - current["index_duplicates"]
                conn.execute("""
                    UPDATE ecc_public.dp_batches
                    SET index_generation=%s,index_duplicates=%s,
                        new_points=new_points-%s,deduplicated_points=deduplicated_points+%s
                    WHERE id=%s
                """, (generation + 1, count, delta, delta, batch["id"]))
                if delta:
                    shard = current["pressure_shard"]
                    shards[shard] = shards.get(shard, 0) + delta
            for shard, delta in shards.items():
                conn.execute("""
                    UPDATE ecc_public.dp_namespace_stats
                    SET unique_points=unique_points-%s,deduplicated_points=deduplicated_points+%s
                    WHERE namespace_id=%s AND shard=%s
                """, (delta, delta, ns.id, shard))

    def rebuild(self, namespace_id: str) -> int:
        """Discard the index; the next merges rebuild it from raw batches.
        Duplicate counts are re-derived, and adjusted only by the difference."""
        with self.db.connection() as conn:
            current = conn.execute("""
                SELECT generation,manifest_key,entries FROM ecc_public.dp_point_index
                WHERE namespace_id=%s FOR UPDATE
            """, (namespace_id,)).fetchone()
            conn.execute("DELETE FROM ecc_public.dp_point_index WHERE namespace_id=%s", (namespace_id,))
            reset = conn.execute("""
                UPDATE ecc_public.dp_batches SET index_generation=NULL
                WHERE namespace_id=%s AND index_generation IS NOT NULL
            """, (namespace_id,)).rowcount
        if current:
            try:
                with self.db.connection() as conn:
                    row = conn.execute("""
                        SELECT campaign_id,record_bytes,manifest AS namespace_manifest
                        FROM ecc_public.dp_namespaces WHERE id=%s
                    """, (namespace_id,)).fetchone()
                manifest = self._manifest(self.pipeline._namespace({**row, "namespace_id": namespace_id}), current)
                for info in manifest["parts"].values():
                    self.index.delete_part(info["key"])
            except Exception:
                LOG.exception("parts of the discarded point index were kept for %s", namespace_id)
        return reset

    def status(self, namespace_id: str) -> dict:
        with self.db.connection() as conn:
            current = conn.execute("""
                SELECT generation,manifest_key,entries,updated_at FROM ecc_public.dp_point_index
                WHERE namespace_id=%s
            """, (namespace_id,)).fetchone()
            pending = conn.execute("""
                SELECT count(*) AS batches,coalesce(sum(records),0) AS records
                FROM ecc_public.dp_batches
                WHERE namespace_id=%s AND state='indexed'
                  AND index_seq IS NOT NULL AND index_generation IS NULL
            """, (namespace_id,)).fetchone()
        return {
            "namespace_id": namespace_id,
            "generation": current["generation"] if current else 0,
            "entries": current["entries"] if current else 0,
            "manifest_key": current["manifest_key"] if current else None,
            "updated_at": current["updated_at"].isoformat() if current else None,
            "pending_batches": pending["batches"],
            "pending_records": int(pending["records"]),
        }
