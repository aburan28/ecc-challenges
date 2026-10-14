// Native full-width product and quotient/remainder circuits for the existing field.
// Included inside eccPacked131 after P131; host arithmetic keeps the reference path.
#pragma once
#if defined(__CUDA_ARCH__) && (ECC_PACKED_NATIVE_PRODUCT || ECC_PACKED_NATIVE_REDUCE)
ECC_DEV uint64_t nativeLo131(uint64_t a, uint64_t b, uint64_t c=0) {
    uint64_t r;
    asm("clmad.lo.u64 %0, %1, %2, %3;" : "=l"(r) : "l"(a), "l"(b), "l"(c));
    return r;
}
ECC_DEV uint64_t nativeHi131(uint64_t a, uint64_t b, uint64_t c=0) {
    uint64_t r;
    asm("clmad.hi.u64 %0, %1, %2, %3;" : "=l"(r) : "l"(a), "l"(b), "l"(c));
    return r;
}
ECC_DEV uint64_t nativeWord131(const uint32_t *a) {
    return uint64_t(a[0]) | (uint64_t(a[1]) << 32);
}
ECC_DEV void nativeProduct131(const P131 &a,const P131 &b,uint32_t *c) {
    const uint64_t a0=nativeWord131(a.v), a1=nativeWord131(a.v+2), a2=a.v[4]&7u;
    const uint64_t b0=nativeWord131(b.v), b1=nativeWord131(b.v+2), b2=b.v[4]&7u;
    const uint64_t l0=nativeLo131(a0,b0), h0=nativeHi131(a0,b0);
    const uint64_t l1=nativeLo131(a1,b1), h1=nativeHi131(a1,b1);
    const uint64_t lm=nativeLo131(a0^a1,b0^b1,l0^l1);
    const uint64_t hm=nativeHi131(a0^a1,b0^b1,h0^h1);
    const uint64_t r0=l0, r1=h0^lm;
#if ECC_PACKED_NATIVE_PRODUCT == 2
    // Only the low halves of the cross terms need native products. Their
    // high halves have two bits and are cheaper as short Boolean circuits.
    // Karatsuba reuses the already computed diagonal terms, so the complete
    // degree-260 product needs eight CLMAD instructions instead of fifteen.
    const uint32_t ma0=0u-(uint32_t(a2)&1u);
    const uint32_t ma1=0u-((uint32_t(a2)>>1)&1u);
    const uint32_t ma2=0u-((uint32_t(a2)>>2)&1u);
    const uint32_t mb1=0u-((uint32_t(b2)>>1)&1u);
    const uint32_t mb2=0u-((uint32_t(b2)>>2)&1u);
    const uint32_t p2=(uint32_t(b2)&ma0)^((uint32_t(b2)&ma1)<<1)^((uint32_t(b2)&ma2)<<2);
    const uint64_t l02=nativeLo131(a0^a2,b0^b2,l0)^p2;
    const uint64_t l12=nativeLo131(a1^a2,b1^b2,l1)^p2;
    const uint32_t h02=((a.v[1]>>31)&mb1)^((a.v[1]>>30)&mb2)^((b.v[1]>>31)&ma1)^((b.v[1]>>30)&ma2);
    const uint32_t h12=((a.v[3]>>31)&mb1)^((a.v[3]>>30)&mb2)^((b.v[3]>>31)&ma1)^((b.v[3]>>30)&ma2);
    const uint64_t r2=l1^hm^l02, r3=h1^h02^l12, r4=p2^h12;
#else
    uint64_t r2=nativeLo131(a0,b2,l1^hm);
    r2=nativeLo131(a2,b0,r2);
    uint64_t r3=nativeHi131(a0,b2,h1);
    r3=nativeHi131(a2,b0,r3);
    r3=nativeLo131(a1,b2,r3);
    r3=nativeLo131(a2,b1,r3);
    uint64_t r4=nativeHi131(a1,b2);
    r4=nativeHi131(a2,b1,r4);
    r4=nativeLo131(a2,b2,r4);
#endif
    c[0]=uint32_t(r0);c[1]=uint32_t(r0>>32);
    c[2]=uint32_t(r1);c[3]=uint32_t(r1>>32);
    c[4]=uint32_t(r2);c[5]=uint32_t(r2>>32);
    c[6]=uint32_t(r3);c[7]=uint32_t(r3>>32);c[8]=uint32_t(r4);
}
ECC_DEV P131 nativeReduce131(const uint32_t *h) {
    // Q = D + sum(D >> s), with D = H >> 131 and
    // s = 1,2,4,9,10,12,25,26,28,57,58,60,121,122,124.
    // Right convolution is the high half of a carryless product; the
    // following limb supplies its low half. Bits above H's degree 260
    // boundary are ignored, just as in the generated direct reducer.
    const uint64_t d0=(nativeWord131(h+4)>>3) | (uint64_t(h[6])<<61);
    const uint64_t d1=(nativeWord131(h+6)>>3) | (uint64_t(h[8])<<61);
    const uint64_t d2=(h[8]>>3)&3u;
    constexpr uint64_t C=0xd0d000d0000000d0ull, T=0xd0ull;
    uint64_t q0=nativeHi131(d0,C,d0);
    q0=nativeLo131(d1,C,q0);
    q0=nativeHi131(d1,T,q0);
    q0=nativeLo131(d2,T,q0);
    uint64_t q1=nativeHi131(d1,C,d1);
    q1=nativeLo131(d2,C,q1);
    const uint64_t q2=d2^(d2>>1);
    // F's three low limbs are 0xd, 0x1d0d000d0000000d, 5.
    // Only three result bits from the last limb survive; combine equal
    // low-three-bit factors before multiplying that limb.
    constexpr uint64_t F1=0x1d0d000d0000000dull;
    const uint64_t r0=nativeLo131(q0,0xd,nativeWord131(h));
    uint64_t r1=nativeHi131(q0,0xd,nativeWord131(h+2));
    r1=nativeLo131(q1,0xd,r1);
    r1=nativeLo131(q0,F1,r1);
    uint64_t r2=nativeHi131(q1,0xd,h[4]);
    r2=nativeHi131(q0,F1,r2);
    r2=nativeLo131(q0^q1^q2,5,r2);
    return P131{{uint32_t(r0),uint32_t(r0>>32),uint32_t(r1),uint32_t(r1>>32),uint32_t(r2)&7u}};
}
#endif
