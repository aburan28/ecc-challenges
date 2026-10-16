// Portable affine EC walk core shared by every engine backend.
//
// This header compiles unchanged as host C++17 (GCC, Clang, Apple Clang,
// MSVC), as CUDA device code (nvcc) and as Metal Shading Language, so the CPU,
// CUDA and Metal backends execute one definition of the field arithmetic, the
// SHA-256 point digest and the walk transition. Only address spaces differ:
// Metal requires them on references, so the macros below name them and expand
// to nothing elsewhere. Read-only operands are passed by value on Metal, which
// lets a constant-space job field bind to them without a cast.
#ifndef ECC_CORE_H
#define ECC_CORE_H

#if defined(__METAL_VERSION__)
// Spelled-out types need no Metal header; long is 64 bits in MSL.
namespace std {
typedef unsigned char uint8_t;
typedef unsigned int uint32_t;
typedef unsigned long uint64_t;
}
#define UINT64_MAX 0xffffffffffffffffUL
#define HD
#define ECC_CONST constant
#define ECC_JOB constant
#define ECC_THREAD thread
#define ECC_IN(T) T
#else
#include <cstdint>
#ifdef __CUDACC__
#define HD __host__ __device__
#else
#define HD
#endif
#define ECC_CONST
#define ECC_JOB
#define ECC_THREAD
#define ECC_IN(T) const T&
#endif

