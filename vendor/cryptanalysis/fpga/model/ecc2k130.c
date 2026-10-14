/*
 * ecc2k130.c - the golden model.  See ecc2k130.h for the mathematics.
 *
 * Written for clarity and for agreement with the hardware, not for speed:
 * every routine here has a counterpart in rtl/ and the testbenches compare
 * them value by value.  Where a choice exists, this file makes the one the
 * hardware makes -- the multiplier really is a shift-and-xor convolution
 * because that is what the digit-serial unit computes, one digit at a time.
 */
#include "ecc2k130.h"

#include <string.h>

/* ---- small helpers ------------------------------------------------------ */

#define TOP_BITS (EC2K_M - 128) /* bits used in the last word: 3 */

static const uint64_t TOP_MASK = ((uint64_t)1 << TOP_BITS) - 1;

static inline void fe_trim(ec2k_fe *a) { a->w[2] &= TOP_MASK; }

static inline int fe_bit(const ec2k_fe *a, unsigned i)
{
    return (int)((a->w[i >> 6] >> (i & 63)) & 1);
}

static inline void fe_setbit(ec2k_fe *a, unsigned i) { a->w[i >> 6] |= (uint64_t)1 << (i & 63); }

void ec2k_fe_zero(ec2k_fe *r) { memset(r, 0, sizeof(*r)); }

/* 1 is the all-ones vector: sum_{i!=0} gamma^i = -1 = 1 in characteristic 2,
 * and that sum is exactly sum_i beta_i. */
void ec2k_fe_one(ec2k_fe *r)
{
    r->w[0] = ~(uint64_t)0;
    r->w[1] = ~(uint64_t)0;
    r->w[2] = TOP_MASK;
}

int ec2k_fe_is_zero(const ec2k_fe *a) { return (a->w[0] | a->w[1] | a->w[2]) == 0; }

int ec2k_fe_equal(const ec2k_fe *a, const ec2k_fe *b)
{
    return a->w[0] == b->w[0] && a->w[1] == b->w[1] && a->w[2] == b->w[2];
}

void ec2k_fe_add(ec2k_fe *r, const ec2k_fe *a, const ec2k_fe *b)
{
    r->w[0] = a->w[0] ^ b->w[0];
    r->w[1] = a->w[1] ^ b->w[1];
    r->w[2] = a->w[2] ^ b->w[2];
}

unsigned ec2k_fe_weight(const ec2k_fe *a)
{
    unsigned n = 0;
    for (int i = 0; i < EC2K_WORDS; i++) {
        uint64_t w = a->w[i];
        while (w) {
            w &= w - 1;
            n++;
        }
    }
    return n;
}

/* ---- multiplication ----------------------------------------------------- */

/* The 263-bit symmetric expansion of a coefficient vector: index i and index
 * 263-i both carry coefficient i, index 0 is zero.  Kept in five words. */
typedef struct sym {
    uint64_t w[5];
} sym;

static inline int sym_bit(const sym *s, unsigned i)
{
    return (int)((s->w[i >> 6] >> (i & 63)) & 1);
}

static inline void sym_setbit(sym *s, unsigned i) { s->w[i >> 6] |= (uint64_t)1 << (i & 63); }

static void sym_expand(sym *s, const ec2k_fe *a)
{
    memset(s, 0, sizeof(*s));
    for (unsigned i = 1; i <= EC2K_M; i++)
        if (fe_bit(a, i - 1)) {
            sym_setbit(s, i);
            sym_setbit(s, EC2K_N - i);
        }
}

/* r ^= rotate(s, k) over 263 bits. */
static void sym_xor_rot(sym *r, const sym *s, unsigned k)
{
    for (unsigned i = 0; i < EC2K_N; i++)
        if (sym_bit(s, i)) {
            unsigned j = i + k;
            if (j >= EC2K_N) j -= EC2K_N;
            r->w[j >> 6] ^= (uint64_t)1 << (j & 63);
        }
}

/*
 * c = a * b.
 *
 * beta_i * beta_j = beta_{i+j} + beta_{i-j}, so with A and B the symmetric
 * 263-vectors the product is their cyclic convolution -- and because B is
 * symmetric the convolution can be taken over j = 1..131 only, adding the
 * two rotations +j and -j together.  That halving is exactly what the RTL
 * does, which is why the model does it too rather than the textbook 263
 * iterations: the two must agree digit for digit when the testbench compares
 * partial products.
 */
