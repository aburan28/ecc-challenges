// Portable affine EC walk engine. This implementation prioritizes correctness;
// CPU and CUDA share the same fixed-width arithmetic and walk transition.
// Compile as C++17 with -x c++ or as CUDA with nvcc -x cu.
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#ifdef __CUDACC__
#include <cuda_runtime.h>
#define HD __host__ __device__
#else
#define HD
#endif

namespace ecc {
constexpr int WORDS = 12;
constexpr std::size_t JOB_BYTES = 6568;
constexpr std::size_t STATE_BYTES = 216;
constexpr std::uint32_t MAX_LANES = 4096;
constexpr std::uint32_t MAX_ROUND_STEPS = 1048576;

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
static_assert(sizeof(State) == STATE_BYTES, "unexpected state layout");

HD inline UInt384 small(std::uint32_t n) {
    UInt384 r{}; r.w[0] = n; return r;
}
HD inline bool is_zero(const UInt384& a) {
    std::uint32_t r = 0;
    for (int i = 0; i < WORDS; ++i) r |= a.w[i];
    return r == 0;
}
HD inline bool is_one(const UInt384& a) {
    if (a.w[0] != 1) return false;
    for (int i = 1; i < WORDS; ++i) if (a.w[i]) return false;
    return true;
}
HD inline int compare(const UInt384& a, const UInt384& b) {
    for (int i = WORDS - 1; i >= 0; --i) {
        if (a.w[i] < b.w[i]) return -1;
        if (a.w[i] > b.w[i]) return 1;
    }
    return 0;
}
HD inline bool equal(const UInt384& a, const UInt384& b) {
    return compare(a, b) == 0;
}
HD inline bool bit(const UInt384& a, int i) {
    return ((a.w[i / 32] >> (i % 32)) & 1U) != 0;
}
HD inline int bit_length(const UInt384& a) {
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
HD inline UInt384 xor_value(const UInt384& a, const UInt384& b) {
    UInt384 r{};
    for (int i = 0; i < WORDS; ++i) r.w[i] = a.w[i] ^ b.w[i];
    return r;
}
HD inline UInt384 add_raw(const UInt384& a, const UInt384& b) {
    UInt384 r{};
    std::uint64_t carry = 0;
    for (int i = 0; i < WORDS; ++i) {
        const std::uint64_t t = std::uint64_t(a.w[i]) + b.w[i] + carry;
        r.w[i] = std::uint32_t(t);
        carry = t >> 32;
    }
    return r;
}
HD inline UInt384 subtract_raw(const UInt384& a, const UInt384& b) {
    UInt384 r{};
    std::uint64_t borrow = 0;
    for (int i = 0; i < WORDS; ++i) {
        const std::uint64_t t = std::uint64_t(b.w[i]) + borrow;
        r.w[i] = std::uint32_t(std::uint64_t(a.w[i]) - t);
        borrow = std::uint64_t(a.w[i]) < t ? 1 : 0;
    }
    return r;
}
HD inline UInt384 shift_right_one(const UInt384& a) {
    UInt384 r{};
    std::uint32_t carry = 0;
    for (int i = WORDS - 1; i >= 0; --i) {
        r.w[i] = (a.w[i] >> 1) | (carry << 31);
        carry = a.w[i] & 1U;
    }
    return r;
}
HD inline UInt384 shift_left(const UInt384& a, int bits) {
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
HD inline UInt384 add_mod(const UInt384& a, const UInt384& b,
                           const UInt384& m) {
    UInt384 r = add_raw(a, b);
    if (compare(r, m) >= 0) r = subtract_raw(r, m);
    return r;
}
HD inline UInt384 subtract_mod(const UInt384& a, const UInt384& b,
                                const UInt384& m) {
    if (compare(a, b) >= 0) return subtract_raw(a, b);
    return subtract_raw(m, subtract_raw(b, a));
}
HD inline UInt384 multiply_mod(UInt384 a, const UInt384& b,
                                const UInt384& m) {
    UInt384 r{};
    const int limit = bit_length(b);
    for (int i = 0; i < limit; ++i) {
        if (bit(b, i)) r = add_mod(r, a, m);
        a = add_mod(a, a, m);
    }
    return r;
}
HD inline UInt384 half_mod(UInt384 a, const UInt384& m) {
    if (a.w[0] & 1U) a = add_raw(a, m);
    return shift_right_one(a);
}

// Binary extended GCD. The watchdog is well above the bit-reduction bound;
// noninvertible input returns failure and never masquerades as a zero inverse.
HD inline bool inverse_mod(const UInt384& a, const UInt384& m,
                             UInt384& result) {
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

HD inline UInt384 binary_xtime(const UInt384& a, const Job& job) {
    UInt384 r = shift_left(a, 1);
    if (bit(a, int(job.field_bits) - 1)) r = xor_value(r, job.modulus);
    return r;
}
HD inline UInt384 binary_multiply(UInt384 a, const UInt384& b,
                                   const Job& job) {
    UInt384 r{};
    const int limit = bit_length(b);
    for (int i = 0; i < limit; ++i) {
        if (bit(b, i)) r = xor_value(r, a);
        a = binary_xtime(a, job);
    }
    return r;
}
HD inline UInt384 binary_xpower(UInt384 a, int power, const Job& job) {
    for (int i = 0; i < power; ++i) a = binary_xtime(a, job);
    return a;
}
HD inline bool binary_inverse(const UInt384& a, const Job& job,
                                UInt384& result) {
    if (is_zero(a)) return false;
    UInt384 u = a, v = job.modulus, x = small(1), y{};
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
HD inline UInt384 field_add(const UInt384& a, const UInt384& b,
                             const Job& job) {
    return job.kind ? xor_value(a, b) : add_mod(a, b, job.modulus);
}
HD inline UInt384 field_subtract(const UInt384& a, const UInt384& b,
                                  const Job& job) {
    return job.kind ? xor_value(a, b) : subtract_mod(a, b, job.modulus);
}
HD inline UInt384 field_multiply(const UInt384& a, const UInt384& b,
                                  const Job& job) {
    return job.kind ? binary_multiply(a, b, job)
                    : multiply_mod(a, b, job.modulus);
}
HD inline bool field_inverse(const UInt384& a, const Job& job,
                               UInt384& result) {
    return job.kind ? binary_inverse(a, job, result)
                    : inverse_mod(a, job.modulus, result);
}
HD inline Point identity() {
    Point r{}; r.infinity = 1; return r;
}
HD inline bool point_equal(const Point& a, const Point& b) {
    if (a.infinity || b.infinity) return a.infinity == b.infinity;
    return equal(a.x, b.x) && equal(a.y, b.y);
}
HD inline bool on_curve(const Point& p, const Job& job) {
    if (p.infinity) return true;
    const UInt384 x2 = field_multiply(p.x, p.x, job);
    const UInt384 x3 = field_multiply(x2, p.x, job);
    const UInt384 y2 = field_multiply(p.y, p.y, job);
    if (job.kind) {
        const UInt384 left = field_add(y2, field_multiply(p.x, p.y, job), job);
        const UInt384 right = field_add(
            field_add(x3, field_multiply(job.a, x2, job), job), job.b, job);
        return equal(left, right);
    }
    const UInt384 right = field_add(
        field_add(x3, field_multiply(job.a, p.x, job), job), job.b, job);
    return equal(y2, right);
}

// y^2 = x^3 + ax + b (prime), or y^2 + xy = x^3 + ax^2 + b (binary).
HD inline bool point_add(const Point& p, const Point& q, const Job& job,
                           Point& result) {
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
                job.a, job);
            result.y = field_add(
                field_multiply(p.x, p.x, job),
                field_multiply(field_add(slope, small(1), job), result.x, job),
                job);
        } else {
            const UInt384 x2 = field_multiply(p.x, p.x, job);
            const UInt384 numerator = field_add(
                field_add(field_add(x2, x2, job), x2, job), job.a, job);
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
                field_add(q.x, job.a, job), job);
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
HD inline bool sha256_short(const std::uint8_t* input, unsigned length,
                            std::uint8_t digest[32]) {
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

HD inline bool point_digest(const UInt384& x, const UInt384& y,
                             const Job& job, std::uint8_t digest[32]) {
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
HD inline bool digest_distinguished(const std::uint8_t digest[32],
                                     std::uint32_t bits) {
    if (bits < 1 || bits > 40) return false;
    const unsigned bytes = bits / 8, remainder = bits % 8;
    for (unsigned i = 0; i < bytes; ++i)
        if (digest[31 - i]) return false;
    return !remainder ||
        !(digest[31 - bytes] & ((1U << remainder) - 1U));
}

HD inline int walk_lane(const Job& job, State& state) {
    if (!state.active) return 0;
    std::uint8_t digest[32]{};
    std::uint32_t index = 0;
    if (!state.infinity) {
        if (!point_digest(state.x, state.y, job, digest)) return 3;
        index = digest[0] & 31U;
    }
    for (std::uint32_t i = 0; i < job.steps_per_round; ++i) {
        if (state.steps == UINT64_MAX) return 2;
        const Jump& jump = job.jumps[index];
        const Point current{state.x, state.y, state.infinity};
        const Point delta{jump.x, jump.y, 0};
        Point next{};
        if (!point_add(current, delta, job, next)) return 1;
        state.x = next.x; state.y = next.y; state.infinity = next.infinity;
        state.a = add_mod(state.a, jump.da, job.n);
        state.b = add_mod(state.b, jump.db, job.n);
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
#ifdef __CUDACC__
__global__ void walk_kernel(const Job* job, State* states, int* errors) {
    const std::uint32_t index = blockIdx.x * blockDim.x + threadIdx.x;
    if (index < job->lanes) errors[index] = walk_lane(*job, states[index]);
}
#endif

std::vector<std::uint8_t> read_file(const std::string& path, std::size_t maximum) {
    std::ifstream input(path, std::ios::binary | std::ios::ate);
    if (!input) throw std::runtime_error("cannot open input file: " + path);
    const std::streamoff size = input.tellg();
    if (size < 0 || std::uint64_t(size) > maximum)
        throw std::runtime_error("input file exceeds permitted size: " + path);
    std::vector<std::uint8_t> data(static_cast<std::size_t>(size));
    input.seekg(0);
    if (!data.empty() && !input.read(reinterpret_cast<char*>(data.data()), size))
        throw std::runtime_error("failed reading input file: " + path);
    return data;
}
struct Reader {
    const std::vector<std::uint8_t>& data;
    std::size_t pos = 0;
    std::uint32_t u32() {
        if (data.size() - pos < 4) throw std::runtime_error("truncated u32");
        std::uint32_t v = 0;
        for (int i = 0; i < 4; ++i) v |= std::uint32_t(data[pos++]) << (i * 8);
        return v;
    }
    std::uint64_t u64() {
        const std::uint64_t low = u32(), high = u32();
        return low | (high << 32);
    }
    UInt384 integer() {
        UInt384 r{};
        for (int i = 0; i < WORDS; ++i) r.w[i] = u32();
        return r;
    }
};
void append_u32(std::vector<std::uint8_t>& data, std::uint32_t n) {
    for (int i = 0; i < 4; ++i) data.push_back(std::uint8_t(n >> (i * 8)));
}
void append_u64(std::vector<std::uint8_t>& data, std::uint64_t n) {
    append_u32(data, std::uint32_t(n)); append_u32(data, std::uint32_t(n >> 32));
}
void append_integer(std::vector<std::uint8_t>& data, const UInt384& n) {
    for (int i = 0; i < WORDS; ++i) append_u32(data, n.w[i]);
}
void require(bool condition, const std::string& message) {
    if (!condition) throw std::runtime_error(message);
}
bool canonical_field(const UInt384& value, const Job& job) {
    return job.kind ? bit_length(value) <= int(job.field_bits)
                    : compare(value, job.modulus) < 0;
}
void validate_point(const Point& point, const Job& job,
                    const std::string& name) {
    require(point.infinity <= 1, name + ": invalid infinity flag");
    require(canonical_field(point.x, job) && canonical_field(point.y, job),
            name + ": noncanonical coordinates");
    if (point.infinity)
        require(is_zero(point.x) && is_zero(point.y),
                name + ": infinity must have zero coordinates");
    require(on_curve(point, job), name + ": point is not on the curve");
}
Job load_job(const std::string& path) {
    const auto bytes = read_file(path, JOB_BYTES);
    require(bytes.size() == JOB_BYTES, "job must contain exactly 6568 bytes");
    require(std::memcmp(bytes.data(), "ECCJOB03", 8) == 0, "invalid job magic");
    Reader r{bytes, 8}; Job job{};
    job.kind = r.u32(); job.field_bits = r.u32(); job.scalar_bits = r.u32();
    job.dp_bits = r.u32(); job.lanes = r.u32(); job.steps_per_round = r.u32();
    job.table_size = r.u32(); job.reserved = r.u32();
    require(job.kind <= 1, "unsupported field kind");
    require(job.field_bits >= 2 && job.field_bits <= 359,
            "field_bits must be between 2 and 359");
    require(job.scalar_bits >= 2 && job.scalar_bits <= 360,
            "scalar_bits must be between 2 and 360");
    require(job.dp_bits >= 1 && job.dp_bits <= 40, "dp_bits must be 1..40");
    require(job.lanes >= 1 && job.lanes <= MAX_LANES, "lanes must be 1..4096");
    require(job.steps_per_round >= 1 && job.steps_per_round <= MAX_ROUND_STEPS,
            "steps_per_round must be 1..1048576");
    require(job.table_size == 32 && job.reserved == 0,
            "unsupported table size or reserved field");
    job.modulus = r.integer(); job.a = r.integer(); job.b = r.integer();
    job.n = r.integer(); job.gx = r.integer(); job.gy = r.integer();
    job.qx = r.integer(); job.qy = r.integer();
    require(bit_length(job.modulus) == int(job.field_bits) + int(job.kind),
            "modulus has the wrong bit length");
    require((job.modulus.w[0] & 1U) != 0, "modulus must be odd");
    require(bit_length(job.n) == int(job.scalar_bits), "order has wrong bit length");
    require(compare(job.n, small(2)) >= 0, "order must be at least two");
    require(canonical_field(job.a, job) && canonical_field(job.b, job),
            "noncanonical curve coefficients");
    if (job.kind) {
        require(!is_zero(job.b), "binary curve must have nonzero b");
    } else {
        const UInt384 a2 = field_multiply(job.a, job.a, job);
        const UInt384 a3 = field_multiply(a2, job.a, job);
        const UInt384 b2 = field_multiply(job.b, job.b, job);
        // Repeated addition also permits fields smaller than the constants.
        UInt384 four_a3{}, twentyseven_b2{};
        for (int i = 0; i < 4; ++i)
            four_a3 = field_add(four_a3, a3, job);
        for (int i = 0; i < 27; ++i)
            twentyseven_b2 = field_add(twentyseven_b2, b2, job);
        require(!is_zero(field_add(four_a3, twentyseven_b2, job)),
                "singular prime curve");
    }
    validate_point(Point{job.gx, job.gy, 0}, job, "G");
    validate_point(Point{job.qx, job.qy, 0}, job, "Q");
    for (int i = 0; i < 32; ++i) {
        Jump& j = job.jumps[i];
        j.x = r.integer(); j.y = r.integer(); j.da = r.integer(); j.db = r.integer();
        require(compare(j.da, job.n) < 0 && compare(j.db, job.n) < 0,
                "jump coefficients must be reduced");
        validate_point(Point{j.x, j.y, 0}, job, "jump " + std::to_string(i));
    }
    require(r.pos == bytes.size(), "job parser length mismatch");
    return job;
}
std::vector<State> load_states(const std::string& path, const Job& job) {
    const std::size_t wanted = std::size_t(job.lanes) * STATE_BYTES;
    const auto bytes = read_file(path, wanted);
    require(bytes.size() == wanted, "input state length does not match lane count");
    Reader r{bytes}; std::vector<State> states(job.lanes);
    for (std::uint32_t i = 0; i < job.lanes; ++i) {
        State& s = states[i];
        s.x = r.integer(); s.y = r.integer(); s.a = r.integer(); s.b = r.integer();
        s.trail_id = r.u64(); s.steps = r.u64();
        s.active = r.u32(); s.infinity = r.u32();
        require(s.active <= 1, "invalid active flag in state " + std::to_string(i));
        require(compare(s.a, job.n) < 0 && compare(s.b, job.n) < 0,
                "noncanonical scalar in state " + std::to_string(i));
        validate_point(Point{s.x, s.y, s.infinity}, job,
                       "state " + std::to_string(i));
    }
    return states;
}
void append_state(std::vector<std::uint8_t>& data, const State& s) {
    append_integer(data, s.x); append_integer(data, s.y);
    append_integer(data, s.a); append_integer(data, s.b);
    append_u64(data, s.trail_id); append_u64(data, s.steps);
    append_u32(data, s.active); append_u32(data, s.infinity);
}
void write_file(const std::string& path, const std::vector<std::uint8_t>& bytes) {
    std::ofstream output(path, std::ios::binary | std::ios::trunc);
    if (!output) throw std::runtime_error("cannot open output file: " + path);
    if (!bytes.empty())
        output.write(reinterpret_cast<const char*>(bytes.data()), bytes.size());
    output.close();
    if (!output) throw std::runtime_error("failed writing output file: " + path);
}
#ifdef __CUDACC__
void cuda_check(cudaError_t error, const char* action) {
    if (error != cudaSuccess)
        throw std::runtime_error(std::string(action) + ": " + cudaGetErrorString(error));
}
template<typename T> struct DeviceBuffer {
    T* ptr = nullptr;
    explicit DeviceBuffer(std::size_t count) {
        cuda_check(cudaMalloc(reinterpret_cast<void**>(&ptr), count * sizeof(T)),
                   "cudaMalloc");
    }
    ~DeviceBuffer() { if (ptr) cudaFree(ptr); }
    DeviceBuffer(const DeviceBuffer&) = delete;
    DeviceBuffer& operator=(const DeviceBuffer&) = delete;
};
void run_cuda(const Job& job, std::vector<State>& states,
              std::vector<int>& errors) {
    int count = 0;
    cuda_check(cudaGetDeviceCount(&count), "CUDA device discovery");
    require(count > 0, "no CUDA device is available");
    cuda_check(cudaDeviceSetLimit(cudaLimitStackSize, 8192),
               "CUDA stack limit");
    DeviceBuffer<Job> device_job(1);
    DeviceBuffer<State> device_states(job.lanes);
    DeviceBuffer<int> device_errors(job.lanes);
    cuda_check(cudaMemcpy(device_job.ptr, &job, sizeof(job), cudaMemcpyHostToDevice),
               "copy job to CUDA");
    cuda_check(cudaMemcpy(device_states.ptr, states.data(),
                          sizeof(State) * job.lanes, cudaMemcpyHostToDevice),
               "copy states to CUDA");
    walk_kernel<<<(job.lanes + 127) / 128, 128>>>(
        device_job.ptr, device_states.ptr, device_errors.ptr);
    cuda_check(cudaGetLastError(), "CUDA walk launch");
    cuda_check(cudaDeviceSynchronize(), "CUDA walk execution");
    cuda_check(cudaMemcpy(states.data(), device_states.ptr,
                          sizeof(State) * job.lanes, cudaMemcpyDeviceToHost),
               "copy states from CUDA");
    cuda_check(cudaMemcpy(errors.data(), device_errors.ptr,
                          sizeof(int) * job.lanes, cudaMemcpyDeviceToHost),
               "copy errors from CUDA");
}
#endif

void check_sha256(const std::uint8_t* input, unsigned length,
                  const std::string& expected) {
    std::uint8_t digest[32]{};
    require(sha256_short(input, length, digest), "SHA-256 input bound");
    const char hex[] = "0123456789abcdef";
    std::string actual;
    for (unsigned i = 0; i < 32; ++i) {
        actual += hex[digest[i] >> 4];
        actual += hex[digest[i] & 15];
    }
    require(actual == expected, "SHA-256 known-answer mismatch");
}

void sha256_self_test() {
    const std::uint8_t abc[] = {'a', 'b', 'c'};
    check_sha256(nullptr, 0,
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    check_sha256(abc, 3,
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    std::uint8_t sequential[91]{};
    for (unsigned i = 0; i < 91; ++i) sequential[i] = std::uint8_t(i);
    check_sha256(sequential, 55,
        "463eb28e72f82e0a96c0a4cc53690c571281131f672aa229e0d45ae59b598b59");
    check_sha256(sequential, 56,
        "da2ae4d6b36748f2a318f23e7ab1dfdf45acdc9d049bd80e59de82a60895f562");
    check_sha256(sequential, 64,
        "fdeab9acf3710362bd2658cdc9a29e8f9c757fcf9811603a8c447cd1d9151108");
    check_sha256(sequential, 90,
        "e052dff9e1c94aaa49556f86fad55029a4875839fda57f5005f4c4403876b256");
    std::uint8_t digest[32]{};
    require(!sha256_short(sequential, 91, digest), "SHA-256 oversized input");
    require(!sha256_short(nullptr, 1, digest), "SHA-256 null input");
    // Cross-byte tail masking, including exactly 40 trailing bits.
    digest[0] = 31;
    require(digest_distinguished(digest, 40), "SHA-256 tail ignores prefix");
    digest[27] = 1;
    require(digest_distinguished(digest, 32), "SHA-256 32-bit tail");
    require(!digest_distinguished(digest, 33), "SHA-256 33-bit tail");
    digest[27] = 0x80;
    require(digest_distinguished(digest, 39), "SHA-256 39-bit tail");
    require(!digest_distinguished(digest, 40), "SHA-256 40-bit tail");
}

void self_test() {
    sha256_self_test();
    const UInt384 prime = small(101);
    UInt384 inv{};
    require(!inverse_mod(small(0), prime, inv), "inverse zero must fail");
    for (std::uint32_t a = 0; a < 101; ++a) {
        for (std::uint32_t b = 0; b < 101; ++b)
            require(equal(multiply_mod(small(a), small(b), prime),
                          small((a * b) % 101)), "prime multiplication");
        if (a) {
            require(inverse_mod(small(a), prime, inv), "prime inverse exists");
            require(is_one(multiply_mod(small(a), inv, prime)), "prime inverse");
        }
    }
    // Exercise carries and the odd-half path above 64 bits using 2^127 - 1.
    UInt384 mersenne{};
    mersenne.w[0] = mersenne.w[1] = mersenne.w[2] = 0xffffffffU;
    mersenne.w[3] = 0x7fffffffU;
    const UInt384 minus_one = subtract_raw(mersenne, small(1));
    require(is_one(multiply_mod(minus_one, minus_one, mersenne)),
            "wide modular multiplication");
    for (std::uint32_t a = 2; a <= 64; ++a) {
        const UInt384 value = subtract_raw(mersenne, small(a));
        require(inverse_mod(value, mersenne, inv), "wide inverse exists");
        require(is_one(multiply_mod(value, inv, mersenne)), "wide inverse");
    }
    Job binary{}; binary.kind = 1; binary.field_bits = 4;
    binary.modulus = small(0x13); binary.a = small(1); binary.b = small(1);
    require(!binary_inverse(small(0), binary, inv), "binary inverse zero");
    for (std::uint32_t a = 0; a < 16; ++a) {
        for (std::uint32_t b = 0; b < 16; ++b) {
            std::uint32_t expected = 0;
            for (int i = 0; i < 4; ++i) if ((b >> i) & 1U) expected ^= a << i;
            for (int i = 6; i >= 4; --i)
                if ((expected >> i) & 1U) expected ^= 0x13U << (i - 4);
            require(equal(binary_multiply(small(a), small(b), binary),
                          small(expected)), "binary multiplication");
        }
        if (a) {
            require(binary_inverse(small(a), binary, inv), "binary inverse exists");
            require(is_one(binary_multiply(small(a), inv, binary)), "binary inverse");
        }
    }
    Job curve{}; curve.modulus = small(17); curve.field_bits = 5;
    curve.a = small(2); curve.b = small(2);
    const Point g{small(5), small(1), 0};
    Point doubled{};
    require(point_add(g, g, curve, doubled), "prime double succeeds");
    require(point_equal(doubled, Point{small(6), small(3), 0}),
            "known prime doubling vector");
    Point sum = identity();
    for (int i = 1; i <= 19; ++i) {
        Point next{};
        require(point_add(sum, g, curve, next), "prime order walk");
        sum = next;
        require(on_curve(sum, curve), "prime group closure");
        require(bool(sum.infinity) == (i == 19), "known prime order");
    }
    std::vector<Point> points{identity()};
    for (std::uint32_t x = 0; x < 16; ++x)
        for (std::uint32_t y = 0; y < 16; ++y) {
            const Point p{small(x), small(y), 0};
            if (on_curve(p, binary)) points.push_back(p);
        }
    require(points.size() > 4, "binary point enumeration");
    for (const Point& p : points) {
        Point negative = p;
        if (!p.infinity) negative.y = xor_value(p.x, p.y);
        Point result{};
        require(point_add(p, negative, binary, result) && result.infinity,
                "binary inverse point");
        for (const Point& q : points) {
            Point pq{}, qp{};
            require(point_add(p, q, binary, pq) && point_add(q, p, binary, qp),
                    "binary addition succeeds");
            require(on_curve(pq, binary) && point_equal(pq, qp),
                    "binary closure and commutativity");
            for (const Point& r : points) {
                Point left{}, qr{}, right{};
                require(point_add(pq, r, binary, left) &&
                        point_add(q, r, binary, qr) &&
                        point_add(p, qr, binary, right) &&
                        point_equal(left, right), "binary associativity");
            }
        }
    }
    std::cout << "{\"self_test\":\"passed\",\"wide_modulus_bits\":127,"
                 "\"prime_control_order\":19,\"binary_field_bits\":4}\n";
}

int run(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--self-test") {
        self_test(); return 0;
    }
    require(argc == 7 && std::string(argv[1]) == "--backend",
            "usage: engine --backend cpu|cuda JOB INPUT_STATES OUTPUT_STATES DP_STATES"
            "\n       engine --self-test");
    const std::string backend = argv[2];
    require(backend == "cpu" || backend == "cuda", "unknown backend");
#ifndef __CUDACC__
    require(backend != "cuda",
            "CUDA backend unavailable in this build; compile engine.cu using nvcc");
#endif
    require(std::string(argv[5]) != std::string(argv[6]),
            "output state and DP paths must differ");
    const Job job = load_job(argv[3]);
    std::vector<State> states = load_states(argv[4], job);
    std::vector<std::uint32_t> was_active(job.lanes);
    for (std::uint32_t i = 0; i < job.lanes; ++i) was_active[i] = states[i].active;
    std::vector<int> errors(job.lanes, 0);
    if (backend == "cpu") {
        // Lanes are independent, so a -fopenmp build spreads them over cores
        // with output identical to the serial loop.
        const std::int64_t lanes = job.lanes;
#pragma omp parallel for schedule(dynamic, 1)
        for (std::int64_t i = 0; i < lanes; ++i)
            errors[i] = walk_lane(job, states[i]);
    }
#ifdef __CUDACC__
    else run_cuda(job, states, errors);
#endif
    for (std::uint32_t i = 0; i < job.lanes; ++i)
        require(errors[i] == 0, "lane " + std::to_string(i) +
                (errors[i] == 2 ? ": step counter exhausted" :
                                  (errors[i] == 3 ? ": point digest failed" :
                                   ": field inversion failed or watchdog expired")));
    std::vector<std::uint8_t> output, dps;
    output.reserve(std::size_t(job.lanes) * STATE_BYTES);
    for (std::uint32_t i = 0; i < job.lanes; ++i) {
        append_state(output, states[i]);
        if (was_active[i] && !states[i].active && !states[i].infinity)
            append_state(dps, states[i]);
    }
    write_file(argv[5], output); write_file(argv[6], dps);
    std::cout << "{\"backend\":\"" << backend << "\",\"lanes\":" << job.lanes
              << ",\"distinguished_points\":" << dps.size() / STATE_BYTES << "}\n";
    return 0;
}
} // namespace ecc

int main(int argc, char** argv) {
    try { return ecc::run(argc, argv); }
    catch (const std::exception& error) {
        std::cerr << "engine: " << error.what() << '\n';
        return 1;
    }
}
