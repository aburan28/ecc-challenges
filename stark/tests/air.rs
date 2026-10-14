//! Completeness and soundness checks for the walk AIR.
//!
//! Run with `cargo test --release`: in debug builds Winterfell refuses to
//! prove invalid traces at all, which would hide the verifier-side checks the
//! forgery tests exercise.

use eccstark::*;
use winterfell::math::FieldElement;

struct Rng(u64);
impl Rng {
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        self.0
    }
    fn felt(&mut self) -> Felt {
        Felt::new(self.next() % Q)
    }
}

// The AIR never uses the curve equation (that is checked outside the proof),
// so random multipliers exercise every constraint.
fn params(seed: u64, dp_bits: u32) -> WalkParams {
    let mut rng = Rng(seed);
    WalkParams { dp_bits, mults: (0..32).map(|_| (rng.felt(), rng.felt())).collect() }
}

fn coeffs() -> Vec<(u64, u64)> {
    (0..32).map(|i| (i as u64 + 1, 2 * i as u64 + 3)).collect()
}

/// A start point and the honest walk from it to its first DP.
fn honest(p: &WalkParams, seed: u64) -> (Point, WalkOutcome) {
    let mut rng = Rng(seed);
    loop {
        let start = (rng.felt(), rng.felt());
        if let Some(w) = walk_to_dp(p, &coeffs(), u64::MAX - 58, start, 0, 0, 1 << 12, 0) {
            if w.length >= 8 {
                return (start, w);
            }
        }
    }
}

fn pi(p: &WalkParams, start: Point, end: Point, steps: usize, end_is_dp: bool) -> PublicInputs {
    PublicInputs { start, end, steps, end_is_dp, params: p.clone() }
}

/// Recompute rows `from..` of a forged trace by walking honestly from row `from`.
fn rewalk(cols: &mut [Vec<Felt>], p: &WalkParams, from: usize) {
    let n = cols[0].len();
    for row in from..n {
        let x = cols[0][row].as_int();
        refill_row(cols, p, row, x);
        if row + 1 < n {
            let idx = p.index(cols[0][row]);
            force_step(cols, row, p.mults[idx]);
        }
    }
}

fn end_of(cols: &[Vec<Felt>], s: usize) -> Point {
    (cols[0][s], cols[1][s])
}

#[test]
fn honest_walk_verifies() {
    let p = params(1, 6);
    let (start, w) = honest(&p, 2);
    let (proof, end) = prove(&p, start, w.length, true).unwrap();
    assert_eq!(end, w.end);
    let bytes = proof.to_bytes();
    println!("steps {} proof {} bytes", w.length, bytes.len());
    verify(winterfell::Proof::from_bytes(&bytes).unwrap(), pi(&p, start, end, w.length, true))
        .unwrap();
}

#[test]
fn honest_segment_verifies() {
    let p = params(3, 10);
    let mut rng = Rng(9);
    let start = (rng.felt(), rng.felt());
    // A segment ending at an ordinary point; retry until no early DP.
    for steps in [1usize, 2, 7, 30, 61] {
        match prove(&p, start, steps, false) {
            Ok((proof, end)) => verify(proof, pi(&p, start, end, steps, false)).unwrap(),
            Err(e) => assert!(e.contains("early"), "{e}"),
        }
    }
}

#[test]
fn wrong_public_inputs_fail() {
    let p = params(4, 6);
    let (start, w) = honest(&p, 5);
    let (proof, end) = prove(&p, start, w.length, true).unwrap();
    let bad_end = (end.0, end.1 + Felt::ONE);
    let bad_start = (start.0 + Felt::ONE, start.1);
    let mut other = p.clone();
    other.mults[0].0 += Felt::ONE;
    for (name, pi) in [
        ("end", pi(&p, start, bad_end, w.length, true)),
        ("start (another key's walk)", pi(&p, bad_start, end, w.length, true)),
        ("multipliers (another job)", pi(&other, start, end, w.length, true)),
        ("steps", pi(&p, start, end, w.length - 1, true)),
    ] {
        assert!(verify(proof.clone(), pi).is_err(), "accepted wrong {name}");
    }
}

