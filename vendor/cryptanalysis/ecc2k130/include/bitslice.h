// bitslice.h - the function-attribute macros the packed GF(2^131) headers use.
//
// In the source repository (github.com/aburan28/crypto, ecc2k130/) this header
// also carries the bitsliced word types and three-input logic of the original
// bitsliced backend.  The packed kernel imported here uses none of that: only
// these attribute macros, which make the same source compile as host code
// (for the tests and the re-walk) and as device code.
#pragma once

#include <stdint.h>

#if defined(__CUDACC__) || defined(__CUDA_ARCH__)
#    define ECC_HD  __host__ __device__ __forceinline__
#    define ECC_DEV __device__ __forceinline__
// Routines big enough that one shared copy beats inlining.  The 20 B/s build
// overrides this for the products through ECC_PACKED_INLINE_POLY.
#    define ECC_BIG   __host__ __device__ __noinline__
#    define ECC_CONST __host__ __device__ constexpr
#else
#    define ECC_HD    inline
#    define ECC_DEV   inline
#    define ECC_BIG   inline
#    define ECC_CONST constexpr
#endif
