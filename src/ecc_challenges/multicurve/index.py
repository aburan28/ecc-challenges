"""Conditional, immutable DynamoDB index of fully verified affine DP witnesses.

The caller must fully verify the batch and durably write its verified S3 receipt
before calling put(). This module checks canonical bindings; it does not repeat
the expensive scalar multiplication for each index write.

Configure the injected low-level DynamoDB client with total_max_attempts=1,
finite connect/read timeouts, and a connection pool sized for the caller's
bounded concurrency. This module owns the bounded retry loop.
"""

from __future__ import annotations

import hashlib
import random
import re
import time
from uuid import UUID

from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from .codec import MAX_BATCH_BYTES, Namespace, Record

HEX = re.compile(r"^[0-9a-f]{64}$")
INTEGER = re.compile(r"^(0|[1-9][0-9]*)$")

MAX_ATTEMPTS = 4
MAX_ITEM_BYTES = 1024

THROTTLED = frozenset({
    "ProvisionedThroughputExceededException",
    "RequestLimitExceeded",
    "Throttling",
    "ThrottlingException",
})
TRANSPORT_ERRORS = (
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)


class IndexBindingError(RuntimeError):
    """An existing index witness does not match its immutable binding."""


def _namespace_id(ns: Namespace) -> str:
    value = ns.id
    if not isinstance(value, str) or HEX.fullmatch(value) is None:
        raise ValueError("invalid namespace digest")
    return value


def _batch_id(value) -> str:
    return str(UUID(str(value)))


def _record_index(ns: Namespace, value) -> int:
    if type(value) is not int or not 0 <= value < MAX_BATCH_BYTES // ns.record_bytes:
        raise ValueError("record index is outside the batch bound")
    return value


