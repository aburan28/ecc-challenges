//! STARK proofs that a Pollard-rho walk was computed correctly.
//!
//! The curve is `y^2 = x^3 + a x + b` over the Goldilocks field
//! `F_q`, `q = 2^64 - 2^32 + 1`, which is also the STARK's base field, so each
//! walk step is a handful of native constraints instead of emulated
//! big-integer arithmetic.
//!
//! Statement proven (public inputs: start, end, steps `s`, `end_is_dp`,
//! `dp_bits`, the `r` multipliers `R_j`):
//!
//!   `P_0 = start`, `P_{i+1} = P_i + R_{idx(P_i)}` for `i < s`, `P_s = end`,
//!   no `P_i` with `1 <= i < s` is distinguished, and if `end_is_dp`, `P_s` is.
//!
//! where `idx(P) = (x >> dp_bits) mod r` and "distinguished" means the low
//! `dp_bits` bits of `x` are zero, both on the canonical integer `x < q`.
//! This is exactly `eccpool/walk.py` restricted to the Goldilocks field.
//!
//! Trace layout, one row per walk point (row `i` holds `P_i`):
//!
//! | col | meaning |
//! |-----|---------|
//! | 0, 1 | x, y |
//! | 2 | lambda, the chord slope of `P_i + R` |
//! | 3, 4 | `R_{idx(P_i)}` (selected multiplier) |
//! | 5 | `1 / (R.x - x)`: proves the chord formula applies (no doubling/infinity) |
//! | 6 | low `dp_bits` bits of x as an integer |
//! | 7 | `1 / col6` where the point must not be distinguished |
//! | 8 | witness for the canonical-decomposition check |
//! | 9..73 | the 64 bits of x, little endian |

use winterfell::{
    crypto::{hashers::Blake3_256, DefaultRandomCoin, MerkleTree},
    math::{fields::f64::BaseElement, FieldElement, StarkField, ToElements},
    matrix::ColMatrix,
    AcceptableOptions, Air, AirContext, Assertion, AuxRandElements, BatchingMethod,
    CompositionPoly, CompositionPolyTrace, ConstraintCompositionCoefficients,
    DefaultConstraintCommitment, DefaultConstraintEvaluator, DefaultTraceLde, EvaluationFrame,
    FieldExtension, PartitionOptions, Proof, ProofOptions, Prover, StarkDomain, TraceInfo,
    Trace, TracePolyTable, TraceTable, TransitionConstraintDegree,
};

pub type Felt = BaseElement;

const COL_X: usize = 0;
const COL_Y: usize = 1;
const COL_LAM: usize = 2;
const COL_RX: usize = 3;
const COL_RY: usize = 4;
const COL_INVDX: usize = 5;
const COL_SLOW: usize = 6;
const COL_INVS: usize = 7;
const COL_W: usize = 8;
const COL_B0: usize = 9;
pub const TRACE_WIDTH: usize = COL_B0 + 64;

/// Field modulus as an integer.
pub const Q: u64 = BaseElement::MODULUS;

// ------------------------------------------------------------------ curve

pub type Point = (Felt, Felt);

/// Chord addition `P + R`; `None` when `P.x == R.x` (doubling or infinity),
/// which an honest walk treats as a failed walk (probability ~ r/n per step).
#[inline]
pub fn add_chord(p: Point, r: Point) -> Option<(Point, Felt, Felt)> {
    let dx = r.0 - p.0;
    if dx == Felt::ZERO {
        return None;
    }
    let inv = dx.inv();
    let lam = (r.1 - p.1) * inv;
    let x3 = lam * lam - p.0 - r.0;
    let y3 = lam * (p.0 - x3) - p.1;
    Some(((x3, y3), lam, inv))
}

#[derive(Clone, Debug)]
pub struct WalkParams {
    pub dp_bits: u32,
    /// `R_j = (x, y)` for `j < r`; `r` a power of two.
    pub mults: Vec<Point>,
}