#[test]
fn wrong_multiplier_step_is_rejected() {
    // The prover takes one step with the wrong R and walks honestly after it.
    let p = params(6, 6);
    let (start, w) = honest(&p, 7);
    let s = w.length;
    let mut cols = trace_columns(&p, start, s).unwrap();
    let idx = p.index(cols[0][3]);
    force_step(&mut cols, 3, p.mults[(idx + 1) % 32]);
    rewalk(&mut cols, &p, 4);
    let end = end_of(&cols, s);
    let proof = prove_trace(winterfell::TraceTable::init(cols), pi(&p, start, end, s, false));
    assert!(verify(proof.unwrap(), pi(&p, start, end, s, false)).is_err());
}

#[test]
fn linear_sweep_is_rejected() {
    // The cheater from docs/DESIGN.md: every step adds the same point.
    let p = params(8, 6);
    let (start, w) = honest(&p, 9);
    let s = w.length;
    let mut cols = trace_columns(&p, start, s).unwrap();
    for row in 0..cols[0].len() - 1 {
        let x = cols[0][row].as_int();
        refill_row(&mut cols, &p, row, x);
        force_step(&mut cols, row, p.mults[0]);
    }
    let end = end_of(&cols, s);
    let proof = prove_trace(winterfell::TraceTable::init(cols), pi(&p, start, end, s, false));
    assert!(verify(proof.unwrap(), pi(&p, start, end, s, false)).is_err());
}

#[test]
fn non_canonical_bits_are_rejected() {
    // x < 2^32 - 1 has a second 64-bit representation x + q; using it changes
    // which multiplier is selected. The canonicity constraint must catch it.
    let p = params(10, 4);
    let mut rng = Rng(11);
    let start = (Felt::new(0x1234_567F), rng.felt());
    let s = 5;
    let mut cols = trace_columns(&p, start, s).unwrap();
    let alt = start.0.as_int() + Q;
    assert_ne!(p.index(start.0), ((alt >> p.dp_bits) as usize) & 31);
    refill_row(&mut cols, &p, 0, alt);
    let idx = ((alt >> p.dp_bits) as usize) & 31;
    force_step(&mut cols, 0, p.mults[idx]);
    rewalk(&mut cols, &p, 1);
    let end = end_of(&cols, s);
    let proof = prove_trace(winterfell::TraceTable::init(cols), pi(&p, start, end, s, false));
    assert!(verify(proof.unwrap(), pi(&p, start, end, s, false)).is_err());
}

#[test]
fn walking_past_a_dp_is_rejected() {
    // Claiming a longer walk (more credit) by not stopping at the first DP.
    let p = params(12, 6);
    let (start, w) = honest(&p, 13);
    let s = w.length + 9;
    assert!(prove(&p, start, s, false).is_err(), "honest prover must refuse");
    let cols = trace_columns(&p, start, s).unwrap();
    let end = end_of(&cols, s);
    let proof = prove_trace(winterfell::TraceTable::init(cols), pi(&p, start, end, s, false));
    assert!(verify(proof.unwrap(), pi(&p, start, end, s, false)).is_err());
}

#[test]
fn non_dp_end_claimed_as_dp_is_rejected() {
    let p = params(14, 10);
    let mut rng = Rng(15);
    let start = (rng.felt(), rng.felt());
    let s = 6;
    let cols = trace_columns(&p, start, s).unwrap();
    let end = end_of(&cols, s);
    assert!(!p.is_dp(end.0));
    let proof = prove_trace(winterfell::TraceTable::init(cols), pi(&p, start, end, s, true));
    assert!(verify(proof.unwrap(), pi(&p, start, end, s, true)).is_err());
}

#[test]
fn foreign_proof_options_are_rejected_without_panicking() {
    // Found in review: a proof claiming blowup 2 passed the conjectured-security
    // check and then panicked the verifier while it built the AIR.
    use winterfell::{BatchingMethod, FieldExtension, ProofOptions};
    let p = params(16, 8);
    let start = (Felt::new(7), Felt::new(11));
    let (proof, end) = prove(&p, start, 1, false).unwrap();
    let opts = ProofOptions::new(97, 2, 0, FieldExtension::Quadratic, 8, 31,
                                 BatchingMethod::Linear, BatchingMethod::Linear);
    let mut forged = proof.clone();
    forged.context = winter_air::proof::Context::new::<Felt>(
        proof.context.trace_info().clone(), opts, proof.context.num_constraints());
    let r = verify(forged, pi(&p, start, end, 1, false));
    assert!(r.unwrap_err().contains("unsupported proof options"));
}
