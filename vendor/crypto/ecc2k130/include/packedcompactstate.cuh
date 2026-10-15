// Compact field storage: 256 aligned 16-byte low records and a byte tail plane.
#pragma once
#if ECC_PACKED_COMPACT_STATE
#include <stddef.h>
#if defined(__CUDA_ARCH__)
#include <vector_types.h>
#else
#include <cstring>
#endif

namespace eccPacked131 {
static_assert(sizeof(unsigned) == 4, "compact field word counts require 32-bit unsigned");

// The tail domain is 0..63: coordinates/prefixes use 0..7, and denominators
// additionally carry three jump bits. Narrowing is not equivalent for an
// arbitrary 32-bit high word. Callers provide valid slot/tid bounds and ensure
// the allocation-size products fit size_t before allocating or indexing.
ECC_HD size_t compactPhysicalFieldWords(size_t threads) {
    const size_t tiles = threads / 256 + (threads % 256 != 0);
    return tiles * size_t(ECC_BATCH) * size_t(4352 / sizeof(unsigned));
}

ECC_HD size_t compactLowByteOffset(int slot, int tid) {
    const size_t tileSlot = (size_t(tid) / 256) * size_t(ECC_BATCH) + size_t(slot);
    return tileSlot * size_t(4352) + (size_t(tid) % 256) * size_t(16);
}

ECC_HD size_t compactTopByteOffset(int slot, int tid) {
    const size_t tileSlot = (size_t(tid) / 256) * size_t(ECC_BATCH) + size_t(slot);
    return tileSlot * size_t(4352) + size_t(4096) + size_t(tid) % 256;
}

ECC_HD P131 compactLoad131(const unsigned *storage, int slot, int tid) {
    const unsigned char *bytes = reinterpret_cast<const unsigned char *>(storage);
    P131 value;
#if defined(__CUDA_ARCH__)
    // CUDA allocation alignment and the 4352/16-byte strides align this view.
    const uint4 low = *reinterpret_cast<const uint4 *>(bytes + compactLowByteOffset(slot, tid));
    value.v[0] = low.x; value.v[1] = low.y;
    value.v[2] = low.z; value.v[3] = low.w;
#else
    // Host staging need not have uint4 alignment or uint4 object lifetimes.
    std::memcpy(value.v, bytes + compactLowByteOffset(slot, tid), 16);
#endif
    value.v[4] = bytes[compactTopByteOffset(slot, tid)];
    return value;
}

// Issue L2 prefetches for the 16-byte low record and the tail byte.  The
// walk is DRAM-bound (Nsight: ~65% DRAM vs ~35% ALU on the RTX PRO 6000
// table walk); starting the next slot's lines during the current product
// hides that latency.  Host is a no-op.
ECC_HD void compactPrefetch131(const unsigned *storage, int slot, int tid) {
#if defined(__CUDA_ARCH__)
    const unsigned char *bytes = reinterpret_cast<const unsigned char *>(storage);
    const unsigned char *low = bytes + compactLowByteOffset(slot, tid);
    const unsigned char *top = bytes + compactTopByteOffset(slot, tid);
    asm volatile("prefetch.global.L2 [%0];" :: "l"(low));
    asm volatile("prefetch.global.L2 [%0];" :: "l"(top));
#else
    (void)storage; (void)slot; (void)tid;
#endif
}

ECC_HD void compactStore131(unsigned *storage, int slot, int tid, P131 value) {
    unsigned char *bytes = reinterpret_cast<unsigned char *>(storage);
#if defined(__CUDA_ARCH__)
    const uint4 low = {value.v[0], value.v[1], value.v[2], value.v[3]};
    *reinterpret_cast<uint4 *>(bytes + compactLowByteOffset(slot, tid)) = low;
#else
    std::memcpy(bytes + compactLowByteOffset(slot, tid), value.v, 16);
#endif
    // Byte ownership avoids a read-modify-write race with neighboring tails.
    bytes[compactTopByteOffset(slot, tid)] = static_cast<unsigned char>(value.v[4]);
}
} // namespace eccPacked131
#endif