impl WalkParams {
    pub fn r(&self) -> usize {
        self.mults.len()
    }
    pub fn log_r(&self) -> u32 {
        self.mults.len().trailing_zeros()
    }
    #[inline]
    pub fn index(&self, x: Felt) -> usize {
        ((x.as_int() >> self.dp_bits) as usize) & (self.r() - 1)
    }
    #[inline]
    pub fn is_dp(&self, x: Felt) -> bool {
        x.as_int() & ((1u64 << self.dp_bits) - 1) == 0
    }
    pub fn validate(&self) -> Result<(), String> {
        let r = self.mults.len();
        if r < 2 || !r.is_power_of_two() || r > 64 {
            return Err("r must be a power of two in [2, 64]".into());
        }
        if self.dp_bits < 1 || self.dp_bits + self.log_r() > 60 {
            return Err("dp_bits out of range".into());
        }
        Ok(())
    }
}

/// Trace length used to prove `steps` steps (row `steps` must not be the last row).
pub fn trace_length(steps: usize) -> usize {
    (steps + 2).next_power_of_two().max(8)
}

/// Walk `steps` steps from `start` and record the trace. Rows after `steps`
/// continue the walk so every transition constraint stays satisfiable.
pub fn build_trace(params: &WalkParams, start: Point, steps: usize) -> Result<TraceTable<Felt>, String> {
    let n = trace_length(steps);
    let mut cols = vec![vec![Felt::ZERO; n]; TRACE_WIDTH];
    let mut p = start;
    let dmask = (1u64 << params.dp_bits) - 1;
    for row in 0..n {
        let xi = p.0.as_int();
        cols[COL_X][row] = p.0;
        cols[COL_Y][row] = p.1;
        for b in 0..64 {
            cols[COL_B0 + b][row] = Felt::new((xi >> b) & 1);
        }
        let slow = xi & dmask;
        cols[COL_SLOW][row] = Felt::new(slow);
        cols[COL_INVS][row] = if slow == 0 { Felt::ZERO } else { Felt::new(slow).inv() };
        let hi_ones = (xi >> 32).count_ones() as u64;
        let z = Felt::new(32 - hi_ones);
        cols[COL_W][row] = if z == Felt::ZERO { Felt::ZERO } else { z.inv() };
        let r = params.mults[params.index(p.0)];
        cols[COL_RX][row] = r.0;
        cols[COL_RY][row] = r.1;
        if row + 1 == n {
            // Last row: no transition out of it; fill the slope columns with anything.
            break;
        }
        match add_chord(p, r) {
            Some((next, lam, inv)) => {
                cols[COL_LAM][row] = lam;
                cols[COL_INVDX][row] = inv;
                p = next;
            }
            None if row >= steps => {
                // Past the end of the statement but the padding walk degenerated:
                // jump to a fresh honest-looking state by restarting from R.
                return Err("degenerate padding walk; retry with another nonce".into());
            }
            None => return Err("walk hit a doubling/infinity case".into()),
        }
    }
    Ok(TraceTable::init(cols))
}

// -------------------------------------------------------------------- AIR

#[derive(Clone, Debug)]
pub struct PublicInputs {
    pub start: Point,
    pub end: Point,
    pub steps: usize,
    pub end_is_dp: bool,
    pub params: WalkParams,
}

impl ToElements<Felt> for PublicInputs {
    fn to_elements(&self) -> Vec<Felt> {
        let mut v = vec![
            self.start.0,
            self.start.1,
            self.end.0,
            self.end.1,
            Felt::new(self.steps as u64),
            Felt::new(self.end_is_dp as u64),
            Felt::new(self.params.dp_bits as u64),
            Felt::new(self.params.r() as u64),
        ];
        for m in &self.params.mults {
            v.push(m.0);
            v.push(m.1);
        }
        v
    }
}

pub struct WalkAir {
    context: AirContext<Felt>,
    pi: PublicInputs,
}

impl Air for WalkAir {
    type BaseField = Felt;
    type PublicInputs = PublicInputs;

    fn new(trace_info: TraceInfo, pi: PublicInputs, options: ProofOptions) -> Self {
        assert_eq!(TRACE_WIDTH, trace_info.width());
        let n = trace_info.length();
        let log_r = pi.params.log_r() as usize;
        let mut degrees = Vec::with_capacity(75);
        degrees.extend((0..64).map(|_| TransitionConstraintDegree::new(2))); // booleans
        degrees.push(TransitionConstraintDegree::new(1)); // x = sum bits
        degrees.push(TransitionConstraintDegree::new(3)); // z (1 - z w) = 0
        degrees.push(TransitionConstraintDegree::new(3)); // (1 - z w) lo32 = 0
        degrees.push(TransitionConstraintDegree::new(log_r)); // rx = select
        degrees.push(TransitionConstraintDegree::new(log_r)); // ry = select
        degrees.push(TransitionConstraintDegree::new(2)); // invdx (rx - x) = 1
        degrees.push(TransitionConstraintDegree::new(2)); // lambda
        degrees.push(TransitionConstraintDegree::new(2)); // x'
        degrees.push(TransitionConstraintDegree::new(2)); // y'
        degrees.push(TransitionConstraintDegree::new(1)); // slow = low bits
        degrees.push(TransitionConstraintDegree::with_cycles(2, vec![n])); // not a DP (masked)
        let num_assertions = 4 + pi.end_is_dp as usize;
        WalkAir { context: AirContext::new(trace_info, degrees, num_assertions, options), pi }
    }

