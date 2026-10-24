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
    require(job.reserved == 0,
            "the negation-class walk is not implemented for the Metal backend");
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
