"""The existing ECC2K-130 sigma-walk wire format and cheap admission checks."""

from __future__ import annotations

import struct

CAMPAIGN = "ecc2k-130"
WALK_ID = "certicom-ecc2k130-frobenius-v1"
DP_WEIGHT = 32
DP_WEIGHTS = (30, 32, 34)
# Keep the original wire/checkpoint default for clients without a threshold.
RECOMMENDED_DP_WEIGHT = 34
RECORD_BYTES = 32
MAX_BATCH_BYTES = 1024 * 1024
MAX_CHECKPOINT_BYTES = 128 * 1024 * 1024
HEADER = struct.Struct("<8s6IQ")


def check_records(data: bytes, run_id: int, dp_weight: int = DP_WEIGHT) -> int:
    if type(dp_weight) is not int or dp_weight not in DP_WEIGHTS:
        raise ValueError("unsupported sigma reporting threshold")
    if not data or len(data) > MAX_BATCH_BYTES or len(data) % RECORD_BYTES:
        raise ValueError("DP upload must contain 1..32768 whole 32-byte records")
    for pos in range(0, len(data), RECORD_BYTES):
        seed = int.from_bytes(data[pos:pos + 8], "little")
        x = int.from_bytes(data[pos + 8:pos + 32], "little")
        if seed >> 48 != run_id:
            raise ValueError("record seed does not belong to the assigned run ID")
        if x >> 131 or x.bit_count() > dp_weight or x.bit_count() & 1:
            raise ValueError(f"record is not a canonical DP{dp_weight} orbit key")
    return len(data) // RECORD_BYTES


def check_checkpoint(head: bytes, run_id: int, expected_dp_weight: int = DP_WEIGHT) -> int:
    if type(expected_dp_weight) is not int or expected_dp_weight not in DP_WEIGHTS:
        raise ValueError("unsupported sigma reporting threshold")
    if len(head) < HEADER.size:
        raise ValueError("checkpoint header is truncated")
    magic, version, degree, threads, batch, lanes, saved_run_id, iterations = HEADER.unpack_from(head)
    if magic != b"ECC2K130" or degree != 131 or saved_run_id != run_id:
        raise ValueError("checkpoint identity does not match the assigned run")
    if version != 0x100 or not threads or not batch or lanes != 1:
        raise ValueError("unsupported checkpoint geometry")
    if threads % 256 or batch != 16 or threads * batch > 2**32:
        raise ValueError("checkpoint is not from the public sigma build")
    if len(head) < 104:
        raise ValueError("checkpoint binding is truncated")
    dp_weight, field_words, _max_iters = struct.unpack_from("<iIQ", head, 40)
    expected_words = ((threads + 255) // 256) * batch * 1088
    expected_size = 104 + 2 * field_words * 4 + threads * batch * 20
    if dp_weight != expected_dp_weight or field_words != expected_words or len(head) != expected_size:
        raise ValueError("checkpoint payload or campaign binding is incompatible")
    return iterations