void ec2k_fe_mul(ec2k_fe *r, const ec2k_fe *a, const ec2k_fe *b)
{
    sym A, C;
    sym_expand(&A, a);
    memset(&C, 0, sizeof(C));
    for (unsigned j = 1; j <= EC2K_M; j++) {
        if (!fe_bit(b, j - 1)) continue;
        sym_xor_rot(&C, &A, j);
        sym_xor_rot(&C, &A, EC2K_N - j);
    }
    /* Fold back: the result is symmetric, and index 0 is always zero for
     * symmetric inputs with no index-0 component (checked in the tests), so
     * reading indices 1..131 is the whole reduction. */
    ec2k_fe out;
    ec2k_fe_zero(&out);
    for (unsigned i = 1; i <= EC2K_M; i++)
        if (sym_bit(&C, i)) fe_setbit(&out, i - 1);
    *r = out;
}

/* ---- squaring and Frobenius --------------------------------------------- */

/* Squaring sends beta_i to beta_{2i mod 263}, folded by beta_{263-k} =
 * beta_k.  A permutation: free in hardware, a table lookup here. */
static unsigned fold_index(unsigned k)
{
    k %= EC2K_N;
    return k > EC2K_M ? EC2K_N - k : k;
}

void ec2k_fe_sqr(ec2k_fe *r, const ec2k_fe *a)
{
    ec2k_fe out;
    ec2k_fe_zero(&out);
    for (unsigned i = 1; i <= EC2K_M; i++)
        if (fe_bit(a, i - 1)) fe_setbit(&out, fold_index(2 * i) - 1);
    *r = out;
}

void ec2k_fe_frob(ec2k_fe *r, const ec2k_fe *a, unsigned j)
{
    j %= EC2K_M; /* sigma^131 is the identity on F_{2^131} */
    /* 2^j mod 263, then one permutation rather than j of them. */
    unsigned m = 1;
    for (unsigned k = 0; k < j; k++) m = (2 * m) % EC2K_N;
    ec2k_fe out;
    ec2k_fe_zero(&out);
    for (unsigned i = 1; i <= EC2K_M; i++)
        if (fe_bit(a, i - 1)) fe_setbit(&out, fold_index(m * i) - 1);
    *r = out;
}

/* ---- inversion ---------------------------------------------------------- */

/*
 * Itoh-Tsujii: a^-1 = a^(2^131 - 2) = (a^(2^130 - 1))^2, built from
 *
 *     a^(2^(m+n) - 1) = (a^(2^m - 1))^(2^n) * a^(2^n - 1)
 *
 * along the chain 1, 2, 4, 8, 16, 32, 64, 65, 130: eight multiplications and
 * a Frobenius power in between.  The hardware runs the same chain, with the
 * Frobenius powers as single-cycle permutations and the multiplications
 * through the one multiplier it has -- which is why inversion dominates the
 * cost of a step and why batching it across many walks is the first thing
 * a larger design would add.
 */
int ec2k_fe_inv(ec2k_fe *r, const ec2k_fe *a)
{
    if (ec2k_fe_is_zero(a)) {
        ec2k_fe_zero(r);
        return 0;
    }
    /* chain[i] - chain[i-1] is either chain[i-1] itself (a doubling step)
     * or 1 (the step from 64 to 65), so the second operand of each
     * multiplication is either the accumulator or a itself.  The hardware
     * makes exactly this case split, with the Frobenius powers as fixed
     * permutations and one multiplier shared across the eight products. */
    static const unsigned chain[] = {1, 2, 4, 8, 16, 32, 64, 65, 130};
    ec2k_fe acc = *a; /* acc = a^(2^1 - 1) */
    for (size_t i = 1; i < sizeof(chain) / sizeof(chain[0]); i++) {
        unsigned prev = chain[i - 1];
        unsigned add = chain[i] - prev;
        ec2k_fe shifted, other;
        ec2k_fe_frob(&shifted, &acc, add);
        other = (add == prev) ? acc : *a;
        ec2k_fe_mul(&acc, &shifted, &other);
    }
    ec2k_fe_sqr(r, &acc);
    return 1;
}

/* ---- serialisation ------------------------------------------------------ */

void ec2k_fe_to_bytes(uint8_t out[17], const ec2k_fe *a)
{
    for (int i = 0; i < 17; i++) {
        unsigned bit = (unsigned)i * 8;
        uint64_t v = 0;
        for (int k = 0; k < 8 && bit + (unsigned)k < EC2K_M; k++)
            v |= (uint64_t)fe_bit(a, bit + (unsigned)k) << k;
        out[i] = (uint8_t)v;
    }
}

