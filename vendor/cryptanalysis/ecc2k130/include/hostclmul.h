// hostclmul.h - the host's carry-less multiplier, where it has one.
//
// The packed kernel is built around one primitive, a 64 x 64 -> 128 carry-less
// multiply: clmad on the CUDA device.  Most hosts have the same instruction --
// PMULL on AArch64 (the NEON polynomial multiply behind vmull_p64, present on
// every Apple-silicon core) and PCLMULQDQ on x86-64 -- so the host paths of
// packed131.h's clmul64, clmadLo64 and spread32p take it when the compiler is
// allowed to emit it, and keep the masked-multiply software product when not.
// Both compute the same bits; src/hosttest.cpp holds whichever was built to a
// bit-serial reference and to the golden model, and `make test` builds both.
//
// Selection is at compile time, by what the target already enables: Apple
// clang targets a PMULL core by default, gcc and clang elsewhere want the
// native core (-mcpu=native on AArch64, -march=native on x86-64; the Makefile
// passes it) or the feature itself (+aes, -mpclmul).  -DECC_HOST_CLMUL=0 forces the software
// product, which is how the portable path stays tested on a PMULL host.
#pragma once

#include <stdint.h>

#ifndef ECC_HOST_CLMUL
// nvcc's host pass keeps the path the CUDA client was measured with.
#    if defined(__CUDACC__) || defined(__CUDA_ARCH__)
#        define ECC_HOST_CLMUL 0
#    elif defined(__aarch64__) && (defined(__ARM_FEATURE_AES) || defined(__ARM_FEATURE_PMULL))
#        define ECC_HOST_CLMUL 1
#    elif defined(__x86_64__) && defined(__PCLMUL__)
#        define ECC_HOST_CLMUL 1
#    else
#        define ECC_HOST_CLMUL 0
#    endif
#endif
#if ECC_HOST_CLMUL != 0 && ECC_HOST_CLMUL != 1
#    error "ECC_HOST_CLMUL must be 0 or 1"
#endif

#if ECC_HOST_CLMUL
#    if defined(__aarch64__)
#        include <arm_neon.h>
static inline void eccHostClmul64(uint64_t a, uint64_t b, uint64_t *lo, uint64_t *hi)
{
    const uint64x2_t p = vreinterpretq_u64_p128(vmull_p64((poly64_t)a, (poly64_t)b));
    *lo = vgetq_lane_u64(p, 0);
    *hi = vgetq_lane_u64(p, 1);
}
#    elif defined(__x86_64__)
#        include <immintrin.h>
static inline void eccHostClmul64(uint64_t a, uint64_t b, uint64_t *lo, uint64_t *hi)
{
    const __m128i p =
        _mm_clmulepi64_si128(_mm_cvtsi64_si128((long long)a), _mm_cvtsi64_si128((long long)b), 0);
    *lo = (uint64_t)_mm_cvtsi128_si64(p);
    *hi = (uint64_t)_mm_cvtsi128_si64(_mm_unpackhi_epi64(p, p));
}
#    else
#        error "ECC_HOST_CLMUL=1 needs an AArch64 (PMULL) or x86-64 (PCLMULQDQ) target"
#    endif
// The low half alone: degree-31 squares and the 3 x 64-bit top products fit it.
static inline uint64_t eccHostClmulLo64(uint64_t a, uint64_t b)
{
    uint64_t lo, hi;
    eccHostClmul64(a, b, &lo, &hi);
    return lo;
}
#endif