def _verified_key(namespace_id: str, batch_id: str, value) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 1024:
        raise ValueError("invalid verified receipt key")
    parts = value.split("/")
    if (
        not value.startswith("public-gateway/")
        or any(part in {"", ".", ".."} for part in parts)
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("invalid verified receipt path")
    suffix = f"/verified/{namespace_id}/{batch_id}/"
    prefix, separator, name = value.rpartition(suffix)
    if (
        not separator
        or not prefix.endswith("/v2")
        or not name.endswith(".json")
        or HEX.fullmatch(name[:-5]) is None
    ):
        raise ValueError("verified receipt is bound to another namespace or batch")
    return value


def _witness(ns: Namespace, value) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise ValueError("witness must be binary")
    data = bytes(value)
    width = ns.scalar_bytes
    if len(data) != 2 * width:
        raise ValueError("witness width does not match namespace")
    a = int.from_bytes(data[:width], "big")
    b = int.from_bytes(data[width:], "big")
    if a >= ns.instance.n or b >= ns.instance.n:
        raise ValueError("noncanonical stored coefficient")
    return data


def _attribute(item: dict, name: str, kind: str):
    value = item.get(name)
    if not isinstance(value, dict) or set(value) != {kind}:
        raise ValueError(f"invalid stored attribute {name}")
    return value[kind]


def _prior(ns: Namespace, point: bytes, item: dict) -> dict:
    """Decode an AWS low-level AttributeValue item and verify its binding."""
    try:
        if not isinstance(item, dict):
            raise ValueError("conditional failure did not return the prior item")
        namespace_id = _namespace_id(ns)
        expected_pk = f"{namespace_id}#{point.hex()}"
        if _attribute(item, "v", "N") != "2":
            raise ValueError("unsupported stored index version")
        if _attribute(item, "pk", "S") != expected_pk:
            raise ValueError("stored point key differs from requested point")
        if _attribute(item, "namespace_id", "S") != namespace_id:
            raise ValueError("stored namespace differs from requested namespace")
        stored_point = _attribute(item, "point_key", "B")
        if not isinstance(stored_point, (bytes, bytearray, memoryview)):
            raise ValueError("stored point is not binary")
        if bytes(stored_point) != point:
            raise ValueError("stored full affine point differs")

        raw_batch = _attribute(item, "batch_id", "S")
        batch = _batch_id(raw_batch)
        if batch != raw_batch:
            raise ValueError("stored batch UUID is not canonical")

        raw_index = _attribute(item, "record_index", "N")
        if not isinstance(raw_index, str) or INTEGER.fullmatch(raw_index) is None:
            raise ValueError("stored record index is not canonical")
        index = _record_index(ns, int(raw_index))
        witness = _witness(ns, _attribute(item, "witness", "B"))
        verified = _verified_key(
            namespace_id, batch, _attribute(item, "verified_key", "S"),
        )
        return {
            "namespace_id": namespace_id,
            "point_key": point,
            "witness": witness,
            "batch_id": batch,
            "record_index": index,
            "verified_key": verified,
        }
    except (TypeError, ValueError, OverflowError) as exc:
        raise IndexBindingError(
            "existing exact point index item has an invalid binding"
        ) from exc


def _item_size(item: dict) -> int:
    """Conservative size bound for this flat string/binary/number item."""
    total = 0
    for name, attribute in item.items():
        total += len(name.encode("utf-8"))
        kind, value = next(iter(attribute.items()))
        if kind == "B":
            total += len(value)
        else:
            # Decimal text overestimates the small DynamoDB number encoding.
            total += len(value.encode("utf-8"))
    return total


class PointIndex:
    def __init__(self, client, table: str):
        if not isinstance(table, str) or not table:
            raise ValueError("DynamoDB point index table is required")
        self.client = client
        self.table = table

    def bind_generation(self, db) -> dict:
        table = self.client.describe_table(TableName=self.table)["Table"]
        name, arn, identity = (table.get(k) for k in ("TableName", "TableArn", "TableId"))
        if table.get("TableStatus") not in ("ACTIVE", "UPDATING"):
            raise RuntimeError("point index table is unavailable")
        if not isinstance(identity, str):
            raise RuntimeError("DynamoDB did not return an immutable TableId")
        try:
            parsed = UUID(identity)
        except (ValueError, TypeError, AttributeError) as exc:
            raise RuntimeError("point index TableId is invalid") from exc
        if str(parsed) != identity:
            raise RuntimeError("point index TableId is not canonical")
        if (not isinstance(name, str) or not isinstance(arn, str)
                or re.fullmatch(r"arn:[a-z0-9-]+:dynamodb:[a-z0-9-]+:[0-9]{12}:table/[A-Za-z0-9_.-]+", arn) is None
                or arn.rsplit("/", 1)[-1] != name or self.table not in (name, arn)):
            raise RuntimeError("point index table identity differs from configuration")
        with db.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (710130003,))
            prior = conn.execute("SELECT table_id,table_name,table_arn FROM ecc_public.dp_index_bindings WHERE singleton=true").fetchone()
            if prior is None:
                indexed = conn.execute("SELECT EXISTS(SELECT 1 FROM ecc_public.dp_batches WHERE state='indexed' OR indexed_at IS NOT NULL) AS present").fetchone()["present"]
                if indexed:
                    raise RuntimeError("index binding absent with indexed batches; explicit rebuild activation required")
                conn.execute("INSERT INTO ecc_public.dp_index_bindings(singleton,table_id,table_name,table_arn) VALUES(true,%s,%s,%s)", (parsed, name, arn))
            elif (str(prior["table_id"]) != identity or prior["table_name"] != name or prior["table_arn"] != arn):
                raise RuntimeError("point index generation changed; complete rebuild activation required")
        return {"table_id": identity, "table_name": name, "table_arn": arn}

    def put(
        self,
        ns: Namespace,
        record: Record,
        batch_id,
        index: int,
        verified_key: str,
    ) -> dict | None:
        """Preserve the first witness; return its binding on an existing point.

        None means the item was inserted. An existing item is returned even
        when it is the same witness, so callers can distinguish retries from
        collisions. Never replace the existing witness.
        """
        namespace_id = _namespace_id(ns)
        ns.check_record(record)
        point = ns.point_key(record)
        witness = _witness(ns, ns.witness(record))
        batch = _batch_id(batch_id)
        index = _record_index(ns, index)
        verified_key = _verified_key(namespace_id, batch, verified_key)
        item = {
            "pk": {"S": f"{namespace_id}#{point.hex()}"},
            "v": {"N": "2"},
            "namespace_id": {"S": namespace_id},
            "point_key": {"B": point},
            "witness": {"B": witness},
            "batch_id": {"S": batch},
            "record_index": {"N": str(index)},
            "verified_key": {"S": verified_key},
        }
        if _item_size(item) > MAX_ITEM_BYTES:
            raise ValueError("point index item exceeds the one-KiB item budget")

        for attempt in range(MAX_ATTEMPTS):
            try:
                self.client.put_item(
                    TableName=self.table,
                    Item=item,
                    ConditionExpression="attribute_not_exists(#pk)",
                    ExpressionAttributeNames={"#pk": "pk"},
                    ReturnValuesOnConditionCheckFailure="ALL_OLD",
                )
                return None
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code == "ConditionalCheckFailedException":
                    return _prior(ns, point, exc.response.get("Item"))
                if code not in THROTTLED or attempt == MAX_ATTEMPTS - 1:
                    raise
            except TRANSPORT_ERRORS:
                if attempt == MAX_ATTEMPTS - 1:
                    raise
            time.sleep(random.uniform(0.0, min(2.0, 0.1 * (2 ** attempt))))

        raise RuntimeError("unreachable retry state")


def collision_lead_id(ns: Namespace, record: Record, prior: dict) -> str | None:
    """Return a location-independent ID for a distinct coefficient pair.

    Identical witnesses are duplicate work and return None. The caller must
    persist the lead and its source locations before completing the batch.
    """
    namespace_id = _namespace_id(ns)
    ns.check_record(record)
    point = ns.point_key(record)
    current = _witness(ns, ns.witness(record))
    try:
        if prior["namespace_id"] != namespace_id:
            raise ValueError("prior witness belongs to another namespace")
        if prior["point_key"] != point:
            raise ValueError("prior witness belongs to another full point")
        previous = _witness(ns, prior["witness"])
    except (KeyError, TypeError, ValueError) as exc:
        raise IndexBindingError("collision prior binding is invalid") from exc

    if current == previous:
        return None
    first, second = sorted((current, previous))
    return hashlib.sha256(
        b"ecc-collision-lead-v2\x00"
        + bytes.fromhex(namespace_id)
        + point
        + first
        + second
    ).hexdigest()
