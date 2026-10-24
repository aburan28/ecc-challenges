// Metal backend for the generic engine on macOS (Apple silicon, and Intel Macs
// with an AMD or Intel GPU). The kernel is compiled at run time from the same
// ec_core.h that the CPU and CUDA backends use, so no offline Metal toolchain
// is needed. The binary also contains the CPU backend.
//
//   cmake -S worker -B build && cmake --build build      (preferred), or
//   deploy/native/install.sh                              (no CMake needed)
//
// Both generate metal_core_source.inc, which holds ec_core.h as a string.
// AssertMacros.h (reached through Foundation) otherwise defines require() and
// check() as macros, which would rewrite the engine's require() calls.
#define __ASSERT_MACROS_DEFINE_VERSIONS_WITHOUT_UNDERSCORES 0
#define ECC_METAL 1
#include "engine.cu"
#include "metal_core_source.inc"

#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

namespace ecc {
namespace {

// Lanes per thread for the specialised kernels: each thread shares one field
// inversion between this many walks.
constexpr int FAST_LANES_PER_THREAD = 4;
const char fast_kernel_source[] = R"ECCMSL(
typedef ecc::f131::PrimeField<ecc::f131::u32, 5> PrimeGpu;
typedef ecc::f131::BinaryField<true> BinaryGpu;
kernel void fast_prime5(device const PrimeGpu::C* ctx [[buffer(0)]],
                        device const ecc::f131::Entry<PrimeGpu>* table [[buffer(1)]],
                        device const ecc::f131::Params* prm [[buffer(2)]],
                        device ecc::f131::Lane<PrimeGpu>* lanes [[buffer(3)]],
                        constant uint* dims [[buffer(4)]],
                        uint tid [[thread_position_in_grid]]) {
    const PrimeGpu::C c = *ctx;
    const ecc::f131::Params p = *prm;
    ecc::f131::gpu_chunk<PrimeGpu, FAST_K>(c, table, p, lanes, dims[0], tid * FAST_K, dims[1]);
}
kernel void fast_binary(device const BinaryGpu::C* ctx [[buffer(0)]],
                        device const ecc::f131::Entry<BinaryGpu>* table [[buffer(1)]],
                        device const ecc::f131::Params* prm [[buffer(2)]],
                        device ecc::f131::Lane<BinaryGpu>* lanes [[buffer(3)]],
                        constant uint* dims [[buffer(4)]],
                        uint tid [[thread_position_in_grid]]) {
    const BinaryGpu::C c = *ctx;
    const ecc::f131::Params p = *prm;
    ecc::f131::gpu_chunk<BinaryGpu, FAST_K>(c, table, p, lanes, dims[0], tid * FAST_K, dims[1]);
}
)ECCMSL";

const char kernel_source[] = R"ECCMSL(
kernel void walk_kernel(const constant ecc::Job& job [[buffer(0)]],
                        device ecc::State* states [[buffer(1)]],
                        device int* errors [[buffer(2)]],
                        const constant uint& steps [[buffer(3)]],
                        uint index [[thread_position_in_grid]]) {
    if (index >= job.lanes) return;
    ecc::State state = states[index];
    errors[index] = ecc::walk_lane(job, state, steps);
    states[index] = state;
}
)ECCMSL";

// macOS stops a command buffer that runs too long, and how long a round takes
// depends on the curve and the GPU. Each command buffer therefore advances at
// most this many steps; walk_lane makes any such split exact.
constexpr std::uint32_t CHUNK_STEPS = 16;

std::string describe(NSError* error) {
    return error ? std::string(error.localizedDescription.UTF8String)
                 : std::string("unknown error");
}

// ECC_GPU selects a device by its index in --probe's metal_devices list;
// otherwise the system default device (the display GPU) is used.
id<MTLDevice> select_device() {
    NSArray<id<MTLDevice>>* devices = MTLCopyAllDevices();
    const char* chosen = std::getenv("ECC_GPU");
    if (chosen && *chosen) {
        unsigned long index = 0;
        for (const char* c = chosen; *c; ++c) {
            require(*c >= '0' && *c <= '9' && index < 1000,
                    "ECC_GPU must be a Metal device index for the Metal backend");
            index = index * 10 + unsigned(*c - '0');
        }
        require(index < devices.count, "ECC_GPU is outside the Metal device list");
        return devices[index];
    }
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (!device && devices.count) device = devices[0];
    require(device != nil, "no Metal device is available");
    return device;
}

} // namespace

std::string metal_source() {
    return std::string(core_source) + kernel_source;
}

std::string fast_metal_source(bool soft_mul32) {
    return std::string(core_source) +
           (soft_mul32 ? "\n#define F131_SOFT_MUL32 1\n" : "\n") +
           fast_source + "\n#define FAST_K " +
           std::to_string(FAST_LANES_PER_THREAD) + "\n" + fast_kernel_source;
}