    fn context(&self) -> &AirContext<Felt> {
        &self.context
    }

    fn evaluate_transition<E: FieldElement + From<Felt>>(
        &self,
        frame: &EvaluationFrame<E>,
        periodic: &[E],
        out: &mut [E],
    ) {
        let cur = frame.current();
        let nxt = frame.next();
        let one = E::ONE;
        let bits = &cur[COL_B0..COL_B0 + 64];

        // Booleans and the integer value of the bits.
        let mut acc = E::ZERO;
        let mut pow = E::ONE;
        let two = E::from(2u32);
        let mut lo32 = E::ZERO;
        let mut slow = E::ZERO;
        let mut hi_count = E::ZERO;
        for (i, &b) in bits.iter().enumerate() {
            out[i] = b * (b - one);
            acc += b * pow;
            if i < 32 {
                lo32 += b * pow;
            } else {
                hi_count += b;
            }
            if (i as u32) < self.pi.params.dp_bits {
                slow += b * pow;
            }
            pow *= two;
        }
        out[64] = cur[COL_X] - acc;

        // Canonical: integer < q, i.e. not (hi 32 bits all one and lo32 != 0).
        let z = E::from(32u32) - hi_count;
        let flag = one - z * cur[COL_W];
        out[65] = z * flag;
        out[66] = flag * lo32;

        // Multiplier selection by the r-index bits (multilinear interpolation).
        let d = self.pi.params.dp_bits as usize;
        let log_r = self.pi.params.log_r() as usize;
        let mut vx: Vec<E> = self.pi.params.mults.iter().map(|m| E::from(m.0)).collect();
        let mut vy: Vec<E> = self.pi.params.mults.iter().map(|m| E::from(m.1)).collect();
        for t in 0..log_r {
            let b = bits[d + t];
            let half = vx.len() / 2;
            for j in 0..half {
                vx[j] = vx[2 * j] + b * (vx[2 * j + 1] - vx[2 * j]);
                vy[j] = vy[2 * j] + b * (vy[2 * j + 1] - vy[2 * j]);
            }
            vx.truncate(half);
            vy.truncate(half);
        }
        out[67] = cur[COL_RX] - vx[0];
        out[68] = cur[COL_RY] - vy[0];

        // Chord addition P' = P + R.
        let x = cur[COL_X];
        let y = cur[COL_Y];
        let lam = cur[COL_LAM];
        let dx = cur[COL_RX] - x;
        out[69] = cur[COL_INVDX] * dx - one;
        out[70] = lam * dx - (cur[COL_RY] - y);
        out[71] = nxt[COL_X] - (lam * lam - x - cur[COL_RX]);
        out[72] = nxt[COL_Y] - (lam * (x - nxt[COL_X]) - y);

        // Distinguished-ness.
        out[73] = cur[COL_SLOW] - slow;
        out[74] = periodic[0] * (nxt[COL_SLOW] * nxt[COL_INVS] - one);
    }

    fn get_assertions(&self) -> Vec<Assertion<Felt>> {
        let s = self.pi.steps;
        let mut a = vec![
            Assertion::single(COL_X, 0, self.pi.start.0),
            Assertion::single(COL_Y, 0, self.pi.start.1),
            Assertion::single(COL_X, s, self.pi.end.0),
            Assertion::single(COL_Y, s, self.pi.end.1),
        ];
        if self.pi.end_is_dp {
            a.push(Assertion::single(COL_SLOW, s, Felt::ZERO));
        }
        a
    }

