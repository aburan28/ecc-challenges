// hostcheck.cpp - the arithmetic cross-check of hostcheck.h; see there.
#include "hostcheck.h"

namespace ec2k_gpu
{

namespace
{

// The field routines on random inputs.
void checkField(CheckResult &cr, int rounds, uint64_t *rng)
{
    using namespace eccPacked131;
    for (int i = 0; i < rounds; ++i) {
        ec2k_fe a, b, m, s, inv;
        randomFe(&a, rng);
        randomFe(&b, rng);
        if (ec2k_fe_is_zero(&a)) continue;
        const P131 pa = toPacked(a), pb = toPacked(b);

        ec2k_fe_mul(&m, &a, &b);
        cr.note(sameFe(fromPacked(mul131(pa, pb)), m), "mul131 == golden multiply");
        ec2k_fe_sqr(&s, &a);
        cr.note(sameFe(fromPacked(sqr131(pa)), s), "sqr131 == golden square");
        ec2k_fe_inv(&inv, &a);
        cr.note(sameFe(fromPacked(inv131(pa)), inv), "inv131 == golden inverse");

        static const unsigned powers[] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 16, 32, 65};
        for (unsigned k : powers) {
            ec2k_fe f;
            ec2k_fe_frob(&f, &a, k);
            cr.note(sameFe(fromPacked(sigma131(pa, (int)k)), f), "sigma131 == golden Frobenius");
        }

        // Polynomial basis: to, product, reduce, from.
        const P131 qa = toPolynomial131(pa), qb = toPolynomial131(pb);
        cr.note(sameFe(fromPacked(fromPolynomial131(qa)), a), "polynomial basis round trip");
        cr.note(sameFe(fromPacked(fromPolynomial131(mulPolynomial131(qa, qb))), m),
                "polynomial product == golden multiply");
        cr.note(sameFe(fromPacked(fromPolynomial131(squarePolynomial131(qa))), s),
                "polynomial squaring == golden square");
        cr.note(sameFe(fromPacked(fromPolynomial131(add131(qa, qb))),
                       [&] {
                           ec2k_fe r;
                           ec2k_fe_add(&r, &a, &b);
                           return r;
                       }()),
                "polynomial addition == golden addition");
    }
}

#if ECC_WALK_TABLE
// The selection on a subgroup point: the device's table lookups (twSelect on
// packed coordinates, host-compiled) against the reference coordinate
// functions on the model's limbs, with the same history.
void checkSelection(CheckResult &cr, const HostWalk &t, int rounds, uint64_t *rng)
{
    using namespace eccPacked131;
    const std::vector<uint32_t> consts = t.deviceConsts();
    for (int i = 0; i < rounds; ++i) {
        ec2k_pt p;
        ec2k_point_from_seed(&p, (*rng)++);
        const int hw = (int)ec2k_fe_weight(&p.x);
        unsigned long long hist = (i & 1) ? ECC_HIST_EMPTY : (0xFFFFFFFFull << 32) | 0x12340000ull;
        const unsigned want = referenceTag(t, p, hist);
        unsigned long long h2 = hist;
        const unsigned got =
            twSelect(toPacked(p.x), toPolynomial131(toPacked(p.y)), hw, &h2, consts.data());
        cr.note(got == want, "twSelect tag == reference tag");
        cr.note(h2 == eccHistPush(hist, want), "twSelect history push");
        // and the addend it reads back from the table, in the polynomial basis
        P131 d, e;
        twAddend(got, toPolynomial131(toPacked(p.x)), toPolynomial131(toPacked(p.y)), consts.data(),
                 &d, &e);
        ec2k_pt q = t.point[eccTagH(got)][eccTagK(got)];
        if (eccTagEps(got)) {
            ec2k_pt n;
            ec2k_pt_neg(&n, &q);
            q = n;
        }
        ec2k_fe dx, ey;
        ec2k_fe_add(&dx, &p.x, &q.x);
        ec2k_fe_add(&ey, &p.y, &q.y);
        cr.note(sameFe(fromPacked(fromPolynomial131(d)), dx), "twAddend d == x + x_T");
        cr.note(sameFe(fromPacked(fromPolynomial131(e)), ey), "twAddend e == y + y_T");
        P131 d2;
        twDenominator(got, toPolynomial131(toPacked(p.x)), consts.data(), &d2);
        cr.note(memcmp(d2.v, d.v, sizeof(d.v)) == 0, "twDenominator == twAddend's d");
    }
}

// One packed step as the device takes it: selection on the normal-basis x,
// the addend from the table, the addition in the polynomial basis.
void packedStep(const HostWalk &t, const std::vector<uint32_t> &consts, P131 *x, P131 *y,
                unsigned long long *hist)
{
    using namespace eccPacked131;
    const P131 xn = fromPolynomial131(*x);
    const int hw = weightOf(xn);
    const unsigned tag = twSelect(xn, *y, hw, hist, consts.data());
    P131 d, e;
    twAddend(tag, *x, *y, consts.data(), &d, &e);
    const P131 lambda = mulPolynomial131(e, toPolynomial131(inv131(fromPolynomial131(d))));
    const P131 nx = add131(add131(squarePolynomial131(lambda), lambda), d);
    const P131 ny = add131(add131(mulPolynomial131(lambda, add131(*x, nx)), nx), *y);
    *x = nx;
    *y = ny;
    (void)t;
}
#else
// The two Frobenius networks the sigma walk's forward pass takes, sigma^j of x
// and y together for j = 3..10, against the model's Frobenius: the global-mask
// network and the shared-memory copy the campaign build reads (on the host
// both read the same immutable masks, which is what makes this a check of the
// network rather than of the copy).
void checkSelection(CheckResult &cr, const HostWalk &, int rounds, uint64_t *rng)
{
    using namespace eccPacked131;
    for (int i = 0; i < rounds; ++i) {
        ec2k_fe a, b;
        randomFe(&a, rng);
        randomFe(&b, rng);
        for (int j = EC2K_J_MIN; j < EC2K_J_MIN + EC2K_J_COUNT; ++j) {
            ec2k_fe fa, fb;
            ec2k_fe_frob(&fa, &a, (unsigned)j);
            ec2k_fe_frob(&fb, &b, (unsigned)j);
            const SigmaWalkPair131 g = sigmaWalkNetworkPair131(toPacked(a), toPacked(b), j - 3);
            cr.note(sameFe(fromPacked(g.first), fa) && sameFe(fromPacked(g.second), fb),
                    "sigmaWalkNetworkPair131 == golden Frobenius");
            const SigmaWalkPair131 s =
                sigmaWalkNetworkPairShared131(toPacked(a), toPacked(b), j - 3);
            cr.note(sameFe(fromPacked(s.first), fa) && sameFe(fromPacked(s.second), fb),
                    "sigmaWalkNetworkPairShared131 == golden Frobenius");
        }
    }
}

// One packed step as the device's weighted-prefix forward and reverse passes
// take it for a single slot: j from the normal-basis weight, d = x + sigma^j x
// and e = y + sigma^j y from the shared network, the addition in the
// polynomial basis.
void packedStep(const HostWalk &, const std::vector<uint32_t> &, P131 *x, P131 *y,
                unsigned long long *)
{
    using namespace eccPacked131;
    const P131 xn = fromPolynomial131(*x), yn = fromPolynomial131(*y);
    const int j = 3 + ((weightOf(xn) >> 1) & 7);
    const SigmaWalkPair131 sigmas = sigmaWalkNetworkPairShared131(xn, yn, j - 3);
    const P131 d = toPolynomial131(add131(xn, sigmas.first));
    const P131 e = toPolynomial131(add131(yn, sigmas.second));
    const P131 lambda = mulPolynomial131(e, toPolynomial131(inv131(fromPolynomial131(d))));
    const P131 nx = add131(add131(squarePolynomial131(lambda), lambda), d);
    const P131 ny = add131(add131(mulPolynomial131(lambda, add131(*x, nx)), nx), *y);
    *x = nx;
    *y = ny;
}
#endif

#if !ECC_WALK_TABLE
int hexNibble(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
}

// P and Q are Certicom's points: on the curve, of prime order n, and distinct
// from the points the tests below would otherwise be built from.
void checkChallenge(CheckResult &cr)
{
    ec2k_pt P, Q, z;
    challengePoints(&P, &Q);
    cr.note(ec2k_on_curve(&P) && ec2k_on_curve(&Q), "challenge points are on the curve");
    ec2k_pt_mul(&z, &P, SUBGROUP_ORDER, 3);
    cr.note(z.inf != 0, "[n]P = O");
    ec2k_pt_mul(&z, &Q, SUBGROUP_ORDER, 3);
    cr.note(z.inf != 0, "[n]Q = O");
}

// Walk every known answer's seed from the challenge points to its first point
// of weight <= 46 and compare the 32-byte record.
void checkKnownAnswers(CheckResult &cr, const char *path)
{
    FILE *f = fopen(path, "r");
    cr.note(f != nullptr, "known-answer file opens");
    if (!f) return;
    ec2k_pt P, Q;
    challengePoints(&P, &Q);
    HostWalk *walk = new HostWalk;
    walk->build(P, Q);
    char line[256];
    int records = 0, matched = 0;
    while (fgets(line, sizeof(line), f)) {
        if (line[0] == '#' || line[0] == '\n') continue;
        uint8_t want[EC2K_RECORD_BYTES];
        bool parsed = true;
        for (int i = 0; i < EC2K_RECORD_BYTES && parsed; ++i) {
            const int hi = hexNibble(line[2 * i]), lo = hexNibble(line[2 * i + 1]);
            parsed = hi >= 0 && lo >= 0;
            want[i] = (uint8_t)(hi << 4 | lo);
        }
        cr.note(parsed, "known answer parses");
        if (!parsed) continue;
        unsigned long long seed = 0;
        for (int i = 0; i < 8; ++i) seed |= (unsigned long long)want[i] << (8 * i);
        DpRecord rec;
        uint8_t got[EC2K_RECORD_BYTES];
        const bool walked = referenceReport(*walk, seed, 46, 1ull << 24, &rec);
        if (walked) campaignRecord(got, rec);
        const bool same = walked && memcmp(got, want, sizeof(got)) == 0;
        cr.note(same, "known answer: the model walks the seed to the campaign client's record");
        records++;
        matched += same;
    }
    fclose(f);
    delete walk;
    cr.note(records >= 32, "known-answer file has its records");
    printf("known answers: %d of %d records reproduced from the challenge points\n", matched,
           records);
}

#endif

} // namespace