namespace {
// Steps per command buffer: macOS stops a command buffer that runs too long.
constexpr std::uint32_t FAST_CHUNK_STEPS = 64;

template <class FD>
void run_metal_fast_field(const Job& job, const JobExt& ext, const char* entry,
                          std::vector<State>& states, std::vector<int>& errors,
                          bool& soft_mul32) {
    @autoreleasepool {
        f131::Prepared<FD> walk = f131::prepare<FD>(job, ext.walk, ext.anchor_log2, ext.dp_shift, ext.escape);
        std::vector<f131::Lane<FD>> lanes(job.lanes);
        for (std::uint32_t i = 0; i < job.lanes; ++i) lanes[i] = f131::lane_in(states[i], walk);

        id<MTLDevice> device = select_device();
        // GitHub's Apple Paravirtual device produced an invalid prime walk-4
        // state. The split product tests whether its 64-bit multiply lowering
        // is involved; retain the native product on other Metal devices.
        soft_mul32 = std::string(device.name.UTF8String).find("Paravirtual") != std::string::npos;
        NSError* error = nil;
        const std::string source = fast_metal_source(soft_mul32);
        id<MTLLibrary> library = [device
            newLibraryWithSource:[NSString stringWithUTF8String:source.c_str()]
                         options:[MTLCompileOptions new]
                           error:&error];
        require(library != nil, "Metal fast kernel compilation: " + describe(error));
        id<MTLFunction> function =
            [library newFunctionWithName:[NSString stringWithUTF8String:entry]];
        require(function != nil, "Metal fast kernel entry point is missing");
        id<MTLComputePipelineState> pipeline =
            [device newComputePipelineStateWithFunction:function error:&error];
        require(pipeline != nil, "Metal fast pipeline creation: " + describe(error));
        id<MTLCommandQueue> queue = [device newCommandQueue];
        require(queue != nil, "Metal command queue creation failed");

        const auto shared = [&](const void* bytes, std::size_t length) {
            return [device newBufferWithBytes:bytes length:length
                                      options:MTLResourceStorageModeShared];
        };
        id<MTLBuffer> ctx_buffer = shared(&walk.ctx, sizeof(walk.ctx));
        id<MTLBuffer> table_buffer = shared(walk.table.data(),
                                            walk.table.size() * sizeof(walk.table[0]));
        id<MTLBuffer> params_buffer = shared(&walk.params, sizeof(walk.params));
        id<MTLBuffer> lane_buffer = shared(lanes.data(), lanes.size() * sizeof(lanes[0]));
        require(ctx_buffer && table_buffer && params_buffer && lane_buffer,
                "Metal buffer allocation failed");

        const std::uint32_t threads =
            (job.lanes + FAST_LANES_PER_THREAD - 1) / FAST_LANES_PER_THREAD;
        const NSUInteger width = std::min<NSUInteger>(pipeline.maxTotalThreadsPerThreadgroup, 64);
        const MTLSize group = MTLSizeMake(width, 1, 1);
        const MTLSize grid = MTLSizeMake((threads + width - 1) / width, 1, 1);
        const f131::Lane<FD>* device_lanes =
            static_cast<const f131::Lane<FD>*>(lane_buffer.contents);
        for (std::uint32_t done = 0; done < job.steps_per_round;) {
            const std::uint32_t steps = std::min(FAST_CHUNK_STEPS, job.steps_per_round - done);
            const std::uint32_t dims[2] = {job.lanes, steps};
            id<MTLCommandBuffer> commands = [queue commandBuffer];
            id<MTLComputeCommandEncoder> encoder = [commands computeCommandEncoder];
            [encoder setComputePipelineState:pipeline];
            [encoder setBuffer:ctx_buffer offset:0 atIndex:0];
            [encoder setBuffer:table_buffer offset:0 atIndex:1];
            [encoder setBuffer:params_buffer offset:0 atIndex:2];
            [encoder setBuffer:lane_buffer offset:0 atIndex:3];
            [encoder setBytes:dims length:sizeof(dims) atIndex:4];
            [encoder dispatchThreadgroups:grid threadsPerThreadgroup:group];
            [encoder endEncoding];
            [commands commit];
            [commands waitUntilCompleted];
            require(commands.status == MTLCommandBufferStatusCompleted,
                    "Metal fast walk execution: " + describe(commands.error));
            done += steps;
            bool failed = false, active = false;
            for (std::uint32_t i = 0; i < job.lanes; ++i) {
                failed = failed || device_lanes[i].err != 0;
                active = active || device_lanes[i].active != 0;
            }
            if (failed || !active) break;
        }
        for (std::uint32_t i = 0; i < job.lanes; ++i) {
            f131::lane_out(device_lanes[i], walk, states[i]);
            errors[i] = int(device_lanes[i].err);
        }
    }
}
} // namespace