    fn get_periodic_column_values(&self) -> Vec<Vec<Felt>> {
        // mask[i] = 1 when the transition i -> i+1 lands on an interior step
        // (1 <= i+1 <= steps-1), where the point must not be distinguished.
        let n = self.trace_length();
        let s = self.pi.steps;
        vec![(0..n).map(|i| Felt::new((i + 2 <= s) as u64)).collect()]
    }
}

// ----------------------------------------------------------------- prover

pub struct WalkProver {
    options: ProofOptions,
    pi: PublicInputs,
}

impl Prover for WalkProver {
    type BaseField = Felt;
    type Air = WalkAir;
    type Trace = TraceTable<Felt>;
    type HashFn = Blake3_256<Felt>;
    type VC = MerkleTree<Self::HashFn>;
    type RandomCoin = DefaultRandomCoin<Self::HashFn>;
    type TraceLde<E: FieldElement<BaseField = Felt>> = DefaultTraceLde<E, Self::HashFn, Self::VC>;
    type ConstraintCommitment<E: FieldElement<BaseField = Felt>> =
        DefaultConstraintCommitment<E, Self::HashFn, Self::VC>;
    type ConstraintEvaluator<'a, E: FieldElement<BaseField = Felt>> =
        DefaultConstraintEvaluator<'a, Self::Air, E>;

    fn get_pub_inputs(&self, _trace: &Self::Trace) -> PublicInputs {
        self.pi.clone()
    }

    fn options(&self) -> &ProofOptions {
        &self.options
    }

    fn new_trace_lde<E: FieldElement<BaseField = Felt>>(
        &self,
        trace_info: &TraceInfo,
        main_trace: &ColMatrix<Felt>,
        domain: &StarkDomain<Felt>,
        partition_option: PartitionOptions,
    ) -> (Self::TraceLde<E>, TracePolyTable<E>) {
        DefaultTraceLde::new(trace_info, main_trace, domain, partition_option)
    }

    fn build_constraint_commitment<E: FieldElement<BaseField = Felt>>(
        &self,
        composition_poly_trace: CompositionPolyTrace<E>,
        num_constraint_composition_columns: usize,
        domain: &StarkDomain<Felt>,
        partition_options: PartitionOptions,
    ) -> (Self::ConstraintCommitment<E>, CompositionPoly<E>) {
        DefaultConstraintCommitment::new(
            composition_poly_trace,
            num_constraint_composition_columns,
            domain,
            partition_options,
        )
    }

    fn new_evaluator<'a, E: FieldElement<BaseField = Felt>>(
        &self,
        air: &'a Self::Air,
        aux_rand_elements: Option<AuxRandElements<E>>,
        composition_coefficients: ConstraintCompositionCoefficients<E>,
    ) -> Self::ConstraintEvaluator<'a, E> {
        DefaultConstraintEvaluator::new(air, aux_rand_elements, composition_coefficients)
    }
}

/// Proof parameters: 27 queries x log2(8) + 16 grinding bits = 97 bits of
/// conjectured security, over the quadratic extension of Goldilocks.
pub fn proof_options() -> ProofOptions {
    ProofOptions::new(27, 8, 16, FieldExtension::Quadratic, 8, 31, BatchingMethod::Linear,
                      BatchingMethod::Linear)
}

pub const MIN_SECURITY_BITS: u32 = 96;

/// Prove `steps` honest steps from `start`. Returns the proof and the end point.
pub fn prove(params: &WalkParams, start: Point, steps: usize, end_is_dp: bool)
             -> Result<(Proof, Point), String> {
    params.validate()?;
    if steps == 0 {
        return Err("steps must be >= 1".into());
    }
    let trace = build_trace(params, start, steps)?;
    let end = (trace.get(COL_X, steps), trace.get(COL_Y, steps));
    if end_is_dp && !params.is_dp(end.0) {
        return Err("end point is not distinguished".into());
    }
    for i in 1..steps {
        if params.is_dp(trace.get(COL_X, i)) {
            return Err(format!("walk reaches a distinguished point early, at step {i}"));
        }
    }
    let pi = PublicInputs { start, end, steps, end_is_dp, params: params.clone() };
    Ok((prove_trace(trace, pi)?, end))
}

/// Prove an arbitrary trace against arbitrary public inputs. Honest callers
/// use [`prove`]; this exists so tests can play a malicious prover.
pub fn prove_trace(trace: TraceTable<Felt>, pi: PublicInputs) -> Result<Proof, String> {
    let prover = WalkProver { options: proof_options(), pi };
    prover.prove(trace).map_err(|e| format!("prover: {e}"))
}

