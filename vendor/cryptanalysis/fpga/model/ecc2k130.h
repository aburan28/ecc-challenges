/*
 * ecc2k130.h - the golden model for the ECC2K-130 rho core.
 *
 * This is the reference the RTL is checked against, the generator of its test
 * vectors, and the host-side twin that replays what the hardware reports.
 * One implementation of the arithmetic, used by all three, so that "the
 * hardware agrees with the model" and "the host agrees with the hardware"
 * cannot drift apart into two different claims.
 *
 * The curve is the Certicom ECC2K-130 challenge curve
 *
 *     E : y^2 + x y = x^3 + 1     over  F_{2^131}
 *
 * whose group order is 4 * n with n = 680564733841876926932320129493409985129
 * prime (~2^129).  That is derived rather than quoted: E_0(F_2) has 4 points,
 * so the Frobenius trace is t = -1, and the Koblitz recursion
 * s_m = t*s_{m-1} - 2*s_{m-2} gives #E(F_{2^131}) = 2^131 + 1 - s_131; see
 * README.md.
 *
 * FIELD REPRESENTATION.  F_{2^131} is carried in a type-II optimal normal
 * basis, which exists because 2*131+1 = 263 is prime and 2 has order 131
 * modulo 263, so 2 generates the quadratic residues.  With gamma a primitive
 * 263rd root of unity the basis is
 *
 *     beta_i = gamma^i + gamma^-i,   i = 1 .. 131
 *
 * and an element is a 131-bit coefficient vector.  Three properties make this
 * the right basis for the hardware, and all three are checked in the test:
 *
 *   - beta_i * beta_j = beta_{i+j} + beta_{i-j}, with indices folded by
 *     beta_{263-k} = beta_k and beta_0 = 0.  So a product is a cyclic
 *     convolution of two symmetric 263-vectors: regular, shift-and-xor, and
 *     exactly what a digit-serial multiplier does in hardware.
 *   - Squaring maps beta_i to beta_{2i mod 263}: a fixed permutation of the
 *     coefficients, which is free in hardware (it is wiring) and is why the
 *     Frobenius endomorphism this attack is built on costs nothing.
 *   - The normal-basis coefficient order is a permutation of this index
 *     order, so the Hamming weight is the same in both.  The iteration
 *     function and the distinguished-point test are defined on the
 *     normal-basis weight, and this is why they can be evaluated directly on
 *     the vector the hardware holds, with no conversion.
 *
 * THE WALK.  The iteration of Bailey et al., "Breaking ECC2K-130":
 *
 *     j = ((HW(x_R) >> 1) & 7) + 3 ;   R' = sigma^j(R) + R
 *
 * with sigma the Frobenius (x, y) -> (x^2, y^2).  A point is distinguished
 * when HW(x) <= 34.  Only x is reported: on a Koblitz curve -(x, y) =
 * (x, x + y), so x is already the negation-class invariant, and the
 * Frobenius class is normalised host-side by taking the minimum x over the
 * orbit (ec2k_orbit_min), which is cheap there and expensive in gates.
 *
 * No coefficients are tracked.  A walk is identified by its seed, and a
 * collision is resolved by replaying both walks from their seeds -- the same
 * trade every ECC2K-130 implementation makes, because tracking (a, b) would
 * double the state and the bandwidth for something needed once per campaign.
 */
#ifndef ECC2K130_H
#define ECC2K130_H

#include <stddef.h>
#include <stdint.h>

#define EC2K_M         131 /* field degree */
#define EC2K_N         263 /* 2m+1, the root of unity order */
#define EC2K_WORDS     3   /* 131 bits in three 64-bit words */
#define EC2K_DP_WEIGHT 34  /* distinguished when HW(x) <= this */
#define EC2K_J_MIN     3   /* j ranges over [3, 10] */
#define EC2K_J_COUNT   8

/* A field element: coefficient i (1-based) of beta_i lives in bit i-1. */
typedef struct ec2k_fe {
    uint64_t w[EC2K_WORDS];
} ec2k_fe;

/* An affine point; `inf` marks the point at infinity. */
typedef struct ec2k_pt {
    ec2k_fe x, y;
    int inf;
} ec2k_pt;

/* ---- field ------------------------------------------------------------- */

