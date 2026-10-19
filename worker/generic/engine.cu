// Portable affine EC walk engine. This implementation prioritizes correctness;
// CPU, CUDA and Metal share the fixed-width arithmetic and walk transition in
// ec_core.h. Compile as C++17 with -x c++ (GCC, Clang, MSVC), as CUDA with
// nvcc -x cu, or for Metal through engine_metal.mm on macOS.
#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <system_error>
#include <thread>
#include <vector>
#ifdef __CUDACC__
#include <cuda_runtime.h>
#endif
#ifdef _OPENMP
#include <omp.h>
#endif
#include "ec_core.h"

namespace ecc {
constexpr std::size_t JOB_BYTES = 6568;
constexpr std::size_t STATE_BYTES = 216;
constexpr std::uint32_t MAX_LANES = 4096;
constexpr std::uint32_t MAX_ROUND_STEPS = 1048576;
static_assert(sizeof(State) == STATE_BYTES, "unexpected state layout");

#ifdef ECC_METAL
// Defined in engine_metal.mm, which includes this file.
void run_metal(const Job& job, std::vector<State>& states,
               std::vector<int>& errors);
std::vector<std::string> metal_devices();
std::string metal_source();
#endif

#ifdef __CUDACC__
__global__ void walk_kernel(const Job* job, State* states, int* errors) {
    const std::uint32_t index = blockIdx.x * blockDim.x + threadIdx.x;
    if (index < job->lanes)
        errors[index] = walk_lane(*job, states[index], job->steps_per_round);
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
    // Match the full-width shift and polynomial fold at every word boundary.
    for (int degree = 2; degree <= 359; ++degree) {
        Job check{};
        check.kind = 1;
        check.field_bits = std::uint32_t(degree);
        check.modulus = small(3);
        check.modulus.w[degree / 32] |= 1U << (degree % 32);
        for (int pattern = 0; pattern < 4; ++pattern) {
            UInt384 value{};
            for (int i = 0; i < (degree + 31) / 32; ++i)
                value.w[i] = (pattern & 1) ? 0xffffffffU
                    : std::uint32_t(0x9e3779b9U * std::uint32_t(i + degree + pattern));
            value.w[(degree - 1) / 32] &=
                0xffffffffU >> (31 - ((degree - 1) % 32));
            if (pattern & 2)
                value.w[(degree - 1) / 32] |= 1U << ((degree - 1) % 32);
            UInt384 expected = shift_left(value, 1);
            if (bit(value, degree - 1))
                expected = xor_value(expected, check.modulus);
            require(equal(binary_xtime(value, check), expected),
                    "binary xtime reference mismatch");
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

unsigned cpu_threads() {
#ifdef _OPENMP
    return unsigned(std::max(1, omp_get_max_threads()));
#else
    // Builds without OpenMP (Apple Clang, plain MSVC) honor the same knob, so
    // the client's lane sizing and container CPU limits behave identically.
    const char* value = std::getenv("OMP_NUM_THREADS");
    unsigned parsed = 0;
    for (; value && *value >= '0' && *value <= '9' && parsed < 100000; ++value)
        parsed = parsed * 10 + unsigned(*value - '0');
    if (parsed) return parsed;
    return std::max(1U, std::thread::hardware_concurrency());
#endif
}

// Lanes are independent, so spreading them over cores gives output identical
// to the serial loop. OpenMP is used when the compiler provides it.
void run_cpu(const Job& job, std::vector<State>& states,
             std::vector<int>& errors) {
    const int lanes = int(job.lanes);
#ifdef _OPENMP
#pragma omp parallel for schedule(dynamic, 1)
    for (int i = 0; i < lanes; ++i)
        errors[i] = walk_lane(job, states[i], job.steps_per_round);
#else
    std::atomic<int> next{0};
    auto worker = [&]() {
        for (int i = next++; i < lanes; i = next++)
            errors[i] = walk_lane(job, states[i], job.steps_per_round);
    };
    const unsigned wanted = std::min(cpu_threads(), unsigned(lanes));
    std::vector<std::thread> pool;
    try {
        for (unsigned t = 1; t < wanted; ++t) pool.emplace_back(worker);
    } catch (const std::system_error&) {
        // Fewer threads only changes the schedule, never the output.
    }
    worker();
    for (std::thread& thread : pool) thread.join();
#endif
}

std::string json_string(const std::string& value) {
    std::string out = "\"";
    for (const char c : value) {
        if (c == '"' || c == '\\') out += '\\';
        if (static_cast<unsigned char>(c) >= 0x20) out += c;
    }
    return out + "\"";
}

// Reports the compiled backends and visible devices so a client can choose a
// backend without starting a round.
void probe() {
    std::vector<std::string> backends{"cpu"};
    std::cout << "{\"cpu_threads\":" << cpu_threads();
#ifdef _OPENMP
    std::cout << ",\"openmp\":true";
#else
    std::cout << ",\"openmp\":false";
#endif
#ifdef __CUDACC__
    backends.push_back("cuda");
    int count = 0;
    if (cudaGetDeviceCount(&count) != cudaSuccess) count = 0;
    std::cout << ",\"cuda_devices\":" << count;
#endif
#ifdef ECC_METAL
    backends.push_back("metal");
    std::cout << ",\"metal_devices\":[";
    const std::vector<std::string> devices = metal_devices();
    for (std::size_t i = 0; i < devices.size(); ++i)
        std::cout << (i ? "," : "") << json_string(devices[i]);
    std::cout << "]";
#endif
    std::cout << ",\"backends\":[";
    for (std::size_t i = 0; i < backends.size(); ++i)
        std::cout << (i ? "," : "") << json_string(backends[i]);
    std::cout << "]}\n";
}

int run(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--self-test") {
        self_test(); return 0;
    }
    if (argc == 2 && std::string(argv[1]) == "--probe") {
        probe(); return 0;
    }
#ifdef ECC_METAL
    if (argc == 2 && std::string(argv[1]) == "--emit-metal-source") {
        std::cout << metal_source(); return 0;
    }
#endif
    require(argc == 7 && std::string(argv[1]) == "--backend",
            "usage: engine --backend cpu|cuda|metal JOB INPUT_STATES OUTPUT_STATES DP_STATES"
            "\n       engine --self-test | --probe");
    const std::string backend = argv[2];
    require(backend == "cpu" || backend == "cuda" || backend == "metal",
            "unknown backend");
#ifndef __CUDACC__
    require(backend != "cuda",
            "CUDA backend unavailable in this build; compile engine.cu using nvcc");
#endif
#ifndef ECC_METAL
    require(backend != "metal",
            "Metal backend unavailable in this build; build engine_metal.mm on macOS");
#endif
    require(std::string(argv[5]) != std::string(argv[6]),
            "output state and DP paths must differ");
    const Job job = load_job(argv[3]);
    std::vector<State> states = load_states(argv[4], job);
    std::vector<std::uint32_t> was_active(job.lanes);
    for (std::uint32_t i = 0; i < job.lanes; ++i) was_active[i] = states[i].active;
    std::vector<int> errors(job.lanes, 0);
    if (backend == "cpu") run_cpu(job, states, errors);
#ifdef __CUDACC__
    else if (backend == "cuda") run_cuda(job, states, errors);
#endif
#ifdef ECC_METAL
    else if (backend == "metal") run_metal(job, states, errors);
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
