//! Prover cost vs. the native walk: `cargo run --release --example bench`.
use std::time::Instant;

use eccstark::*;

fn main() {
    let mut s = 0x9E37_79B9_7F4A_7C15u64;
    let mut felt = || {
        s ^= s << 13;
        s ^= s >> 7;
        s ^= s << 17;
        Felt::new(s % Q)
    };
    let p = WalkParams { dp_bits: 30, mults: (0..32).map(|_| (felt(), felt())).collect() };
    let coeffs: Vec<(u64, u64)> = (0..32).map(|i| (i, i)).collect();
    let start = (felt(), felt());

    // Native walk speed (dp_bits = 30 so it does not stop early).
    let n = 2_000_000;
    let t = Instant::now();
    let _ = walk_to_dp(&p, &coeffs, u64::MAX, start, 0, 0, n, 0);
    let native = t.elapsed().as_secs_f64() / n as f64;
    println!("native walk: {:.0} ns/step (one field inversion per step, no batching)", native * 1e9);

    println!("{:>8} {:>10} {:>10} {:>12} {:>10} {:>12}", "steps", "prove_s", "verify_ms",
             "proof_bytes", "ns/step", "x_native");
    for log in [8, 10, 12, 14, 16] {
        let steps = (1usize << log) - 2;
        let t = Instant::now();
        let (proof, end) = prove(&p, start, steps, false).expect("prove");
        let pt = t.elapsed().as_secs_f64();
        let bytes = proof.to_bytes().len();
        let t = Instant::now();
        verify(proof, PublicInputs { start, end, steps, end_is_dp: false, params: p.clone() })
            .expect("verify");
        let vt = t.elapsed().as_secs_f64();
        println!("{:>8} {:>10.3} {:>10.2} {:>12} {:>10.0} {:>12.0}", steps, pt, vt * 1e3, bytes,
                 pt / steps as f64 * 1e9, pt / steps as f64 / native);
    }
}