void ec2k_fe_zero(ec2k_fe *r);
void ec2k_fe_one(ec2k_fe *r);
int ec2k_fe_is_zero(const ec2k_fe *a);
int ec2k_fe_equal(const ec2k_fe *a, const ec2k_fe *b);
void ec2k_fe_add(ec2k_fe *r, const ec2k_fe *a, const ec2k_fe *b); /* xor */
void ec2k_fe_mul(ec2k_fe *r, const ec2k_fe *a, const ec2k_fe *b);
void ec2k_fe_sqr(ec2k_fe *r, const ec2k_fe *a);
/* r = a^(2^j), the j-th Frobenius power; a permutation, any j >= 0. */
void ec2k_fe_frob(ec2k_fe *r, const ec2k_fe *a, unsigned j);
/* Inverse by Itoh-Tsujii; a must be nonzero (returns 0 and zeroes r if not). */
int ec2k_fe_inv(ec2k_fe *r, const ec2k_fe *a);
unsigned ec2k_fe_weight(const ec2k_fe *a);
/* Little-endian bytes, 17 bytes (131 bits, top 5 bits zero). */
void ec2k_fe_to_bytes(uint8_t out[17], const ec2k_fe *a);
int ec2k_fe_from_bytes(ec2k_fe *r, const uint8_t in[17]);
/* Hex, most significant nibble first; 33 characters plus a terminator. */
void ec2k_fe_to_hex(char out[34], const ec2k_fe *a);
int ec2k_fe_from_hex(ec2k_fe *r, const char *hex);

/* ---- curve ------------------------------------------------------------- */

int ec2k_on_curve(const ec2k_pt *p);
void ec2k_pt_neg(ec2k_pt *r, const ec2k_pt *p);
void ec2k_pt_add(ec2k_pt *r, const ec2k_pt *p, const ec2k_pt *q);
void ec2k_pt_dbl(ec2k_pt *r, const ec2k_pt *p);
void ec2k_pt_mul(ec2k_pt *r, const ec2k_pt *p, const uint64_t *k, size_t words);
void ec2k_pt_frob(ec2k_pt *r, const ec2k_pt *p, unsigned j);
/* A point from a seed: deterministic, on the curve, in the order-n subgroup. */
void ec2k_point_from_seed(ec2k_pt *r, uint64_t seed);

/* ---- the walk ---------------------------------------------------------- */

/* j = ((HW(x) >> 1) & 7) + 3, the iteration's Frobenius power. */
unsigned ec2k_step_j(const ec2k_fe *x);
/* One iteration: R' = sigma^j(R) + R.  Returns 0 on the exceptional case
 * sigma^j(R) == +-R, where the sum is not an ordinary addition; the caller
 * restarts the walk, which is what the hardware does too. */
int ec2k_step(ec2k_pt *r, const ec2k_pt *p);
int ec2k_is_distinguished(const ec2k_fe *x);
/* The same test at a different cutoff.  At the campaign's weight 34 a point
 * turns up about once in 2^25 steps, which no simulation is going to see, so
 * the testbenches run the model and the RTL together at a relaxed weight.
 * The cutoff is a parameter in both, never a different rule. */
int ec2k_is_distinguished_w(const ec2k_fe *x, unsigned weight);
/* The Frobenius-orbit representative of x: the smallest of the 131 values
 * x^(2^k).  Host-side normalisation of a reported point. */
void ec2k_orbit_min(ec2k_fe *r, const ec2k_fe *x);

/* ---- distinguished-point records --------------------------------------- */

/* 32 bytes on the wire: 8-byte little-endian walk seed, then 17 bytes of x,
 * then 7 bytes of zero padding.  Fixed width and no framing, so a corpus is
 * a concatenation of records and a truncated file is a shorter one. */
#define EC2K_RECORD_BYTES 32

typedef struct ec2k_record {
    uint64_t seed;
    ec2k_fe x;
} ec2k_record;

void ec2k_record_encode(uint8_t out[EC2K_RECORD_BYTES], const ec2k_record *r);
int ec2k_record_decode(ec2k_record *r, const uint8_t in[EC2K_RECORD_BYTES]);

/* Walk a seed for at most `steps` iterations, calling `sink` for every
 * distinguished point.  Returns the number of steps actually taken; `*dps`
 * receives the count of points, `*restarts` the exceptional cases. */
typedef void (*ec2k_sink)(void *ctx, const ec2k_record *rec);
uint64_t ec2k_walk(uint64_t seed, uint64_t steps, ec2k_sink sink, void *ctx, uint64_t *dps,
                   uint64_t *restarts);

#endif /* ECC2K130_H */
