// Imported from github.com/aburan28/crypto ecc2k130/include/packedtablewalk.cuh
// (branch ecc2k130-l1-geometry-and-tag-denominator, b8ed2f5), reformatted to
// this repository's .clang-format.  The generated files (packedtransform131,
// packed*reduce131, packedgeneratedproduct131, packedsigma131) come from that
// repository's codegen/ and are not edited here.
// Device side of the table walk (tablewalk.h) for the packed GF(2^131) kernel.
//
// Per block, dynamic shared memory holds the walk's tables, filled by the host
// from the reference TableWalk into one flat word buffer in this order:
//
//   table[k][h]  sigma^k(T_h) in the polynomial basis, 9 words: x words 0-3,
//                y words 0-3, then the two 3-bit top words packed as x | y << 3
//   maskLt[k]    normal-basis coordinates e with L(e) < k, 5 words
//   fromRow[p]   row p of the polynomial->normal transform, 5 words: the
//                normal-basis coordinate p of y is the parity of y_p & fromRow[p]
//   inv[w]       w^-1 mod 131, one word each
//   phase[i][v]  bytes: sum of L over the set bits of byte i of x when that
//                byte is v, mod 131   (17 x 256)
//   maxL[i][v]   bytes: 1 + the largest L over the set bits of nibble i of x
//                when that nibble is v, 0 when v = 0   (33 x 16)
//   linv[l]      bytes: the coordinate (bit index) whose L is l   (131)
//
// The selection is table lookups on purpose.  Its first form was eight
// bit-plane masked popcounts for the phase and an eight-round greedy argmax for
// the pivot; benchmarks/clmad-price prices POPC and FLO at 3.97 LOP3 slots on
// sm_120, so that form cost ~430 slot-equivalents per update on the pipe the
// walk is bound by.  Byte and nibble tables move most of it to the shared-
// memory pipe, which the walk barely uses: a 16-byte nibble table spans four
// banks, so its lookups are conflict-free, and the 17 byte lookups of the
// phase cost ~3.5 wavefronts each.  The whole buffer must stay under 48 KB so
// two blocks fit an SM, which is why the table entries are packed to 9 words.
#pragma once
#include "tablewalk.h"
#include "packed131.h"

// ECC_TABLE_PIVOT_BYTES=1 looks the pivot up per BYTE of x instead of per
// nibble: 17 lookups and 16 maxes in place of 33 and 32.  THROUGHPUT-20B.md
// prices the nibble scan at ~110 ALU slots per update, a third of the whole
// selection, mostly because IMNMX issues at 1.81 slots and PRMT at one, 33
// times over.  The byte table is 4,352 bytes against the nibble table's 528,
// which does not fit under the 48 KB two-blocks-per-SM line beside the other
// tables -- so this layout also packs the 3-bit top words: the table's x|y
// tops as one byte per entry (two words per k at H = 8 instead of one word per
// entry), and fromRow's top as a nibble (17 words instead of 131).  Net 48,732
// bytes.  Off by default; unmeasured on a card.  The selection primitives
// compile for the host too (TW_FN) so src/testtablewalkhost.cpp can hold both
// layouts to the reference without a GPU.
#ifndef ECC_TABLE_PIVOT_BYTES
#    define ECC_TABLE_PIVOT_BYTES 0
#endif
#if ECC_TABLE_PIVOT_BYTES != 0 && ECC_TABLE_PIVOT_BYTES != 1
#    error "ECC_TABLE_PIVOT_BYTES must be 0 or 1"
#endif
#ifndef ECC_TABLE_GLOBAL
#    define ECC_TABLE_GLOBAL 0
#endif
#if ECC_TABLE_GLOBAL != 0 && ECC_TABLE_GLOBAL != 1
#    error "ECC_TABLE_GLOBAL must be 0 or 1"
#endif
#if ECC_TABLE_GLOBAL && !ECC_WALK_TABLE
#    error "ECC_TABLE_GLOBAL requires ECC_WALK_TABLE"
#endif
#ifndef ECC_TABLE_ADDEND_GLOBAL
#    define ECC_TABLE_ADDEND_GLOBAL 0
#endif
#if ECC_TABLE_ADDEND_GLOBAL != 0 && ECC_TABLE_ADDEND_GLOBAL != 1
#    error "ECC_TABLE_ADDEND_GLOBAL must be 0 or 1"
#endif
#if ECC_TABLE_ADDEND_GLOBAL && !ECC_WALK_TABLE
#    error "ECC_TABLE_ADDEND_GLOBAL requires ECC_WALK_TABLE"
#endif
#if ECC_TABLE_ADDEND_GLOBAL && ECC_TABLE_GLOBAL
#    error "ECC_TABLE_ADDEND_GLOBAL is the hybrid smem path; do not combine with ECC_TABLE_GLOBAL"
#endif
// ECC_TABLE_TAG_DENOM=1: the reverse pass rebuilds d = x + x_T from the step
// tag instead of reading the denominator the forward pass stored.  The tag is
// already in memory -- twSelect pushes it into the low 16 bits of hist, which
// pass 2 has to read anyway to stay a class function -- so the denominator
// field (17 bytes stored in pass 1, 17 loaded in pass 2) disappears from the
// per-update traffic, and the persisting blob shrinks from x/y/pchain/denom to
// x/y/pchain, which fits this SKU's 80 MiB persisting-L2 window.  The price is
// one hist load and one 5-word table read per update in the reverse pass.
#ifndef ECC_TABLE_TAG_DENOM
#    define ECC_TABLE_TAG_DENOM 0
#endif
#if ECC_TABLE_TAG_DENOM != 0 && ECC_TABLE_TAG_DENOM != 1
#    error "ECC_TABLE_TAG_DENOM must be 0 or 1"
#endif
#if ECC_TABLE_TAG_DENOM && !ECC_WALK_TABLE
#    error "ECC_TABLE_TAG_DENOM requires ECC_WALK_TABLE"
#endif