#if !ECC_WALK_TABLE
CheckResult &campaignCheck(CheckResult &cr, const char *katPath)
{
    checkChallenge(cr);
    checkKnownAnswers(cr, katPath);
    return cr;
}
#endif

CheckResult crossCheck(const HostWalk &t, int rounds, uint64_t seed)
{
    using namespace eccPacked131;
    CheckResult cr;
    uint64_t rng = seed;
    checkField(cr, rounds, &rng);
    checkSelection(cr, t, rounds, &rng);

    // The walk itself, a few steps from a seeded start, on the golden model
    // and on the packed arithmetic driven by the device's selection code.
#if ECC_WALK_TABLE
    const std::vector<uint32_t> consts = t.deviceConsts();
#else
    const std::vector<uint32_t> consts;
#endif
    for (int i = 0; i < 4; ++i) {
        ec2k_pt r;
        if (!startPoint(t, eccSeedFor(7u, (unsigned long long)i), &r)) continue;
        cr.note(ec2k_on_curve(&r) != 0, "start point is on the curve");
        unsigned long long hist = HIST_START, hist2 = HIST_START;
        P131 x = toPolynomial131(toPacked(r.x)), y = toPolynomial131(toPacked(r.y));
        for (int step = 0; step < 24; ++step) {
            packedStep(t, consts, &x, &y, &hist2);
            if (!referenceStep(t, &r, &hist)) break;
            cr.note(sameFe(fromPacked(fromPolynomial131(x)), r.x) &&
                        sameFe(fromPacked(fromPolynomial131(y)), r.y),
                    "packed walk step == golden walk step");
            cr.note(hist == hist2, "packed and golden histories agree");
            cr.note(ec2k_on_curve(&r) != 0, "walk stays on the curve");
        }
    }
    return cr;
}

} // namespace ec2k_gpu
