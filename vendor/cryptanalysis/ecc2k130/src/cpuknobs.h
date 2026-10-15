// cpuknobs.h - the packed kernel's configuration for a host CPU.
//
// The Makefile's DEFS are the knobs the CUDA client was measured with; every
// one of them selects between formulations that compute the same bits, so a
// host is free to choose differently, and two choices are worth making.  With
// a carry-less multiplier on the host (hostclmul.h: PMULL, PCLMULQDQ) the
// multiplier is no longer the scarce unit the device treats it as: the 3-bit
// top-word correction and the polynomial squaring both go to it
// (ECC_PACKED_TOP_CLMAD=1, ECC_PACKED_ALU_SQUARE=0).  Measured with the whole
// walk on these routines, one worker on an M4 Pro core: 127 ns an iteration
// with these two, 163 with the device's choices over the same PMULL, 261 on
// the software product.  Without a multiplier the same two knobs would route
// small products through the masked-multiply software clmul, so they stay on
// the ALU.  (The walk's step has since moved to src/f131.h; what runs on the
// packed routines now is the inversion, the start points and the tests.)
//
// Force-included (-include) into every translation unit of the CPU client and
// of its tests, so the arithmetic the tests hold to the golden model is the
// arithmetic the walker runs.  The table layout (ECC_TABLE_PIVOT_BYTES) is the
// CUDA client's, so all clients build one constant buffer from one HostTable.
#pragma once

#include "../include/hostclmul.h"

#define ECC_PACKED_CLMAD 1 // product131 + direct reduction; the generated product is slower here
#if ECC_HOST_CLMUL
#    define ECC_PACKED_TOP_CLMAD  1
#    define ECC_PACKED_ALU_SQUARE 0
#else
#    define ECC_PACKED_TOP_CLMAD  0
#    define ECC_PACKED_ALU_SQUARE 1
#endif

#define ECC_PACKED_SINGLE_PRODUCT    1
#define ECC_PACKED_BY_VALUE          1
#define ECC_PACKED_PERM_SIGMA        3
#define ECC_PACKED_UNROLL_INV        1
#define ECC_PACKED_DIRECT_REDUCE     1
#define ECC_PACKED_GENERATED_PRODUCT 1
#define ECC_PACKED_FROM_REDUCED      1
#define ECC_PACKED_PAIR_ILP          1
#define ECC_PACKED_INLINE_POLY       3
#define ECC_WALK_TABLE               1
#define ECC_TABLE_PIVOT_BYTES        1
