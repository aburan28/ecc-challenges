// Full-word per-thread shared scratch for the fused sigma prefix and tagged
// denominator fields. Layout is [field][slot][word][thread], making a fixed
// field/slot/word warp access cover 32 adjacent banks. The production cache is
// deliberately ephemeral: a
// launch prologue initializes every cached slot before the reverse pass reads
// it, and the final pass has no successor that needs the scratch at exit.
#pragma once

#include <stddef.h>
#include "packed131.h"

namespace eccPacked131 {

enum SigmaFusedScratchField131 {
    SIGMA_FUSED_SCRATCH_CHAIN = 0,
    SIGMA_FUSED_SCRATCH_DENOMINATOR = 1,
    SIGMA_FUSED_SCRATCH_FIELDS = 2
};

ECC_HD size_t sigmaFusedScratchIndex131(int field, int slot, int word, int lane,
                                        int cachedSlots, int blockThreads) {
    return (((size_t(field) * cachedSlots + size_t(slot)) * 5 + size_t(word)) *
            blockThreads) + size_t(lane);
}

ECC_HD P131 sigmaFusedScratchLoad131(const unsigned *scratch, int field, int slot,
                                     int lane, int cachedSlots, int blockThreads) {
    P131 value;
    for (int word = 0; word < 5; ++word)
        value.v[word] = scratch[sigmaFusedScratchIndex131(
            field, slot, word, lane, cachedSlots, blockThreads)];
    return value;
}

ECC_HD void sigmaFusedScratchStore131(unsigned *scratch, int field, int slot,
                                      int lane, int cachedSlots, int blockThreads,
                                      P131 value) {
    for (int word = 0; word < 5; ++word)
        scratch[sigmaFusedScratchIndex131(
            field, slot, word, lane, cachedSlots, blockThreads)] = value.v[word];
}

} // namespace eccPacked131
