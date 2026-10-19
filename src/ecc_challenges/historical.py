"""One-way import of the original ECC2K-130 campaign's distinguished points.

The original campaign's S3 `dp/` corpus is authoritative; its RDS table is a
derived dashboard view that may lag. So points are exported from S3, not RDS,
and the public service never connects to the original account:

1. `export` runs with a read-only profile for the original account. It checks
   every object against its `.bin.json` commit marker and the sha256 in its
   name, checks every record is a canonical DP32 orbit key outside the public
   run range, and writes each object as a checksum-bound part with a sidecar.
2. `load` runs against the public database. Each part is re-hashed, inserted
   with `ON CONFLICT DO NOTHING`, and recorded in the same transaction, so a
   rerun skips finished parts and a crash repeats one part harmlessly. A point
   reached again from a different seed is a historical collision and is kept
   in `historical_conflicts`.
3. `backmatch` runs after the last load. Batches indexed before a point was
   imported never joined against it; this finds those public candidates and
   writes the campaign leads `index_one` would have written.

Record encodings match the original ingester (crypto ecc2k130/aws/dp_ingest.py):
a 32-byte record is `(seed, k0, k1, k2)` little-endian, `point_key` is bytes
8..32 as stored, and `walk_seed` is the seed big-endian with leading zero
bytes stripped -- the same bytes `queues._seed_bytes` compares against.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Iterator

from .protocol import CAMPAIGN, WALK_ID, DP_WEIGHT, DP_WEIGHTS, RECORD_BYTES

LEGACY_KEY_RE = re.compile(r"^dp/(slot-\d+)/(\d+)-(\d+)\.bin$")
ORBIT_KEY_RE = re.compile(r"^dp/(slot-\d+)/([0-9a-f]{32})-(\d+)-([0-9a-f]{64})\.bin$")
# cryptanalysis ecc2k130/runner/aws/worker.py: one content-addressed delta of
# whole records, <sha256 of body>-<start offset>-<end offset> in the dp file.
RUNNER_KEY_RE = re.compile(r"^dp/(slot-\d+)/([0-9a-f]{64})-(\d{16})-(\d{16})\.bin$")
MARKER_SUFFIX = ".bin.json"
# A WITNESS=1 client's 72-byte records; never decoded as 32-byte records.
DP_MAGIC_V2 = b"ECC2KDP2"
PUBLIC_RUN_MIN, PUBLIC_RUN_MAX = 50000, 65534
LOAD_CHUNK_RECORDS = 250_000


class SourceRejected(ValueError):
    """An object that must not be imported; the reason is reported, not hidden."""


def worker_id(key: str) -> str:
    """The identifier the original rows use, so reconcile can compare per worker."""
    return "dp-" + key[len("dp/"):].replace("/", "-")


def seed_bytes(seed: int) -> bytes:
    return seed.to_bytes(max(1, (seed.bit_length() + 7) // 8), "big")


def is_dp_object(key: str) -> bool:
    return bool(LEGACY_KEY_RE.match(key) or ORBIT_KEY_RE.match(key) or RUNNER_KEY_RE.match(key))


def record_problem(record: bytes) -> str | None:
    seed = int.from_bytes(record[:8], "little")
    x = int.from_bytes(record[8:], "little")
    if PUBLIC_RUN_MIN <= seed >> 48 <= PUBLIC_RUN_MAX:
        return "public_run_range"
    if x >> 131 or x.bit_count() & 1:
        return "not_orbit_key"
    # The fleet's early runs used DP weight 34. Those points are valid for the
    # original campaign, but a DP32 walker passes through them without
    # recording them, so they can never match a public point.
    if x.bit_count() > DP_WEIGHT:
        return "above_dp32_weight"
    return None


@dataclass
class CheckedObject:
    key: str
    body: bytes
    sha256: str
    records: int
    rejected: dict[str, int] = field(default_factory=dict)


def check_object(key: str, body: bytes, marker: bytes | None) -> CheckedObject:
    """Validate one source object. Bad records are dropped and counted; a bad object is refused."""
    if not is_dp_object(key):
        raise SourceRejected("unrecognised key shape")
    if body[:8] == DP_MAGIC_V2:
        raise SourceRejected("witness-format object")
    if len(body) % RECORD_BYTES:
        raise SourceRejected("length is not whole 32-byte records")
    digest = hashlib.sha256(body).hexdigest()
    orbit = ORBIT_KEY_RE.match(key)
    if orbit and orbit.group(4) != digest:
        raise SourceRejected("sha256 in key does not match body")
    runner = RUNNER_KEY_RE.match(key)
    if runner:
        if runner.group(2) != digest:
            raise SourceRejected("sha256 in key does not match body")
        if int(runner.group(4)) - int(runner.group(3)) != len(body):
            raise SourceRejected("offsets in key do not match body length")
    if marker is not None:
        meta = json.loads(marker)
        if meta.get("sha256") not in (None, digest):
            raise SourceRejected("commit marker sha256 does not match body")
        count = meta.get("records", meta.get("record_count"))
        if count is not None and int(count) != len(body) // RECORD_BYTES:
            raise SourceRejected("commit marker record count does not match body")
    kept, rejected = bytearray(), {}
    for pos in range(0, len(body), RECORD_BYTES):
        record = body[pos:pos + RECORD_BYTES]
        problem = record_problem(record)
        if problem:
            rejected[problem] = rejected.get(problem, 0) + 1
        else:
            kept += record
    kept = bytes(kept)
    return CheckedObject(key, kept, hashlib.sha256(kept).hexdigest(), len(kept) // RECORD_BYTES, rejected)


# -- sinks: where exported parts go. Local directory or an S3 prefix. ---------
#
# A part is named by the sha256 of its body and a sidecar by the sha256 of its
# source key, so two source objects with the same body keep separate sidecars,
# and an export resumes by listing sidecar names without reading any of them.

def source_id(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class DirectorySink:
    def __init__(self, root):
        from pathlib import Path
        self.root = Path(root)
        (self.root / "parts").mkdir(parents=True, exist_ok=True)

    def done_ids(self) -> set[str]:
        return {path.stem for path in (self.root / "parts").glob("*.json")}

    def sidecars(self) -> Iterator[dict]:
        for path in sorted((self.root / "parts").glob("*.json")):
            yield json.loads(path.read_text())

    def put(self, digest: str, body: bytes, sidecar: dict) -> None:
        part = self.root / "parts" / f"{digest}.bin"
        if not part.exists():
            part.write_bytes(body)
        # Sidecar last: a sidecar is the commit marker for its part.
        name = source_id(sidecar["source_key"])
        (self.root / "parts" / f"{name}.json").write_text(json.dumps(sidecar, sort_keys=True))

    def get(self, digest: str) -> bytes:
        return (self.root / "parts" / f"{digest}.bin").read_bytes()


class S3Sink:
    def __init__(self, s3, bucket: str, prefix: str):
        self.s3, self.bucket, self.prefix = s3, bucket, prefix.rstrip("/") + "/parts/"

    def _sidecar_keys(self) -> Iterator[str]:
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=self.prefix):
            for item in page.get("Contents", []):
                if item["Key"].endswith(".json"):
                    yield item["Key"]

    def done_ids(self) -> set[str]:
        return {key[len(self.prefix):-len(".json")] for key in self._sidecar_keys()}

    def sidecars(self) -> Iterator[dict]:
        for key in self._sidecar_keys():
            yield json.loads(self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read())

    def _put_once(self, **kwargs) -> None:
        # The archive bucket refuses any PutObject without If-None-Match: *.
        # Parts are content-addressed and sidecars are keyed by source, so an
        # object that already exists is the one this call would have written.
        from botocore.exceptions import ClientError
        try:
            self.s3.put_object(Bucket=self.bucket, IfNoneMatch="*", ServerSideEncryption="AES256", **kwargs)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in ("PreconditionFailed", "ConditionalRequestConflict"):
                raise

    def put(self, digest: str, body: bytes, sidecar: dict) -> None:
        import base64
        sha = base64.b64encode(bytes.fromhex(digest)).decode()
        self._put_once(Key=f"{self.prefix}{digest}.bin", Body=body, ChecksumSHA256=sha)
        self._put_once(Key=f"{self.prefix}{source_id(sidecar['source_key'])}.json",
                       Body=json.dumps(sidecar, sort_keys=True).encode(), ContentType="application/json")

    def get(self, digest: str) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=f"{self.prefix}{digest}.bin")["Body"].read()


def open_sink(target: str, s3=None):
    if target.startswith("s3://"):
        bucket, _, prefix = target[5:].partition("/")
        return S3Sink(s3, bucket, prefix)
    return DirectorySink(target)


# -- export ------------------------------------------------------------------

def source_objects(s3, bucket: str) -> Iterator[dict]:
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="dp/"):
        yield from page.get("Contents", [])


def export(s3, bucket: str, sink, *, write: bool = True, limit: int | None = None,
           source_dp_weight: int | None = None, log=print) -> dict:
    """Copy and check the source corpus. With write=False nothing is written anywhere."""
    if source_dp_weight is not None and source_dp_weight not in DP_WEIGHTS:
        raise ValueError("unsupported source sigma threshold")
    done = sink.done_ids() if write else set()
    summary = {"objects": 0, "skipped_done": 0, "records": 0, "refused": {}, "rejected_records": {},
               "ignored_keys": 0}
    for item in source_objects(s3, bucket):
        key = item["Key"]
        if key.endswith(MARKER_SUFFIX):
            continue
        if not is_dp_object(key):
            summary["ignored_keys"] += 1
            log(f"ignored {key}: unrecognised key shape")
            continue
        if source_id(key) in done:
            summary["skipped_done"] += 1
            continue
        if limit is not None and summary["objects"] >= limit:
            break
        got = s3.get_object(Bucket=bucket, Key=key)
        body = got["Body"].read()
        marker = None
        try:
            marker = s3.get_object(Bucket=bucket, Key=key[:-len(".bin")] + MARKER_SUFFIX)["Body"].read()
        except s3.exceptions.NoSuchKey:
            pass  # legacy keys predate markers
        try:
            checked = check_object(key, body, marker)
        except SourceRejected as exc:
            summary["refused"][str(exc)] = summary["refused"].get(str(exc), 0) + 1
            log(f"refused {key}: {exc}")
            continue
        for reason, n in checked.rejected.items():
            summary["rejected_records"][reason] = summary["rejected_records"].get(reason, 0) + n
        summary["objects"] += 1
        summary["records"] += checked.records
        if write:
            sink.put(checked.sha256, checked.body, {
                "version": 1, "campaign_id": CAMPAIGN, "part_sha256": checked.sha256,
                "records": checked.records, "source_bucket": bucket, "source_key": key,
                "source_version": got.get("VersionId"), "source_etag": got.get("ETag"),
                "source_sha256": hashlib.sha256(body).hexdigest(), "source_bytes": len(body),
                "worker_id": worker_id(key), "rejected": checked.rejected,
                "walk_id": WALK_ID, "source_dp_weight": source_dp_weight,
            })
    return summary


# -- load ----------------------------------------------------------------------

def export_database(conn, sink, *, source_id: str, walk_id: str,
                    source_dp_weight: int | None = None, part_records: int = 250_000,
                    write: bool = True, log=print) -> dict:
    """Stream a consistent read-only RDS snapshot into the existing import format.

    Only the operator connects to the source. Original thresholds can be
    unknown; endpoint Hamming weight does not reveal the stopping rule.
    Content-addressed chunks and the destination point PK make reruns safe.
    """
    if walk_id != WALK_ID or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", source_id):
        raise ValueError("source must be an explicitly identified legacy sigma database")
    if source_dp_weight is not None and (type(source_dp_weight) is not int or source_dp_weight not in DP_WEIGHTS):
        raise ValueError("unsupported source sigma threshold")
    if not 1 <= part_records <= 1_000_000 or (write and sink is None):
        raise ValueError("invalid database export sink or part size")
    summary = {"scanned": 0, "records": 0, "parts": 0, "skipped_done": 0, "rejected_records": {}}
    done = sink.done_ids() if write else set()
    body = bytearray()
    # Read-only transaction and named cursor keep the source unmodified and
    # memory bounded even when the table holds hundreds of millions of rows.
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '6h'")
        def flush():
            if not body:
                return
            raw = bytes(body)
            digest = hashlib.sha256(raw).hexdigest()
            key = f"rds/{source_id}/{CAMPAIGN}/{digest}.bin"
            summary["parts"] += 1
            if hashlib.sha256(key.encode()).hexdigest() in done:
                summary["skipped_done"] += 1
            elif write:
                sink.put(digest, raw, {"version": 1, "campaign_id": CAMPAIGN,
                    "walk_id": walk_id, "source_dp_weight": source_dp_weight,
                    "part_sha256": digest, "records": len(raw) // RECORD_BYTES,
                    "source_key": key, "source_database": source_id,
                    "source_sha256": digest, "source_bytes": len(raw),
                    "worker_id": f"rds-{source_id}-{digest}", "rejected": {}})
            log(f"database export: {summary['scanned']} scanned, {summary['records']} retained")
            body.clear()
        with conn.cursor(name="historical_export") as cursor:
            cursor.itersize = 10_000
            cursor.execute("SELECT point_key,walk_seed FROM public.distinguished_points "
                           "WHERE campaign_id=%s ORDER BY point_key", (CAMPAIGN,))
            for row in cursor:
                summary["scanned"] += 1
                point, seed = bytes(row["point_key"]), bytes(row["walk_seed"] or b"")
                problem = None
                if len(point) != 24 or not 1 <= len(seed) <= 8:
                    problem = "invalid_record_width"
                else:
                    record = int.from_bytes(seed, "big").to_bytes(8, "little") + point
                    problem = record_problem(record)
                    if source_dp_weight is not None and int.from_bytes(point, "little").bit_count() > source_dp_weight:
                        problem = "source_threshold_mismatch"
                if problem:
                    summary["rejected_records"][problem] = summary["rejected_records"].get(problem, 0) + 1
                    continue
                body.extend(record)
                summary["records"] += 1
                if len(body) >= part_records * RECORD_BYTES:
                    flush()
        flush()
    return summary

def _rows(body: bytes, start: int, stop: int) -> Iterator[tuple[int, bytes, bytes]]:
    for i in range(start, min(stop, len(body) // RECORD_BYTES)):
        row = body[RECORD_BYTES * i:RECORD_BYTES * (i + 1)]
        yield i, seed_bytes(int.from_bytes(row[:8], "little")), row[8:]


def already_loaded(conn, sidecar: dict) -> bool:
    previous = conn.execute("""
        SELECT part_sha256,records,walk_id,source_dp_weight
        FROM ecc_public.historical_import_parts WHERE source_key=%s
    """, (sidecar["source_key"],)).fetchone()
    if previous is None:
        return False
    if (previous["part_sha256"] != sidecar["part_sha256"] or previous["records"] != sidecar["records"]
            or previous["walk_id"] != sidecar.get("walk_id", WALK_ID)
            or previous["source_dp_weight"] != sidecar.get("source_dp_weight")):
        raise RuntimeError("loaded source key has different content or walk binding")
    return True


def load_part(conn, sidecar: dict, body: bytes) -> dict:
    """Insert one source object's part in one transaction; an object already in the ledger is skipped."""
    digest = sidecar["part_sha256"]
    if hashlib.sha256(body).hexdigest() != digest or len(body) != sidecar["records"] * RECORD_BYTES:
        raise RuntimeError(f"part {digest} does not match its sidecar")
    weight = sidecar.get("source_dp_weight")
    if sidecar.get("walk_id", WALK_ID) != WALK_ID or (weight is not None and
            (type(weight) is not int or weight not in DP_WEIGHTS)):
        raise RuntimeError("historical part uses an incompatible walk or threshold")
    for pos in range(0, len(body), RECORD_BYTES):
        record = body[pos:pos + RECORD_BYTES]
        if record_problem(record) or (weight is not None and int.from_bytes(record[8:], "little").bit_count() > weight):
            raise RuntimeError("historical part contains a record outside its sigma binding")
    with conn.transaction():
        conn.execute("SET LOCAL statement_timeout = '15min'")
        if already_loaded(conn, sidecar):
            return {"part": digest, "loaded": False}
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS historical_incoming"
                     "(idx bigint, walk_seed bytea, point_key bytea) ON COMMIT DELETE ROWS")
        inserted = conflicts = 0
        for start in range(0, sidecar["records"], LOAD_CHUNK_RECORDS):
            conn.execute("TRUNCATE historical_incoming")
            with conn.cursor().copy("COPY historical_incoming(idx,walk_seed,point_key) FROM STDIN") as copy:
                for row in _rows(body, start, start + LOAD_CHUNK_RECORDS):
                    copy.write_row(row)
            # Insert before lookup, as index_one does: the conflict query below
            # then sees this chunk's own first witness for every point.
            inserted += conn.execute("""
                WITH first AS (SELECT DISTINCT ON(point_key) point_key,walk_seed
                               FROM historical_incoming ORDER BY point_key,idx),
                ins AS (INSERT INTO public.distinguished_points(campaign_id,point_key,walk_seed,worker_id,source_dp_weight)
                        SELECT %s,point_key,walk_seed,%s,%s FROM first ORDER BY point_key
                        ON CONFLICT (campaign_id,point_key) DO NOTHING RETURNING 1)
                SELECT count(*) AS n FROM ins
            """, (CAMPAIGN, sidecar["worker_id"], weight)).fetchone()["n"]
            conflicts += conn.execute("""
                WITH c AS (INSERT INTO ecc_public.historical_conflicts(point_key,walk_seed,stored_seed,source_key)
                           SELECT DISTINCT i.point_key,i.walk_seed,d.walk_seed,%s
                           FROM historical_incoming i JOIN public.distinguished_points d
                             ON d.campaign_id=%s AND d.point_key=i.point_key
                           WHERE d.walk_seed IS DISTINCT FROM i.walk_seed
                           ON CONFLICT DO NOTHING RETURNING 1)
                SELECT count(*) AS n FROM c
            """, (sidecar["source_key"], CAMPAIGN)).fetchone()["n"]
        conn.execute("""
            INSERT INTO ecc_public.historical_import_parts
              (part_sha256,source_key,source_version,records,inserted,conflicts,source_dp_weight,walk_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, (digest, sidecar["source_key"], sidecar.get("source_version"), sidecar["records"],
              inserted, conflicts, weight, WALK_ID))
    return {"part": digest, "loaded": True, "records": sidecar["records"],
            "inserted": inserted, "conflicts": conflicts}


def load(conn, sink, log=print) -> dict:
    summary = {"parts": 0, "already_loaded": 0, "records": 0, "inserted": 0, "conflicts": 0}
    for sidecar in sink.sidecars():
        if sidecar.get("version") != 1 or sidecar.get("campaign_id") != CAMPAIGN:
            raise RuntimeError(f"unexpected sidecar {sidecar.get('part_sha256')}")
        # Completed transactions need no re-download of hundreds of millions
        # of archived records, but the source/content/threshold binding must agree.
        if already_loaded(conn, sidecar):
            summary["already_loaded"] += 1
            continue
        result = load_part(conn, sidecar, sink.get(sidecar["part_sha256"]))
        if not result["loaded"]:
            summary["already_loaded"] += 1
            continue
        summary["parts"] += 1
        for k in ("records", "inserted", "conflicts"):
            summary[k] += result[k]
        if result["conflicts"]:
            log(f"historical collision(s) in {sidecar['source_key']}: {result['conflicts']}")
    return summary


# -- backmatch -----------------------------------------------------------------

def backmatch(conn, storage, log=print) -> dict:
    """Write the campaign leads that batches indexed before the import missed. Idempotent."""
    with conn.transaction():
        return _backmatch(conn, storage, log)


def _backmatch(conn, storage, log) -> dict:
    matches = conn.execute("""
        SELECT c.point_key,c.first_seed,c.first_run,c.first_batch,d.walk_seed,d.worker_id,b.object_key
        FROM ecc_public.candidate_points c
        JOIN public.distinguished_points d ON d.campaign_id=c.campaign_id AND d.point_key=c.point_key
        JOIN ecc_public.batches b ON b.id=c.first_batch
        WHERE c.campaign_id=%s AND c.disqualified_at IS NULL
          AND NOT EXISTS (SELECT 1 FROM ecc_public.collision_leads l
                          WHERE l.batch_id=c.first_batch AND l.point_key=c.point_key
                            AND l.prior_source='campaign')
    """, (CAMPAIGN,)).fetchall()
    summary = {"matches": len(matches), "leads": 0, "same_seed": 0}
    bodies: dict[str, bytes] = {}
    for m in matches:
        seed = int(m["first_seed"])
        if bytes(m["walk_seed"]) == seed_bytes(seed):
            summary["same_seed"] += 1  # one walk reported twice, not a collision
            continue
        body = bodies.setdefault(m["object_key"], storage.get(m["object_key"]))
        wanted = seed.to_bytes(8, "little") + bytes(m["point_key"])
        index = next((i for i in range(len(body) // RECORD_BYTES)
                      if body[RECORD_BYTES * i:RECORD_BYTES * (i + 1)] == wanted), None)
        if index is None:
            raise RuntimeError(f"batch {m['first_batch']} no longer holds its candidate point")
        conn.execute("""
            INSERT INTO ecc_public.collision_leads
              (batch_id,record_index,run_id,seed,point_key,prior_source,prior_seed_bytes,prior_worker)
            VALUES (%s,%s,%s,%s,%s,'campaign',%s,%s)
        """, (m["first_batch"], index, m["first_run"], seed, bytes(m["point_key"]),
              bytes(m["walk_seed"]), m["worker_id"]))
        summary["leads"] += 1
        log(f"campaign lead: batch {m['first_batch']} record {index}")
    return summary


# -- reconcile -----------------------------------------------------------------

def worker_counts(conn) -> dict[str, int]:
    rows = conn.execute("SELECT worker_id, count(*) AS n FROM distinguished_points "
                        "WHERE campaign_id=%s GROUP BY 1", (CAMPAIGN,)).fetchall()
    return {r["worker_id"]: r["n"] for r in rows}


def reconcile(source: dict[str, int], imported: dict[str, int]) -> dict:
    """Compare per-worker counts. The source RDS is a lagging view, so a deficit there is expected."""
    missing = {w: n for w, n in source.items() if imported.get(w, 0) < n}
    extra = {w: n for w, n in imported.items() if n > source.get(w, 0)}
    return {"source_total": sum(source.values()), "imported_total": sum(imported.values()),
            "workers_short": len(missing), "workers_ahead_of_source": len(extra),
            "short": dict(sorted(missing.items())[:50])}