int ec2k_fe_from_bytes(ec2k_fe *r, const uint8_t in[17])
{
    ec2k_fe_zero(r);
    for (int i = 0; i < 17; i++)
        for (int k = 0; k < 8; k++) {
            unsigned bit = (unsigned)i * 8 + (unsigned)k;
            if (!((in[i] >> k) & 1)) continue;
            if (bit >= EC2K_M) return 0; /* padding must be zero */
            fe_setbit(r, bit);
        }
    return 1;
}

void ec2k_fe_to_hex(char out[34], const ec2k_fe *a)
{
    static const char digits[] = "0123456789abcdef";
    for (int i = 0; i < 33; i++) {
        unsigned nib = 0;
        for (int k = 0; k < 4; k++) {
            unsigned bit = (unsigned)(32 - i) * 4 + (unsigned)k;
            if (bit < EC2K_M && fe_bit(a, bit)) nib |= 1u << k;
        }
        out[i] = digits[nib];
    }
    out[33] = '\0';
}

int ec2k_fe_from_hex(ec2k_fe *r, const char *hex)
{
    size_t len = strlen(hex);
    if (len == 0 || len > 33) return 0;
    ec2k_fe_zero(r);
    for (size_t i = 0; i < len; i++) {
        char c = hex[len - 1 - i];
        unsigned v;
        if (c >= '0' && c <= '9')
            v = (unsigned)(c - '0');
        else if (c >= 'a' && c <= 'f')
            v = (unsigned)(c - 'a' + 10);
        else if (c >= 'A' && c <= 'F')
            v = (unsigned)(c - 'A' + 10);
        else
            return 0;
        for (int k = 0; k < 4; k++) {
            if (!((v >> k) & 1)) continue;
            unsigned bit = (unsigned)i * 4 + (unsigned)k;
            if (bit >= EC2K_M) return 0;
            fe_setbit(r, bit);
        }
    }
    return 1;
}

/* ---- the curve ---------------------------------------------------------- */

/* y^2 + x y = x^3 + 1 : a = 0, b = 1. */
int ec2k_on_curve(const ec2k_pt *p)
{
    if (p->inf) return 1;
    ec2k_fe y2, xy, lhs, x2, x3, rhs, one;
    ec2k_fe_sqr(&y2, &p->y);
    ec2k_fe_mul(&xy, &p->x, &p->y);
    ec2k_fe_add(&lhs, &y2, &xy);
    ec2k_fe_sqr(&x2, &p->x);
    ec2k_fe_mul(&x3, &x2, &p->x);
    ec2k_fe_one(&one);
    ec2k_fe_add(&rhs, &x3, &one);
    return ec2k_fe_equal(&lhs, &rhs);
}

void ec2k_pt_neg(ec2k_pt *r, const ec2k_pt *p)
{
    /* -(x, y) = (x, x + y): x is invariant, which is why a distinguished
     * point can be reported as x alone and the negation class comes free. */
    r->inf = p->inf;
    r->x = p->x;
    ec2k_fe_add(&r->y, &p->x, &p->y);
}

void ec2k_pt_dbl(ec2k_pt *r, const ec2k_pt *p)
{
    if (p->inf || ec2k_fe_is_zero(&p->x)) {
        r->inf = 1;
        ec2k_fe_zero(&r->x);
        ec2k_fe_zero(&r->y);
        return;
    }
    ec2k_fe inv, lam, x2, t, y3, x3;
    ec2k_fe_inv(&inv, &p->x);
    ec2k_fe_mul(&t, &p->y, &inv);
    ec2k_fe_add(&lam, &p->x, &t); /* lambda = x + y/x */
    ec2k_fe_sqr(&x2, &lam);
    ec2k_fe_add(&x3, &x2, &lam); /* a = 0 */
    ec2k_fe_add(&t, &p->x, &x3);
    ec2k_fe_mul(&y3, &lam, &t);
    ec2k_fe_add(&y3, &y3, &x3);
    ec2k_fe_add(&y3, &y3, &p->y);
    r->x = x3;
    r->y = y3;
    r->inf = 0;
}

