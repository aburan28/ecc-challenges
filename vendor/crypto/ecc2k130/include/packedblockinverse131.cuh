// Batch inversion across whole warps. All 256 block threads must participate.
#pragma once
#include "packed131.h"
#ifndef ECC_PACKED_LOGICAL_PAIR_INVERSE
#define ECC_PACKED_LOGICAL_PAIR_INVERSE 0
#endif
#if ECC_PACKED_LOGICAL_PAIR_INVERSE < 0 || ECC_PACKED_LOGICAL_PAIR_INVERSE > 2
#error "LOGICAL_PAIR_INVERSE must be 0, 1 or 2"
#endif
#if ECC_THREADS != 256
#error "Block inversion requires ECC_THREADS=256"
#endif
namespace eccPacked131 {
static constexpr int blockInverseWords131 =
    (ECC_PACKED_LOGICAL_PAIR_INVERSE == 2 ? 7 :
     ECC_PACKED_LOGICAL_PAIR_INVERSE == 1 ? 8 : 15) * 5 * 32;
static constexpr int blockInverseFlagWords131 = ECC_PACKED_LOGICAL_PAIR_INVERSE ? 0 : 256;
__device__ __forceinline__ P131 blockInverseLoad131(const uint32_t *tree, int node, int lane) {
    P131 value;
#pragma unroll
    for (int word=0;word<5;++word) value.v[word]=tree[(node*5+word)*32+lane];
    return value;
}
__device__ __forceinline__ void blockInverseStore131(uint32_t *tree, int node, int lane, P131 value) {
#pragma unroll
    for (int word=0;word<5;++word) tree[(node*5+word)*32+lane]=value.v[word];
}
// PairZero preserves a logical 16-slot batch split across t and t^128.
// Flag scratch is included in blockInverseFlagWords131; retained mode tags the gather tail.
template<bool PairZero = false>
__device__ __forceinline__ P131 blockInverse131(P131 value, uint32_t *tree) {
#if ECC_PACKED_LOGICAL_PAIR_INVERSE == 2
    const int lane=threadIdx.x&31, warp=threadIdx.x>>5;
    const bool lower=warp<4;
    const int pair=warp&3;
    const int parentNode=3+((pair+1)&3);
    bool zeroOwn=(value.v[0]|value.v[1]|value.v[2]|value.v[3]|value.v[4])==0;
    value=zeroOwn?P131{{1,0,0,0,0}}:value;
    // Upper inputs occupy nodes 0..3. Lower inputs stay local.
    if (!lower) {
        P131 tagged=value;
        tagged.v[4]|=unsigned(zeroOwn)<<3;
        blockInverseStore131(tree,pair,lane,tagged);
    }
    __syncthreads();
    P131 sibling{};
    bool zeroPartner=false;
    if (lower) {
        sibling=blockInverseLoad131(tree,pair,lane);
        zeroPartner=(sibling.v[4]>>3)&1u;
        sibling.v[4]&=7u;
        if constexpr (PairZero) zeroOwn=zeroPartner=zeroOwn||zeroPartner;
        // Pair 3 reads then overwrites node 3 in the same warp. Every other
        // gather source and product destination is disjoint.
        blockInverseStore131(tree,parentNode,lane,mulPolynomial131(value,sibling));
    }
    __syncthreads();
#pragma unroll
    for (int count=2;count;count>>=1) {
        if (warp<count) {
            const int node=count-1+warp;
            const P131 left=blockInverseLoad131(tree,2*node+1,lane);
            const P131 right=blockInverseLoad131(tree,2*node+2,lane);
            blockInverseStore131(tree,node,lane,mulPolynomial131(left,right));
        }
        __syncthreads();
    }
    if (warp==0) {
        const P131 root=blockInverseLoad131(tree,0,lane);
        blockInverseStore131(tree,0,lane,toPolynomial131(inv131(fromPolynomial131(root))));
    }
    __syncthreads();
#pragma unroll
    for (int count=1;count<=2;count<<=1) {
        if (warp<count) {
            const int node=count-1+warp;
            const P131 parent=blockInverseLoad131(tree,node,lane);
            const P131 left=blockInverseLoad131(tree,2*node+1,lane);
            const P131 right=blockInverseLoad131(tree,2*node+2,lane);
            const auto children=mulPolynomialPair131(parent,right,left);
            blockInverseStore131(tree,2*node+1,lane,children.first);
            blockInverseStore131(tree,2*node+2,lane,children.second);
        }
        __syncthreads();
    }
    P131 result{};
    if (lower) {
        const P131 parent=blockInverseLoad131(tree,parentNode,lane);
        auto leaves=mulPolynomialPair131(parent,sibling,value);
        if (zeroOwn) leaves.first=P131{};
        if (zeroPartner) leaves.second=P131{};
        result=leaves.first;
        // Pair 3 consumes and overwrites node 3 in the same warp. Lower
        // results remain in registers; only upper results use shared memory.
        blockInverseStore131(tree,pair,lane,leaves.second);
    }
    __syncthreads();
    // Upper threads next overwrite only the node they have already read.
    if (!lower) result=blockInverseLoad131(tree,pair,lane);
    return result;
#elif ECC_PACKED_LOGICAL_PAIR_INVERSE == 1
    const int lane=threadIdx.x&31, warp=threadIdx.x>>5;
    const bool lower=warp<4;
    const int pair=warp&3;
    const int upperNode=pair==3?7:pair;
    const int ownNode=lower?3+pair:upperNode;
    bool zeroOwn=(value.v[0]|value.v[1]|value.v[2]|value.v[3]|value.v[4])==0;
    value=zeroOwn?P131{{1,0,0,0,0}}:value;
    // Only upper partners need shared publication. Lower inputs stay local.
    if (!lower) {
        P131 tagged=value;
        tagged.v[4]|=unsigned(zeroOwn)<<3;
        blockInverseStore131(tree,upperNode,lane,tagged);
    }
    __syncthreads();
    P131 sibling{};
    bool zeroPartner=false;
    if (lower) {
        sibling=blockInverseLoad131(tree,upperNode,lane);
        zeroPartner=(sibling.v[4]>>3)&1u;
        sibling.v[4]&=7u;
        if constexpr (PairZero) zeroOwn=zeroPartner=zeroOwn||zeroPartner;
        // Gather nodes 0,1,2,7 are disjoint from these product destinations.
        blockInverseStore131(tree,3+pair,lane,mulPolynomial131(value,sibling));
    }
    __syncthreads();
#pragma unroll
    for (int count=2;count;count>>=1) {
        if (warp<count) {
            const int node=count-1+warp;
            const P131 left=blockInverseLoad131(tree,2*node+1,lane);
            const P131 right=blockInverseLoad131(tree,2*node+2,lane);
            blockInverseStore131(tree,node,lane,mulPolynomial131(left,right));
        }
        __syncthreads();
    }
    if (warp==0) {
        const P131 root=blockInverseLoad131(tree,0,lane);
        blockInverseStore131(tree,0,lane,toPolynomial131(inv131(fromPolynomial131(root))));
    }
    __syncthreads();
#pragma unroll
    for (int count=1;count<=2;count<<=1) {
        if (warp<count) {
            const int node=count-1+warp;
            const P131 parent=blockInverseLoad131(tree,node,lane);
            const P131 left=blockInverseLoad131(tree,2*node+1,lane);
            const P131 right=blockInverseLoad131(tree,2*node+2,lane);
            const auto children=mulPolynomialPair131(parent,right,left);
            blockInverseStore131(tree,2*node+1,lane,children.first);
            blockInverseStore131(tree,2*node+2,lane,children.second);
        }
        __syncthreads();
    }
    if (lower) {
        const P131 parent=blockInverseLoad131(tree,3+pair,lane);
        auto result=mulPolynomialPair131(parent,sibling,value);
        if (zeroOwn) result.first=P131{};
        if (zeroPartner) result.second=P131{};
        // Each parent has one reader/writer; upper destinations are now unused.
        blockInverseStore131(tree,3+pair,lane,result.first);
        blockInverseStore131(tree,upperNode,lane,result.second);
    }
    __syncthreads();
    // Upper threads next publish only after reading this same owned node.
    // Lower destinations are not overwritten before the next initial barrier.
    return blockInverseLoad131(tree,ownNode,lane);
#else
    const int lane=threadIdx.x&31, warp=threadIdx.x>>5;
    const bool zero=(value.v[0]|value.v[1]|value.v[2]|value.v[3]|value.v[4])==0;
    if constexpr (PairZero) tree[blockInverseWords131 + threadIdx.x] = unsigned(zero);
    // Isolate zero leaves: inv(0) stays zero without poisoning other batches.
    blockInverseStore131(tree,7+warp,lane,zero?P131{{1,0,0,0,0}}:value);
    __syncthreads();
    bool outputZero = zero;
    if constexpr (PairZero)
        outputZero = outputZero || tree[blockInverseWords131 + (threadIdx.x ^ 128)];
#pragma unroll
    for (int count=4;count;count>>=1) {
        if (warp<count) {
            const int node=count-1+warp;
            P131 left=blockInverseLoad131(tree,2*node+1,lane);
            P131 right=blockInverseLoad131(tree,2*node+2,lane);
            blockInverseStore131(tree,node,lane,mulPolynomial131(left,right));
        }
        __syncthreads();
    }
    if (warp==0) {
        P131 root=blockInverseLoad131(tree,0,lane);
        blockInverseStore131(tree,0,lane,toPolynomial131(inv131(fromPolynomial131(root))));
    }
    __syncthreads();
#pragma unroll
    for (int count=1;count<=4;count<<=1) {
        if (warp<count) {
            const int node=count-1+warp;
            const P131 parent=blockInverseLoad131(tree,node,lane);
            const P131 left=blockInverseLoad131(tree,2*node+1,lane);
            const P131 right=blockInverseLoad131(tree,2*node+2,lane);
            const auto pair=mulPolynomialPair131(parent,right,left);
            blockInverseStore131(tree,2*node+1,lane,pair.first);
            blockInverseStore131(tree,2*node+2,lane,pair.second);
        }
        __syncthreads();
    }
    const P131 result=blockInverseLoad131(tree,7+warp,lane);
    // Each thread owns its leaf, so a following invocation can safely store
    // that leaf before its initial barrier without racing another reader.
    return outputZero?P131{}:result;
#endif
}
} // namespace eccPacked131
