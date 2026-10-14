// Batch inversion across whole warps. All 256 block threads must participate.
#pragma once
#include "packed131.h"
#if ECC_THREADS != 256
#error "Block inversion requires ECC_THREADS=256"
#endif
namespace eccPacked131 {
static constexpr int blockInverseReferenceWords131 = 15 * 5 * 32;
__device__ __forceinline__ P131 blockInverseReferenceLoad131(const uint32_t *tree, int node, int lane) {
    P131 value;
#pragma unroll
    for (int word=0;word<5;++word) value.v[word]=tree[(node*5+word)*32+lane];
    return value;
}
__device__ __forceinline__ void blockInverseReferenceStore131(uint32_t *tree, int node, int lane, P131 value) {
#pragma unroll
    for (int word=0;word<5;++word) tree[(node*5+word)*32+lane]=value.v[word];
}
// PairZero preserves a logical 16-slot batch split across t and t^128.
// It requires 256 additional words after the tree, reused with its barriers.
template<bool PairZero = false>
__device__ __forceinline__ P131 blockInverseReference131(P131 value, uint32_t *tree) {
    const int lane=threadIdx.x&31, warp=threadIdx.x>>5;
    const bool zero=(value.v[0]|value.v[1]|value.v[2]|value.v[3]|value.v[4])==0;
    if constexpr (PairZero) tree[blockInverseReferenceWords131 + threadIdx.x] = unsigned(zero);
    // Isolate zero leaves: inv(0) stays zero without poisoning other batches.
    blockInverseReferenceStore131(tree,7+warp,lane,zero?P131{{1,0,0,0,0}}:value);
    __syncthreads();
    bool outputZero = zero;
    if constexpr (PairZero)
        outputZero = outputZero || tree[blockInverseReferenceWords131 + (threadIdx.x ^ 128)];
#pragma unroll
    for (int count=4;count;count>>=1) {
        if (warp<count) {
            const int node=count-1+warp;
            P131 left=blockInverseReferenceLoad131(tree,2*node+1,lane);
            P131 right=blockInverseReferenceLoad131(tree,2*node+2,lane);
            blockInverseReferenceStore131(tree,node,lane,mulPolynomial131(left,right));
        }
        __syncthreads();
    }
    if (warp==0) {
        P131 root=blockInverseReferenceLoad131(tree,0,lane);
        blockInverseReferenceStore131(tree,0,lane,toPolynomial131(inv131(fromPolynomial131(root))));
    }
    __syncthreads();
#pragma unroll
    for (int count=1;count<=4;count<<=1) {
        if (warp<count) {
            const int node=count-1+warp;
            const P131 parent=blockInverseReferenceLoad131(tree,node,lane);
            const P131 left=blockInverseReferenceLoad131(tree,2*node+1,lane);
            const P131 right=blockInverseReferenceLoad131(tree,2*node+2,lane);
            const auto pair=mulPolynomialPair131(parent,right,left);
            blockInverseReferenceStore131(tree,2*node+1,lane,pair.first);
            blockInverseReferenceStore131(tree,2*node+2,lane,pair.second);
        }
        __syncthreads();
    }
    const P131 result=blockInverseReferenceLoad131(tree,7+warp,lane);
    // Each thread owns its leaf, so a following invocation can safely store
    // that leaf before its initial barrier without racing another reader.
    return outputZero?P131{}:result;
}
} // namespace eccPacked131