void ec2k_pt_add(ec2k_pt *r, const ec2k_pt *p, const ec2k_pt *q)
{
    if (p->inf) {
        *r = *q;
        return;
    }
    if (q->inf) {
        *r = *p;
        return;
    }
    if (ec2k_fe_equal(&p->x, &q->x)) {
        ec2k_fe sum;
        ec2k_fe_add(&sum, &p->y, &q->y);
        if (ec2k_fe_equal(&p->y, &q->y)) {
            ec2k_pt_dbl(r, p);
        } else {
            (void)sum;
            r->inf = 1; /* q == -p */
            ec2k_fe_zero(&r->x);
            ec2k_fe_zero(&r->y);
        }
        return;
    }
    ec2k_fe dx, dy, inv, lam, x3, t, y3;
    ec2k_fe_add(&dx, &p->x, &q->x);
    ec2k_fe_add(&dy, &p->y, &q->y);
    ec2k_fe_inv(&inv, &dx);
    ec2k_fe_mul(&lam, &dy, &inv);
    ec2k_fe_sqr(&x3, &lam);
    ec2k_fe_add(&x3, &x3, &lam);
    ec2k_fe_add(&x3, &x3, &p->x);
    ec2k_fe_add(&x3, &x3, &q->x); /* a = 0 */
    ec2k_fe_add(&t, &p->x, &x3);
    ec2k_fe_mul(&y3, &lam, &t);
    ec2k_fe_add(&y3, &y3, &x3);
    ec2k_fe_add(&y3, &y3, &p->y);
    r->x = x3;
    r->y = y3;
    r->inf = 0;
}

void ec2k_pt_frob(ec2k_pt *r, const ec2k_pt *p, unsigned j)
{
    r->inf = p->inf;
    ec2k_fe_frob(&r->x, &p->x, j);
    ec2k_fe_frob(&r->y, &p->y, j);
}

void ec2k_pt_mul(ec2k_pt *r, const ec2k_pt *p, const uint64_t *k, size_t words)
{
    ec2k_pt acc;
    acc.inf = 1;
    ec2k_fe_zero(&acc.x);
    ec2k_fe_zero(&acc.y);
    int top = -1;
    for (size_t i = 0; i < words * 64; i++)
        if ((k[i >> 6] >> (i & 63)) & 1) top = (int)i;
    for (int i = top; i >= 0; i--) {
        ec2k_pt t;
        ec2k_pt_dbl(&t, &acc);
        acc = t;
        if ((k[(size_t)i >> 6] >> ((size_t)i & 63)) & 1) {
            ec2k_pt s;
            ec2k_pt_add(&s, &acc, p);
            acc = s;
        }
    }
    *r = acc;
}

/* ---- points from seeds --------------------------------------------------- */

static uint64_t splitmix64(uint64_t *x)
{
    uint64_t z = (*x += 0x9E3779B97F4A7C15ULL);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
}

/*
 * Solve y^2 + x y = x^3 + 1 for y, given x != 0.
 *
 * Substituting y = x*u turns it into u^2 + u = x + 1/x^2, which is solvable
 * exactly when the absolute trace of the right-hand side is zero, and the
 * half-trace gives the root.  m = 131 is odd, so the half-trace
 * H(c) = sum_{i=0}^{(m-1)/2} c^(4^i) satisfies H^2 + H = c whenever Tr(c) = 0.
 */
static int fe_trace(const ec2k_fe *a)
{
    /* Tr(a) = sum_i a^(2^i).  In this basis the trace of every beta_i is 1,
     * so the trace of an element is the parity of its Hamming weight -- which
     * the test checks against the definition. */
    return (int)(ec2k_fe_weight(a) & 1);
}

static void fe_half_trace(ec2k_fe *r, const ec2k_fe *c)
{
    ec2k_fe acc = *c, term = *c;
    for (unsigned i = 1; i <= (EC2K_M - 1) / 2; i++) {
        ec2k_fe t;
        ec2k_fe_frob(&t, &term, 2);
        term = t;
        ec2k_fe_add(&acc, &acc, &term);
    }
    *r = acc;
}

static int point_from_x(ec2k_pt *p, const ec2k_fe *x)
{
    if (ec2k_fe_is_zero(x)) return 0;
    ec2k_fe x2, inv, c, u, one;
    ec2k_fe_sqr(&x2, x);
    if (!ec2k_fe_inv(&inv, &x2)) return 0;
    ec2k_fe_one(&one);
    ec2k_fe_add(&c, x, &inv); /* c = x + 1/x^2 */
    if (fe_trace(&c) != 0) return 0;
    fe_half_trace(&u, &c);
    p->inf = 0;
    p->x = *x;
    ec2k_fe_mul(&p->y, x, &u);
    return ec2k_on_curve(p);
}