namespace ecc {
static ECC_CONST constexpr int WORDS = 12;

// Words are little endian. Persistence always uses explicit byte encoding.
struct UInt384 { std::uint32_t w[WORDS]; };
struct Point { UInt384 x, y; std::uint32_t infinity; };
struct Jump { UInt384 x, y, da, db; };
struct Job {
    std::uint32_t kind, field_bits, scalar_bits, dp_bits;
    std::uint32_t lanes, steps_per_round, table_size, reserved;
    UInt384 modulus, a, b, n, gx, gy, qx, qy;
    Jump jumps[32];
};
struct State {
    UInt384 x, y, a, b;
    std::uint64_t trail_id, steps;
    std::uint32_t active, infinity;
};
static_assert(sizeof(UInt384) == 48, "unexpected integer layout");
// Host and device copy these structures byte for byte, so the layout is
// pinned for every compiler, including the Metal shader compiler.
static_assert(sizeof(State) == 216, "unexpected state layout");
static_assert(sizeof(Job) == 6560, "unexpected job layout");

// Copies an integer out of the job word by word. Host and CUDA builds inline
// this to a plain copy; on Metal it is the read from the constant address
// space, which an implicit struct copy does not portably express.
HD inline UInt384 load(const ECC_JOB UInt384& value) {
    UInt384 r{};
    for (int i = 0; i < WORDS; ++i) r.w[i] = value.w[i];
    return r;
}
HD inline UInt384 small(std::uint32_t n) {
    UInt384 r{}; r.w[0] = n; return r;
}
HD inline bool is_zero(ECC_IN(UInt384) a) {
    std::uint32_t r = 0;
    for (int i = 0; i < WORDS; ++i) r |= a.w[i];
    return r == 0;
}
HD inline bool is_one(ECC_IN(UInt384) a) {
    if (a.w[0] != 1) return false;
    for (int i = 1; i < WORDS; ++i) if (a.w[i]) return false;
    return true;
}
HD inline int compare(ECC_IN(UInt384) a, ECC_IN(UInt384) b) {
    for (int i = WORDS - 1; i >= 0; --i) {
        if (a.w[i] < b.w[i]) return -1;
        if (a.w[i] > b.w[i]) return 1;
    }
    return 0;
}
HD inline bool equal(ECC_IN(UInt384) a, ECC_IN(UInt384) b) {
    return compare(a, b) == 0;
}
HD inline bool bit(ECC_IN(UInt384) a, int i) {
    return ((a.w[i / 32] >> (i % 32)) & 1U) != 0;
}
HD inline int bit_length(ECC_IN(UInt384) a) {
    for (int i = WORDS - 1; i >= 0; --i) {
        std::uint32_t v = a.w[i];
        if (v) {
            int n = 0;
            while (v) { ++n; v >>= 1; }
            return i * 32 + n;
        }
    }
    return 0;
}
HD inline UInt384 xor_value(ECC_IN(UInt384) a, ECC_IN(UInt384) b) {
    UInt384 r{};
    for (int i = 0; i < WORDS; ++i) r.w[i] = a.w[i] ^ b.w[i];
    return r;
}
HD inline UInt384 add_raw(ECC_IN(UInt384) a, ECC_IN(UInt384) b) {
    UInt384 r{};
    std::uint64_t carry = 0;
    for (int i = 0; i < WORDS; ++i) {
        const std::uint64_t t = std::uint64_t(a.w[i]) + b.w[i] + carry;
        r.w[i] = std::uint32_t(t);
        carry = t >> 32;
    }
    return r;
}
HD inline UInt384 subtract_raw(ECC_IN(UInt384) a, ECC_IN(UInt384) b) {
    UInt384 r{};
    std::uint64_t borrow = 0;
    for (int i = 0; i < WORDS; ++i) {
        const std::uint64_t t = std::uint64_t(b.w[i]) + borrow;
        r.w[i] = std::uint32_t(std::uint64_t(a.w[i]) - t);
        borrow = std::uint64_t(a.w[i]) < t ? 1 : 0;
    }
    return r;
}
HD inline UInt384 shift_right_one(ECC_IN(UInt384) a) {
    UInt384 r{};
    std::uint32_t carry = 0;
    for (int i = WORDS - 1; i >= 0; --i) {
        r.w[i] = (a.w[i] >> 1) | (carry << 31);
        carry = a.w[i] & 1U;
    }
    return r;
}
HD inline UInt384 shift_left(ECC_IN(UInt384) a, int bits) {
    UInt384 r{};
    const int words = bits / 32, rem = bits % 32;
    for (int i = WORDS - 1; i >= words; --i) {
        const int j = i - words;
        r.w[i] = a.w[j] << rem;
        if (rem && j > 0) r.w[i] |= a.w[j - 1] >> (32 - rem);
    }
    return r;
}

// Reduced operands and moduli below 2^360 leave room for the unreduced sum.
HD inline UInt384 add_mod(ECC_IN(UInt384) a, ECC_IN(UInt384) b,
                           ECC_IN(UInt384) m) {
    UInt384 r = add_raw(a, b);
    if (compare(r, m) >= 0) r = subtract_raw(r, m);
    return r;
}
HD inline UInt384 subtract_mod(ECC_IN(UInt384) a, ECC_IN(UInt384) b,
                                ECC_IN(UInt384) m) {
    if (compare(a, b) >= 0) return subtract_raw(a, b);
    return subtract_raw(m, subtract_raw(b, a));
}
HD inline UInt384 multiply_mod(UInt384 a, ECC_IN(UInt384) b,
                                ECC_IN(UInt384) m) {
    UInt384 r{};
    const int limit = bit_length(b);
    for (int i = 0; i < limit; ++i) {
        if (bit(b, i)) r = add_mod(r, a, m);
        a = add_mod(a, a, m);
    }
    return r;
}
HD inline UInt384 half_mod(UInt384 a, ECC_IN(UInt384) m) {
    if (a.w[0] & 1U) a = add_raw(a, m);
    return shift_right_one(a);
}

// Binary extended GCD. The watchdog is well above the bit-reduction bound;
// noninvertible input returns failure and never masquerades as a zero inverse.
HD inline bool inverse_mod(ECC_IN(UInt384) a, ECC_IN(UInt384) m,
                             ECC_THREAD UInt384& result) {
    if (is_zero(a) || !(m.w[0] & 1U)) return false;
    UInt384 u = a, v = m, x = small(1), y{};
    for (int watchdog = 0; watchdog < 8192; ++watchdog) {
        if (is_one(u)) { result = x; return true; }
        if (is_one(v)) { result = y; return true; }
        if (is_zero(u) || is_zero(v)) return false;
        if (!(u.w[0] & 1U)) {
            u = shift_right_one(u); x = half_mod(x, m);
        } else if (!(v.w[0] & 1U)) {
            v = shift_right_one(v); y = half_mod(y, m);
        } else if (compare(u, v) >= 0) {
            u = subtract_raw(u, v); x = subtract_mod(x, y, m);
        } else {
            v = subtract_raw(v, u); y = subtract_mod(y, x, m);
        }
    }
    return false;
}

HD inline UInt384 binary_xtime(ECC_IN(UInt384) a, const ECC_JOB Job& job) {
    UInt384 r{};
    // Include the modulus's leading bit; the other words remain zero.
    const int words = (int(job.field_bits) + 32) / 32;
    const std::uint32_t top =
        (a.w[(job.field_bits - 1) / 32] >> ((job.field_bits - 1) % 32)) & 1U;
    const std::uint32_t fold = 0U - top;
    std::uint32_t carry = 0;
    for (int i = 0; i < words; ++i) {
        const std::uint32_t word = a.w[i];
        r.w[i] = ((word << 1) | carry) ^ (job.modulus.w[i] & fold);
        carry = word >> 31;
    }
    return r;
}
HD inline UInt384 binary_multiply(UInt384 a, ECC_IN(UInt384) b,
                                   const ECC_JOB Job& job) {
    UInt384 r{};
    const int limit = bit_length(b);
    for (int i = 0; i < limit; ++i) {
        if (bit(b, i)) r = xor_value(r, a);
        a = binary_xtime(a, job);
    }
    return r;
}
HD inline UInt384 binary_xpower(UInt384 a, int power, const ECC_JOB Job& job) {
    for (int i = 0; i < power; ++i) a = binary_xtime(a, job);
    return a;
}
HD inline bool binary_inverse(ECC_IN(UInt384) a, const ECC_JOB Job& job,
                                ECC_THREAD UInt384& result) {
    if (is_zero(a)) return false;
    UInt384 u = a, v = load(job.modulus), x = small(1), y{};
    for (int watchdog = 0; watchdog < 8192; ++watchdog) {
        if (is_one(u)) { result = x; return true; }
        if (is_one(v)) { result = y; return true; }
        if (is_zero(u) || is_zero(v)) return false;
        int shift = bit_length(u) - bit_length(v);
        if (shift < 0) {
            UInt384 tmp = u; u = v; v = tmp;
            tmp = x; x = y; y = tmp;
            shift = -shift;
        }
        u = xor_value(u, shift_left(v, shift));
        // Keep the Bezout coefficients reduced instead of needing 768 bits.
        x = xor_value(x, binary_xpower(y, shift, job));
    }
    return false;
}
HD inline UInt384 field_add(ECC_IN(UInt384) a, ECC_IN(UInt384) b,
                             const ECC_JOB Job& job) {
    return job.kind ? xor_value(a, b) : add_mod(a, b, load(job.modulus));
}
HD inline UInt384 field_subtract(ECC_IN(UInt384) a, ECC_IN(UInt384) b,
                                  const ECC_JOB Job& job) {
    return job.kind ? xor_value(a, b) : subtract_mod(a, b, load(job.modulus));
}
HD inline UInt384 field_multiply(ECC_IN(UInt384) a, ECC_IN(UInt384) b,
                                  const ECC_JOB Job& job) {
    return job.kind ? binary_multiply(a, b, job)
                    : multiply_mod(a, b, load(job.modulus));
}
HD inline bool field_inverse(ECC_IN(UInt384) a, const ECC_JOB Job& job,
                               ECC_THREAD UInt384& result) {
    return job.kind ? binary_inverse(a, job, result)
                    : inverse_mod(a, load(job.modulus), result);
}
HD inline Point identity() {
    Point r{}; r.infinity = 1; return r;
}
HD inline bool point_equal(ECC_IN(Point) a, ECC_IN(Point) b) {
    if (a.infinity || b.infinity) return a.infinity == b.infinity;
    return equal(a.x, b.x) && equal(a.y, b.y);
}
HD inline bool on_curve(ECC_IN(Point) p, const ECC_JOB Job& job) {
    if (p.infinity) return true;
    const UInt384 x2 = field_multiply(p.x, p.x, job);
    const UInt384 x3 = field_multiply(x2, p.x, job);
    const UInt384 y2 = field_multiply(p.y, p.y, job);
    if (job.kind) {
        const UInt384 left = field_add(y2, field_multiply(p.x, p.y, job), job);
        const UInt384 right = field_add(
            field_add(x3, field_multiply(load(job.a), x2, job), job), load(job.b), job);
        return equal(left, right);
    }
    const UInt384 right = field_add(
        field_add(x3, field_multiply(load(job.a), p.x, job), job), load(job.b), job);
    return equal(y2, right);
}

// y^2 = x^3 + ax + b (prime), or y^2 + xy = x^3 + ax^2 + b (binary).
HD inline bool point_add(ECC_IN(Point) p, ECC_IN(Point) q, const ECC_JOB Job& job,
                           ECC_THREAD Point& result) {
    if (p.infinity) { result = q; return true; }
    if (q.infinity) { result = p; return true; }
    UInt384 slope{}, inverse{};
    if (equal(p.x, q.x)) {
        if (!equal(p.y, q.y) || (job.kind ? is_zero(p.x) : is_zero(p.y))) {
            result = identity(); return true;
        }
        if (job.kind) {
            if (!field_inverse(p.x, job, inverse)) return false;
            slope = field_add(p.x, field_multiply(p.y, inverse, job), job);
            result.x = field_add(
                field_add(field_multiply(slope, slope, job), slope, job),
                load(job.a), job);
            result.y = field_add(
                field_multiply(p.x, p.x, job),
                field_multiply(field_add(slope, small(1), job), result.x, job),
                job);
        } else {
            const UInt384 x2 = field_multiply(p.x, p.x, job);
            const UInt384 numerator = field_add(
                field_add(field_add(x2, x2, job), x2, job), load(job.a), job);
            const UInt384 denominator = field_add(p.y, p.y, job);
            if (!field_inverse(denominator, job, inverse)) return false;
            slope = field_multiply(numerator, inverse, job);
            result.x = field_subtract(
                field_multiply(slope, slope, job),
                field_add(p.x, p.x, job), job);
            result.y = field_subtract(
                field_multiply(slope, field_subtract(p.x, result.x, job), job),
                p.y, job);
        }
    } else {
        const UInt384 numerator = field_subtract(q.y, p.y, job);
        const UInt384 denominator = field_subtract(q.x, p.x, job);
        if (!field_inverse(denominator, job, inverse)) return false;
        slope = field_multiply(numerator, inverse, job);
        if (job.kind) {
            result.x = field_add(
                field_add(field_add(field_multiply(slope, slope, job),
                                    slope, job), p.x, job),
                field_add(q.x, load(job.a), job), job);
            result.y = field_add(
                field_add(field_multiply(slope, field_add(p.x, result.x, job),
                                         job), result.x, job), p.y, job);
        } else {
            result.x = field_subtract(
                field_subtract(field_multiply(slope, slope, job), p.x, job),
                q.x, job);
            result.y = field_subtract(
                field_multiply(slope, field_subtract(p.x, result.x, job), job),
                p.y, job);
        }
    }
    result.infinity = 0;
    return true;
}
HD inline std::uint32_t rotate_right(std::uint32_t value, unsigned bits) {
    return (value >> bits) | (value << (32 - bits));
}

// SHA-256 for the complete public point encoding (at most 90 bytes).
// An automatic constant table works in both the host and device compilation.
HD inline bool sha256_short(const ECC_THREAD std::uint8_t* input, unsigned length,
                            ECC_THREAD std::uint8_t digest[32]) {
    if (length > 90 || (length && input == nullptr)) return false;
    const std::uint32_t k[64] = {
        0x428a2f98U, 0x71374491U, 0xb5c0fbcfU, 0xe9b5dba5U,
        0x3956c25bU, 0x59f111f1U, 0x923f82a4U, 0xab1c5ed5U,
        0xd807aa98U, 0x12835b01U, 0x243185beU, 0x550c7dc3U,
        0x72be5d74U, 0x80deb1feU, 0x9bdc06a7U, 0xc19bf174U,
        0xe49b69c1U, 0xefbe4786U, 0x0fc19dc6U, 0x240ca1ccU,
        0x2de92c6fU, 0x4a7484aaU, 0x5cb0a9dcU, 0x76f988daU,
        0x983e5152U, 0xa831c66dU, 0xb00327c8U, 0xbf597fc7U,
        0xc6e00bf3U, 0xd5a79147U, 0x06ca6351U, 0x14292967U,
        0x27b70a85U, 0x2e1b2138U, 0x4d2c6dfcU, 0x53380d13U,
        0x650a7354U, 0x766a0abbU, 0x81c2c92eU, 0x92722c85U,
        0xa2bfe8a1U, 0xa81a664bU, 0xc24b8b70U, 0xc76c51a3U,
        0xd192e819U, 0xd6990624U, 0xf40e3585U, 0x106aa070U,
        0x19a4c116U, 0x1e376c08U, 0x2748774cU, 0x34b0bcb5U,
        0x391c0cb3U, 0x4ed8aa4aU, 0x5b9cca4fU, 0x682e6ff3U,
        0x748f82eeU, 0x78a5636fU, 0x84c87814U, 0x8cc70208U,
        0x90befffaU, 0xa4506cebU, 0xbef9a3f7U, 0xc67178f2U,
    };
    std::uint8_t padded[128]{};
    for (unsigned i = 0; i < length; ++i) padded[i] = input[i];
    padded[length] = 0x80;
    const unsigned blocks = length < 56 ? 1 : 2;
    const std::uint64_t bit_length = std::uint64_t(length) * 8;
    for (unsigned i = 0; i < 8; ++i)
        padded[blocks * 64 - 1 - i] = std::uint8_t(bit_length >> (8 * i));
    std::uint32_t hash[8] = {
        0x6a09e667U, 0xbb67ae85U, 0x3c6ef372U, 0xa54ff53aU,
        0x510e527fU, 0x9b05688cU, 0x1f83d9abU, 0x5be0cd19U,
    };
    for (unsigned block = 0; block < blocks; ++block) {
        std::uint32_t w[64]{};
        const unsigned base = block * 64;
        for (unsigned i = 0; i < 16; ++i) {
            const unsigned p = base + i * 4;
            w[i] = (std::uint32_t(padded[p]) << 24)
                 | (std::uint32_t(padded[p + 1]) << 16)
                 | (std::uint32_t(padded[p + 2]) << 8)
                 | std::uint32_t(padded[p + 3]);
        }
        for (unsigned i = 16; i < 64; ++i) {
            const std::uint32_t s0 = rotate_right(w[i - 15], 7)
                                  ^ rotate_right(w[i - 15], 18)
                                  ^ (w[i - 15] >> 3);
            const std::uint32_t s1 = rotate_right(w[i - 2], 17)
                                  ^ rotate_right(w[i - 2], 19)
                                  ^ (w[i - 2] >> 10);
            w[i] = w[i - 16] + s0 + w[i - 7] + s1;
        }
        std::uint32_t a = hash[0], b = hash[1], c = hash[2], d = hash[3];
        std::uint32_t e = hash[4], f = hash[5], g = hash[6], h = hash[7];
        for (unsigned i = 0; i < 64; ++i) {
            const std::uint32_t s1 = rotate_right(e, 6)
                                  ^ rotate_right(e, 11)
                                  ^ rotate_right(e, 25);
            const std::uint32_t choice = (e & f) ^ (~e & g);
            const std::uint32_t t1 = h + s1 + choice + k[i] + w[i];
            const std::uint32_t s0 = rotate_right(a, 2)
                                  ^ rotate_right(a, 13)
                                  ^ rotate_right(a, 22);
            const std::uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
            const std::uint32_t t2 = s0 + majority;
            h = g; g = f; f = e; e = d + t1;
            d = c; c = b; b = a; a = t1 + t2;
        }
        hash[0] += a; hash[1] += b; hash[2] += c; hash[3] += d;
        hash[4] += e; hash[5] += f; hash[6] += g; hash[7] += h;
    }
    for (unsigned i = 0; i < 8; ++i)
        for (unsigned j = 0; j < 4; ++j)
            digest[i * 4 + j] = std::uint8_t(hash[i] >> (24 - j * 8));
    return true;
}

HD inline bool point_digest(ECC_IN(UInt384) x, ECC_IN(UInt384) y,
                             const ECC_JOB Job& job, std::uint8_t digest[32]) {
    const unsigned width = (job.field_bits + 7) / 8;
    if (width < 1 || width > 45) return false;
    std::uint8_t encoded[90]{};
    for (unsigned i = 0; i < width; ++i) {
        const unsigned byte = width - 1 - i;
        const unsigned word = byte / 4, shift = (byte % 4) * 8;
        encoded[i] = std::uint8_t(x.w[word] >> shift);
        encoded[width + i] = std::uint8_t(y.w[word] >> shift);
    }
    return sha256_short(encoded, 2 * width, digest);
}

// Low bits of the digest interpreted as a big-endian integer are its tail.
HD inline bool digest_distinguished(const ECC_THREAD std::uint8_t digest[32],
                                     std::uint32_t bits) {
    if (bits < 1 || bits > 40) return false;
    const unsigned bytes = bits / 8, remainder = bits % 8;
    for (unsigned i = 0; i < bytes; ++i)
        if (digest[31 - i]) return false;
    return !remainder ||
        !(digest[31 - bytes] & ((1U << remainder) - 1U));
}

// Advances one lane by at most steps transitions. Any split of a round into
// shorter calls yields the same result: the next jump is always selected from
// the current point's digest, so a backend may chunk a round freely.
HD inline int walk_lane(const ECC_JOB Job& job, ECC_THREAD State& state,
                        std::uint32_t steps) {
    if (!state.active) return 0;
    std::uint8_t digest[32]{};
    std::uint32_t index = 0;
    if (!state.infinity) {
        if (!point_digest(state.x, state.y, job, digest)) return 3;
        index = digest[0] & 31U;
    }
    for (std::uint32_t i = 0; i < steps; ++i) {
        if (state.steps == UINT64_MAX) return 2;
        const ECC_JOB Jump& jump = job.jumps[index];
        const Point current{state.x, state.y, state.infinity};
        const Point delta{load(jump.x), load(jump.y), 0};
        Point next{};
        if (!point_add(current, delta, job, next)) return 1;
        state.x = next.x; state.y = next.y; state.infinity = next.infinity;
        state.a = add_mod(state.a, load(jump.da), load(job.n));
        state.b = add_mod(state.b, load(jump.db), load(job.n));
        ++state.steps;
        if (state.infinity) {
            index = 0;
            continue;
        }
        if (!point_digest(state.x, state.y, job, digest)) return 3;
        if (digest_distinguished(digest, job.dp_bits)) {
            state.active = 0;
            return 0;
        }
        // Reuse this digest when selecting the next transition.
        index = digest[0] & 31U;
    }
    return 0;
}
} // namespace ecc

#endif // ECC_CORE_H