namespace eccPacked131
{

static const int TW_H = ECC_TABLE_BRANCHES;
#if ECC_TABLE_PIVOT_BYTES
static const int TW_ENTRY = 8;                           // x words 0-3, y words 0-3
static const int TW_KWORDS = TW_H * TW_ENTRY + TW_H / 4; // + tops, 8 bits per entry
static const int TW_TABLE_WORDS = 131 * TW_KWORDS;
static const int TW_MASK_OFF = TW_TABLE_WORDS;
static const int TW_ROW_OFF = TW_MASK_OFF + 131 * 5;   // 131 x 4 words
static const int TW_ROWTOP_OFF = TW_ROW_OFF + 131 * 4; // 17 words, 4 bits per row
static const int TW_INV_OFF = TW_ROWTOP_OFF + 17;
static const int TW_PHASE_OFF = TW_INV_OFF + 132;     // 17 * 256 bytes
static const int TW_MAX_OFF = TW_PHASE_OFF + 17 * 64; // 17 * 256 bytes
static const int TW_LINV_OFF = TW_MAX_OFF + 17 * 64;  // 131 bytes, padded
#else
static const int TW_ENTRY = 9;
static const int TW_TABLE_WORDS = 131 * TW_H * TW_ENTRY;
static const int TW_MASK_OFF = TW_TABLE_WORDS;
static const int TW_ROW_OFF = TW_MASK_OFF + 131 * 5;
static const int TW_INV_OFF = TW_ROW_OFF + 131 * 5;
static const int TW_PHASE_OFF = TW_INV_OFF + 132;     // 17 * 256 bytes
static const int TW_MAX_OFF = TW_PHASE_OFF + 17 * 64; // 33 * 16 bytes
static const int TW_LINV_OFF = TW_MAX_OFF + 33 * 4;   // 131 bytes, padded
#endif
static const int TW_WORDS = TW_LINV_OFF + 33;
// Hybrid occupancy path: keep phase/pivot/sign tables in shared memory and
// read the bulky addend table from global.  Selection is 14 148 bytes, which
// fits three (and four) blocks in this SKU's 100 KB/SM; the full buffer does
// not.  Offsets in the selection copy are relative to TW_MASK_OFF.
static const int TW_SEL_WORDS = TW_WORDS - TW_MASK_OFF;
static const int TW_SEL0 = ECC_TABLE_ADDEND_GLOBAL ? TW_MASK_OFF : 0;
static const size_t TW_SHARED_BYTES =
    size_t(ECC_TABLE_ADDEND_GLOBAL ? TW_SEL_WORDS : TW_WORDS) * sizeof(uint32_t);
static_assert(TW_SHARED_BYTES <= 48 * 1024,
              "table walk tables must leave room for two blocks per SM");
static_assert(!ECC_TABLE_ADDEND_GLOBAL || TW_SHARED_BYTES <= 33 * 1024,
              "selection tables must fit three blocks in a 100 KB SM");

#ifdef __CUDACC__
#    define TW_FN __device__ __forceinline__
__device__ __forceinline__ void twLoadShared(uint32_t *shared, const uint32_t *global,
                                             int words = TW_WORDS)
{
    for (int i = threadIdx.x; i < words; i += blockDim.x) shared[i] = global[i];
    __syncthreads();
}
// Byte t of a word into the low byte, zeros above: one PRMT.
__device__ __forceinline__ uint32_t twByte(uint32_t w, int t)
{
    return __byte_perm(w, 0u, 0x4440u | unsigned(t));
}
__device__ __forceinline__ unsigned twMax(unsigned a, unsigned b) { return max(a, b); }
__device__ __forceinline__ int twParity(uint32_t t) { return int(__popc(t) & 1u); }
#else
#    define TW_FN static inline
static inline uint32_t twByte(uint32_t w, int t) { return (w >> (8 * t)) & 0xFFu; }
static inline unsigned twMax(unsigned a, unsigned b) { return a > b ? a : b; }
static inline int twParity(uint32_t t) { return __builtin_popcount(t) & 1; }
#endif

// Frobenius phase k(x) = (sum_e L(e) x_e) * HW(x)^-1 mod 131 of a normal-basis x.
TW_FN int twPhase(const P131 &x, int hw, const uint8_t *phase, const uint32_t *inv)
{
    unsigned s = 0;
#pragma unroll
    for (int w = 0; w < 4; ++w)
#pragma unroll
        for (int t = 0; t < 4; ++t) s += phase[(4 * w + t) * 256 + twByte(x.v[w], t)];
    s += phase[16 * 256 + (x.v[4] & 0xFFu)];
    return int(((s % 131u) * inv[hw]) % 131u);
}

// Index of the support element whose L is last before k in cyclic order:
// among set bits with L < k if any, else among all set bits, the largest L.
// x is never zero for a subgroup point, so some lookup is non-zero.
TW_FN int twPivot(const P131 &x, int k, const uint32_t *maskLt, const uint8_t *maxL,
                  const uint8_t *linv)
{
    const uint32_t *m = maskLt + k * 5;
    uint32_t s[5];
#pragma unroll
    for (int i = 0; i < 5; ++i) s[i] = x.v[i] & m[i];
    const bool any = (s[0] | s[1] | s[2] | s[3] | s[4]) != 0;
#pragma unroll
    for (int i = 0; i < 5; ++i) s[i] = any ? s[i] : x.v[i];
    unsigned best = 0;
#if ECC_TABLE_PIVOT_BYTES
    // 1 + the largest L over the set bits of byte i of x, 17 bytes of x.
#    pragma unroll
    for (int w = 0; w < 4; ++w)
#    pragma unroll
        for (int t = 0; t < 4; ++t)
            best = twMax(best, unsigned(maxL[(4 * w + t) * 256 + twByte(s[w], t)]));
    best = twMax(best, unsigned(maxL[16 * 256 + (s[4] & 7u)]));
#else
#    pragma unroll
    for (int w = 0; w < 4; ++w) {
        const uint32_t lo = s[w] & 0x0F0F0F0Fu, hi = (s[w] >> 4) & 0x0F0F0F0Fu;
#    pragma unroll
        for (int t = 0; t < 4; ++t) {
            best = twMax(best, unsigned(maxL[(8 * w + 2 * t) * 16 + twByte(lo, t)]));
            best = twMax(best, unsigned(maxL[(8 * w + 2 * t + 1) * 16 + twByte(hi, t)]));
        }
    }
    best = twMax(best, unsigned(maxL[32 * 16 + (s[4] & 7u)]));
#endif
    return linv[best - 1];
}

// Coordinate p of the normal-basis image of a polynomial-basis y.
TW_FN int twCoordinate(const P131 &yp, int p, const uint32_t *fromRow)
{
#if ECC_TABLE_PIVOT_BYTES
    const uint32_t *r = fromRow + p * 4;
    const uint32_t top = (fromRow[(TW_ROWTOP_OFF - TW_ROW_OFF) + (p >> 3)] >> ((p & 7) * 4)) & 7u;
    const uint32_t t =
        (yp.v[0] & r[0]) ^ (yp.v[1] & r[1]) ^ (yp.v[2] & r[2]) ^ (yp.v[3] & r[3]) ^ (yp.v[4] & top);
#else
    const uint32_t *r = fromRow + p * 5;
    const uint32_t t = (yp.v[0] & r[0]) ^ (yp.v[1] & r[1]) ^ (yp.v[2] & r[2]) ^ (yp.v[3] & r[3]) ^
                       (yp.v[4] & r[4]);
#endif
    return twParity(t);
}

// The whole selection for one point: (h, k, eps) after the cycle rule, with
// the history advanced.  x is in the normal basis, yp in the polynomial basis.
// The history is passed by value and returned so a caller that already holds
// it (the fused schedule reads hist for the denominator tag) does not load it
// twice; twSelect below is the in-memory form.
TW_FN unsigned twSelectHist(const P131 &x, const P131 &yp, int hw, unsigned long long *hist,
                            const uint32_t *shared)
{
    const uint8_t *bytes = reinterpret_cast<const uint8_t *>(shared);
    const int k =
        twPhase(x, hw, bytes + 4 * (TW_PHASE_OFF - TW_SEL0), shared + (TW_INV_OFF - TW_SEL0));
    const int p = twPivot(x, k, shared + (TW_MASK_OFF - TW_SEL0),
                          bytes + 4 * (TW_MAX_OFF - TW_SEL0), bytes + 4 * (TW_LINV_OFF - TW_SEL0));
    const int eps = twCoordinate(yp, p, shared + (TW_ROW_OFF - TW_SEL0));
    int h = (hw >> 1) & (TW_H - 1);
    unsigned tag = eccTag(h, k, eps);
    const unsigned long long old = *hist;
    while (eccTagFruitless(tag, old)) {
        h = (h + 1) & (TW_H - 1);
        tag = eccTag(h, k, eps);
    }
    *hist = eccHistPush(old, tag);
    return tag;
}
TW_FN unsigned twSelect(const P131 &x, const P131 &yp, int hw, unsigned long long *hist,
                        const uint32_t *shared)
{
    unsigned long long h = *hist;
    const unsigned tag = twSelectHist(x, yp, hw, &h, shared);
    *hist = h;
    return tag;
}

// d = x + x_T and e = y + y_T (+ x_T when the table point is negated), in the
// polynomial basis, for the selected tag.
TW_FN void twAddend(unsigned tag, const P131 &xp, const P131 &yp, const uint32_t *shared, P131 *d,
                    P131 *e)
{
#if ECC_TABLE_PIVOT_BYTES
    const int h = eccTagH(tag);
    const uint32_t *kbase = shared + eccTagK(tag) * TW_KWORDS;
    const uint32_t *t = kbase + h * TW_ENTRY;
    const uint32_t top = (kbase[TW_H * TW_ENTRY + (h >> 2)] >> ((h & 3) * 8)) & 63u;
#else
    const uint32_t *t = shared + (eccTagK(tag) * TW_H + eccTagH(tag)) * TW_ENTRY;
    const uint32_t top = t[8];
#endif
    const uint32_t negMask = 0u - unsigned(eccTagEps(tag));
#pragma unroll
    for (int i = 0; i < 4; ++i) {
        const uint32_t tx = t[i];
        d->v[i] = xp.v[i] ^ tx;
        e->v[i] = yp.v[i] ^ t[4 + i] ^ (tx & negMask);
    }
    const uint32_t tx = top & 7u;
    d->v[4] = xp.v[4] ^ tx;
    e->v[4] = yp.v[4] ^ (top >> 3) ^ (tx & negMask);
}

// d = x + x_T alone, for the reverse pass under ECC_TABLE_TAG_DENOM: the same
// table entry twAddend reads, without the y half.
TW_FN void twDenominator(unsigned tag, const P131 &xp, const uint32_t *shared, P131 *d)
{
#if ECC_TABLE_PIVOT_BYTES
    const int h = eccTagH(tag);
    const uint32_t *kbase = shared + eccTagK(tag) * TW_KWORDS;
    const uint32_t *t = kbase + h * TW_ENTRY;
    const uint32_t top = (kbase[TW_H * TW_ENTRY + (h >> 2)] >> ((h & 3) * 8)) & 7u;
#else
    const uint32_t *t = shared + (eccTagK(tag) * TW_H + eccTagH(tag)) * TW_ENTRY;
    const uint32_t top = t[8] & 7u;
#endif
#pragma unroll
    for (int i = 0; i < 4; ++i) d->v[i] = xp.v[i] ^ t[i];
    d->v[4] = xp.v[4] ^ top;
}

// Host: fill the flat constant buffer from the reference walk.
#ifndef __METAL_VERSION__
template <class TW> inline void twFillConsts(const TW &walk, uint32_t *out)
{
    auto pack = [](const unsigned long long *v, uint32_t *w) {
        for (int i = 0; i < 5; ++i) w[i] = uint32_t(v[i / 2] >> (32 * (i & 1)));
    };
    for (int i = 0; i < TW_WORDS; ++i) out[i] = 0;
    for (int k = 0; k < 131; ++k)
        for (int h = 0; h < TW_H; ++h) {
            P131 x, y;
            pack(walk.table[h][k].x.v, x.v);
            pack(walk.table[h][k].y.v, y.v);
            x = toPolynomial131(x);
            y = toPolynomial131(y);
#    if ECC_TABLE_PIVOT_BYTES
            uint32_t *t = out + k * TW_KWORDS + h * TW_ENTRY;
            for (int i = 0; i < 4; ++i) {
                t[i] = x.v[i];
                t[4 + i] = y.v[i];
            }
            out[k * TW_KWORDS + TW_H * TW_ENTRY + (h >> 2)] |=
                ((x.v[4] & 7u) | ((y.v[4] & 7u) << 3)) << ((h & 3) * 8);
#    else
            uint32_t *t = out + (k * TW_H + h) * TW_ENTRY;
            for (int i = 0; i < 4; ++i) {
                t[i] = x.v[i];
                t[4 + i] = y.v[i];
            }
            t[8] = (x.v[4] & 7u) | ((y.v[4] & 7u) << 3);
#    endif
        }
    for (int k = 0; k < 131; ++k) pack(walk.consts.maskLt[k], out + TW_MASK_OFF + k * 5);
    for (int j = 0; j < 131; ++j) {
        P131 e = {{0, 0, 0, 0, 0}};
        e.v[j >> 5] = 1u << (j & 31);
        const P131 n = fromPolynomial131(e);
        for (int p = 0; p < 131; ++p)
            if ((n.v[p >> 5] >> (p & 31)) & 1u) {
#    if ECC_TABLE_PIVOT_BYTES
                if (j < 128)
                    out[TW_ROW_OFF + p * 4 + (j >> 5)] |= 1u << (j & 31);
                else
                    out[TW_ROWTOP_OFF + (p >> 3)] |= 1u << ((j - 128) + (p & 7) * 4);
#    else
                out[TW_ROW_OFF + p * 5 + (j >> 5)] |= 1u << (j & 31);
#    endif
            }
    }
    for (int w = 0; w < 132; ++w) out[TW_INV_OFF + w] = uint32_t(walk.consts.inv[w]);
    // Coordinate i (1-based in the reference) sits at bit i - 1.
    auto L = [&](int bit) { return bit < 131 ? walk.consts.L[bit + 1] : -1; };
    uint8_t *bytes = reinterpret_cast<uint8_t *>(out);
    uint8_t *phase = bytes + 4 * TW_PHASE_OFF, *maxL = bytes + 4 * TW_MAX_OFF,
            *linv = bytes + 4 * TW_LINV_OFF;
    for (int i = 0; i < 17; ++i)
        for (int v = 0; v < 256; ++v) {
            unsigned s = 0;
            for (int t = 0; t < 8; ++t)
                if ((v >> t) & 1) s += unsigned(L(8 * i + t) < 0 ? 0 : L(8 * i + t));
            phase[i * 256 + v] = uint8_t(s % 131u);
        }
#    if ECC_TABLE_PIVOT_BYTES
    for (int i = 0; i < 17; ++i)
        for (int v = 0; v < 256; ++v) {
            int best = -1;
            for (int t = 0; t < 8; ++t)
                if ((v >> t) & 1) best = L(8 * i + t) > best ? L(8 * i + t) : best;
            maxL[i * 256 + v] = uint8_t(best + 1);
        }
#    else
    for (int i = 0; i < 33; ++i)
        for (int v = 0; v < 16; ++v) {
            int best = -1;
            for (int t = 0; t < 4; ++t)
                if ((v >> t) & 1) best = L(4 * i + t) > best ? L(4 * i + t) : best;
            maxL[i * 16 + v] = uint8_t(best + 1);
        }
#    endif
    for (int bit = 0; bit < 131; ++bit) linv[L(bit)] = uint8_t(bit);
}
#endif // __METAL_VERSION__

} // namespace eccPacked131