void ec2k_point_from_seed(ec2k_pt *r, uint64_t seed)
{
    uint64_t s = seed ^ 0xC0FFEE1234567890ULL;
    for (;;) {
        ec2k_fe x;
        ec2k_fe_zero(&x);
        x.w[0] = splitmix64(&s);
        x.w[1] = splitmix64(&s);
        x.w[2] = splitmix64(&s) & TOP_MASK;
        ec2k_pt p;
        if (!point_from_x(&p, &x)) continue;
        /* Multiply by the cofactor 4 so that the walk stays in the
         * order-n subgroup, which is where the challenge lives. */
        ec2k_pt q;
        ec2k_pt_dbl(&q, &p);
        ec2k_pt_dbl(r, &q);
        if (!r->inf) return;
    }
}

/* ---- the walk ------------------------------------------------------------ */

unsigned ec2k_step_j(const ec2k_fe *x)
{
    return ((ec2k_fe_weight(x) >> 1) & (EC2K_J_COUNT - 1)) + EC2K_J_MIN;
}

int ec2k_step(ec2k_pt *r, const ec2k_pt *p)
{
    if (p->inf) return 0;
    unsigned j = ec2k_step_j(&p->x);
    ec2k_pt s;
    ec2k_pt_frob(&s, p, j);
    if (ec2k_fe_equal(&s.x, &p->x)) return 0; /* sigma^j(R) == +-R */
    ec2k_pt_add(r, &s, p);
    return !r->inf;
}

int ec2k_is_distinguished(const ec2k_fe *x) { return ec2k_is_distinguished_w(x, EC2K_DP_WEIGHT); }

int ec2k_is_distinguished_w(const ec2k_fe *x, unsigned weight)
{
    return ec2k_fe_weight(x) <= weight;
}

static int fe_less(const ec2k_fe *a, const ec2k_fe *b)
{
    for (int i = EC2K_WORDS - 1; i >= 0; i--) {
        if (a->w[i] != b->w[i]) return a->w[i] < b->w[i];
    }
    return 0;
}

void ec2k_orbit_min(ec2k_fe *r, const ec2k_fe *x)
{
    ec2k_fe best = *x, cur = *x;
    for (unsigned k = 1; k < EC2K_M; k++) {
        ec2k_fe t;
        ec2k_fe_sqr(&t, &cur);
        cur = t;
        if (fe_less(&cur, &best)) best = cur;
    }
    *r = best;
}

void ec2k_record_encode(uint8_t out[EC2K_RECORD_BYTES], const ec2k_record *rec)
{
    memset(out, 0, EC2K_RECORD_BYTES);
    for (int i = 0; i < 8; i++) out[i] = (uint8_t)(rec->seed >> (8 * i));
    ec2k_fe_to_bytes(out + 8, &rec->x);
}

int ec2k_record_decode(ec2k_record *rec, const uint8_t in[EC2K_RECORD_BYTES])
{
    rec->seed = 0;
    for (int i = 0; i < 8; i++) rec->seed |= (uint64_t)in[i] << (8 * i);
    if (!ec2k_fe_from_bytes(&rec->x, in + 8)) return 0;
    for (int i = 25; i < EC2K_RECORD_BYTES; i++)
        if (in[i]) return 0; /* the padding is part of the format */
    return 1;
}

uint64_t ec2k_walk(uint64_t seed, uint64_t steps, ec2k_sink sink, void *ctx, uint64_t *dps,
                   uint64_t *restarts)
{
    ec2k_pt r;
    ec2k_point_from_seed(&r, seed);
    uint64_t taken = 0, points = 0, restarted = 0;
    uint64_t salt = seed;
    for (uint64_t i = 0; i < steps; i++) {
        ec2k_pt next;
        if (!ec2k_step(&next, &r)) {
            /* The exceptional case.  Restart deterministically from a fresh
             * seed derived from this one, so that a replay of this walk
             * reproduces it exactly -- the hardware does the same, from the
             * same derivation. */
            salt = salt * 6364136223846793005ULL + 1442695040888963407ULL;
            ec2k_point_from_seed(&r, salt);
            restarted++;
            taken++;
            continue;
        }
        r = next;
        taken++;
        if (ec2k_is_distinguished(&r.x)) {
            ec2k_record rec;
            rec.seed = seed;
            ec2k_orbit_min(&rec.x, &r.x);
            points++;
            if (sink) sink(ctx, &rec);
        }
    }
    if (dps) *dps = points;
    if (restarts) *restarts = restarted;
    return taken;
}