/// Raw trace columns for an honest walk (tests mutate these to forge traces).
pub fn trace_columns(params: &WalkParams, start: Point, steps: usize) -> Result<Vec<Vec<Felt>>, String> {
    let t = build_trace(params, start, steps)?;
    Ok((0..TRACE_WIDTH).map(|c| (0..t.length()).map(|r| t.get(c, r)).collect()).collect())
}

/// Recompute the derived witness columns of row `row` from its x, y and the
/// next row's point, as a malicious prover would after editing a row.
pub fn refill_row(cols: &mut [Vec<Felt>], params: &WalkParams, row: usize, x_bits: u64) {
    let dmask = (1u64 << params.dp_bits) - 1;
    for b in 0..64 {
        cols[COL_B0 + b][row] = Felt::new((x_bits >> b) & 1);
    }
    let slow = x_bits & dmask;
    cols[COL_SLOW][row] = Felt::new(slow);
    cols[COL_INVS][row] = if slow == 0 { Felt::ZERO } else { Felt::new(slow).inv() };
    let z = Felt::new(32 - (x_bits >> 32).count_ones() as u64);
    cols[COL_W][row] = if z == Felt::ZERO { Felt::ZERO } else { z.inv() };
    let idx = ((x_bits >> params.dp_bits) as usize) & (params.r() - 1);
    let r = params.mults[idx];
    cols[COL_RX][row] = r.0;
    cols[COL_RY][row] = r.1;
}

/// Set row `row + 1` to `P_row + R` for an arbitrary R and fix the slope columns.
pub fn force_step(cols: &mut [Vec<Felt>], row: usize, r: Point) {
    let p = (cols[COL_X][row], cols[COL_Y][row]);
    let (next, lam, inv) = add_chord(p, r).expect("chord");
    cols[COL_LAM][row] = lam;
    cols[COL_INVDX][row] = inv;
    cols[COL_X][row + 1] = next.0;
    cols[COL_Y][row + 1] = next.1;
}



pub fn verify(proof: Proof, pi: PublicInputs) -> Result<(), String> {
    pi.params.validate()?;
    if pi.steps == 0 {
        return Err("steps must be >= 1".into());
    }
    // The statement fixes the trace length; reject proofs of a different shape.
    if proof.context.trace_info().length() != trace_length(pi.steps) {
        return Err("proof trace length does not match the step count".into());
    }
    if proof.context.trace_info().width() != TRACE_WIDTH {
        return Err("proof has the wrong trace width".into());
    }
    let acceptable = AcceptableOptions::MinConjecturedSecurity(MIN_SECURITY_BITS);
    winterfell::verify::<WalkAir, Blake3_256<Felt>, DefaultRandomCoin<Blake3_256<Felt>>,
                         MerkleTree<Blake3_256<Felt>>>(proof, pi, &acceptable)
        .map_err(|e| format!("{e}"))
}

// ------------------------------------------------------------ native walk

pub struct WalkOutcome {
    pub length: usize,
    pub end: Point,
    pub a: u64,
    pub b: u64,
    /// `P_k, P_2k, ...` for every multiple of `checkpoint_interval` below `length`.
    pub checkpoints: Vec<Point>,
}

/// The honest walk from `start` to its first distinguished point, tracking
/// the representation `a G + b Q` modulo the subgroup order `n`.
#[allow(clippy::too_many_arguments)]
pub fn walk_to_dp(params: &WalkParams, coeffs: &[(u64, u64)], n: u64, start: Point,
                  a0: u64, b0: u64, max_len: usize, checkpoint_interval: usize)
                  -> Option<WalkOutcome> {
    let mut p = start;
    let mut checkpoints = Vec::new();
    let (mut a, mut b) = (a0 as u128, b0 as u128);
    let n128 = n as u128;
    for i in 1..=max_len {
        let j = params.index(p.0);
        let (next, _, _) = add_chord(p, params.mults[j])?;
        p = next;
        a = (a + coeffs[j].0 as u128) % n128;
        b = (b + coeffs[j].1 as u128) % n128;
        if params.is_dp(p.0) {
            return Some(WalkOutcome { length: i, end: p, a: a as u64, b: b as u64, checkpoints });
        }
        if checkpoint_interval > 0 && i % checkpoint_interval == 0 {
            checkpoints.push(p);
        }
    }
    None
}
