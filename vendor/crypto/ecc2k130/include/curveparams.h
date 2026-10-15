// Adapters turning each generated namespace into a Cfg type for the templates.
//
// A Cfg names the field implementation to use (normal or polynomial basis) and
// the matching scalar reference, so the walk, the kernel and the solver are all
// written once and work for either representation.
#pragma once

#include "bitslice.h"
#include "fieldbs.h"
#include "fieldpb.h"
#include "ref.h"
#include "../generated/eccF131.h"
#include "../generated/eccF83.h"
#include "../generated/eccF41.h"
#include "../generated/eccF23.h"
#include "../generated/eccP97.h"
#include "../generated/eccP41.h"
#include "../generated/eccP19.h"
#include "../generated/eccP13.h"

// ---- type-II optimal normal basis ------------------------------------------
#define ECC_MAKE_CFG(NAME, NS)                                                        \
    struct NAME {                                                                     \
        static const int M = NS::M;                                                   \
        static const int NRING = NS::NRING;                                           \
        static const int HWBITS = NS::HWBITS;                                         \
        static const int LEAF = NS::LEAF;                                             \
        static const int PRODLEN = NS::PRODLEN;                                       \
        static const int DP_WEIGHT = NS::DP_WEIGHT;                                   \
        template <class W> static ECC_BIG void multPrep(const W *a, W *o) { NS::multPrep<W>(a, o); }   \
        template <class W> static ECC_BIG void toOnb(const W *h, W *o) { NS::toOnb<W>(h, o); }         \
        template <class W> static ECC_BIG void mulLeaf(const W *a, const W *b, W *o) { NS::mulLeaf<W>(a, b, o); } \
        template <class W> static ECC_BIG void hamming(const W *x, W *o) { NS::hamming<W>(x, o); }     \
        template <class W> using Field = FieldBs<NAME, W>;                             \
        typedef ScalarOnb<NAME> Scalar;                                                \
    };

// ---- polynomial basis, weight through one linear map into a normal basis ----
#define ECC_MAKE_CFG_PB(NAME, NS)                                                     \
    struct NAME {                                                                     \
        static const int M = NS::M;                                                   \
        static const int NRING = NS::NRING;                                           \
        static const int HWBITS = NS::HWBITS;                                         \
        static const int LEAF = NS::LEAF;                                             \
        static const int PRODLEN = NS::PRODLEN;                                       \
        static const int DP_WEIGHT = NS::DP_WEIGHT;                                   \
        static constexpr const int *PB_TAPS = NS::PB_TAPS;                            \
        static constexpr const unsigned long long (*NB_ROWS)[3] = NS::NB_ROWS;        \
        template <class W> static ECC_BIG void reduce(const W *h, W *o) { NS::reduce<W>(h, o); }       \
        template <class W> static ECC_BIG void sqr(const W *a, W *o) { NS::sqr<W>(a, o); }             \
        template <class W> static ECC_BIG void mulLeaf(const W *a, const W *b, W *o) { NS::mulLeaf<W>(a, b, o); } \
        template <class W> static ECC_BIG void hamming(const W *x, W *o) { NS::hamming<W>(x, o); }     \
        template <class W> using Field = FieldPb<NAME, W>;                             \
        typedef ScalarPb<NAME> Scalar;                                                 \
    };

ECC_MAKE_CFG(CfgF131, eccF131)
ECC_MAKE_CFG(CfgF83, eccF83)
ECC_MAKE_CFG(CfgF41, eccF41)
ECC_MAKE_CFG(CfgF23, eccF23)

ECC_MAKE_CFG_PB(CfgP97, eccP97)
ECC_MAKE_CFG_PB(CfgP41, eccP41)
ECC_MAKE_CFG_PB(CfgP19, eccP19)
ECC_MAKE_CFG_PB(CfgP13, eccP13)

ECC_DEFINE_LEAF(CfgF131, eccF131::LEAF)
ECC_DEFINE_LEAF(CfgF83, eccF83::LEAF)
ECC_DEFINE_LEAF(CfgF41, eccF41::LEAF)
ECC_DEFINE_LEAF(CfgF23, eccF23::LEAF)
ECC_DEFINE_LEAF(CfgP97, eccP97::LEAF)
ECC_DEFINE_LEAF(CfgP41, eccP41::LEAF)
ECC_DEFINE_LEAF(CfgP19, eccP19::LEAF)
ECC_DEFINE_LEAF(CfgP13, eccP13::LEAF)