bool run_metal_fast(const Job& job, const JobExt& ext, std::vector<State>& states,
                    std::vector<int>& errors, bool& soft_mul32) {
    if (!f131::fast_curve(job.kind, job.field_bits, job.modulus)) return false;
    if (job.kind)
        run_metal_fast_field<f131::BinaryField<true>>(job, ext, "fast_binary", states,
                                                     errors, soft_mul32);
    else
        run_metal_fast_field<f131::PrimeField<std::uint32_t, 5>>(job, ext, "fast_prime5",
                                                                  states, errors, soft_mul32);
    return true;
}

std::vector<std::string> metal_devices() {
    std::vector<std::string> names;
    @autoreleasepool {
        for (id<MTLDevice> device in MTLCopyAllDevices())
            names.emplace_back(device.name.UTF8String);
    }
    return names;
}

void run_metal(const Job& job, std::vector<State>& states,
               std::vector<int>& errors) {
    @autoreleasepool {
        id<MTLDevice> device = select_device();
        NSError* error = nil;
        const std::string source = metal_source();
        id<MTLLibrary> library = [device
            newLibraryWithSource:[NSString stringWithUTF8String:source.c_str()]
                         options:[MTLCompileOptions new]
                           error:&error];
        require(library != nil, "Metal kernel compilation: " + describe(error));
        id<MTLFunction> function = [library newFunctionWithName:@"walk_kernel"];
        require(function != nil, "Metal kernel entry point is missing");
        id<MTLComputePipelineState> pipeline =
            [device newComputePipelineStateWithFunction:function error:&error];
        require(pipeline != nil, "Metal pipeline creation: " + describe(error));
        id<MTLCommandQueue> queue = [device newCommandQueue];
        require(queue != nil, "Metal command queue creation failed");

        const std::size_t state_bytes = sizeof(State) * job.lanes;
        const std::size_t error_bytes = sizeof(int) * job.lanes;
        // Shared buffers are coherent with the CPU on unified and discrete GPUs.
        id<MTLBuffer> job_buffer = [device newBufferWithBytes:&job
                                                       length:sizeof(Job)
                                                      options:MTLResourceStorageModeShared];
        id<MTLBuffer> state_buffer = [device newBufferWithBytes:states.data()
                                                         length:state_bytes
                                                        options:MTLResourceStorageModeShared];
        id<MTLBuffer> error_buffer = [device newBufferWithLength:error_bytes
                                                         options:MTLResourceStorageModeShared];
        require(job_buffer && state_buffer && error_buffer,
                "Metal buffer allocation failed");
        std::memset(error_buffer.contents, 0, error_bytes);

        const NSUInteger width =
            std::min<NSUInteger>(pipeline.maxTotalThreadsPerThreadgroup, 64);
        const MTLSize group = MTLSizeMake(width, 1, 1);
        const MTLSize grid = MTLSizeMake((job.lanes + width - 1) / width, 1, 1);
        const State* device_states = static_cast<const State*>(state_buffer.contents);
        const int* device_errors = static_cast<const int*>(error_buffer.contents);

        for (std::uint32_t done = 0; done < job.steps_per_round;) {
            const std::uint32_t steps =
                std::min(CHUNK_STEPS, job.steps_per_round - done);
            id<MTLCommandBuffer> commands = [queue commandBuffer];
            id<MTLComputeCommandEncoder> encoder = [commands computeCommandEncoder];
            [encoder setComputePipelineState:pipeline];
            [encoder setBuffer:job_buffer offset:0 atIndex:0];
            [encoder setBuffer:state_buffer offset:0 atIndex:1];
            [encoder setBuffer:error_buffer offset:0 atIndex:2];
            [encoder setBytes:&steps length:sizeof(steps) atIndex:3];
            [encoder dispatchThreadgroups:grid threadsPerThreadgroup:group];
            [encoder endEncoding];
            [commands commit];
            [commands waitUntilCompleted];
            require(commands.status == MTLCommandBufferStatusCompleted,
                    "Metal walk execution: " + describe(commands.error));
            done += steps;
            bool failed = false, active = false;
            for (std::uint32_t i = 0; i < job.lanes; ++i) {
                failed = failed || device_errors[i] != 0;
                active = active || device_states[i].active != 0;
            }
            // The caller reports a lane error; finished lanes need no more work.
            if (failed || !active) break;
        }
        std::memcpy(states.data(), device_states, state_bytes);
        std::memcpy(errors.data(), device_errors, error_bytes);
    }
}

} // namespace ecc
