// Shared function body; deliberately included by separate host and device
// templates. No include guard: this is not a standalone translation unit.
// Keeping execution spaces explicit avoids compiling host-only Ref operations
// into an unused device specialization (or device intrinsics for the host).
    ECC_CYCLE_PROFILE_BEGIN();
    Point points[8];
    unsigned tags[8];
    Point cur = start;
    int length = 0;
    for (int i = 0; i < 8; ++i) {
        if (ops.distinguished(cur)) {
            ECC_CYCLE_PROFILE_OUTCOME(ECC_CYCLE_OUTCOME_DP_ABORT);
            return raw; // never escape past a report
        }
        points[i] = cur;
#if ECC_CYCLE_FAST2
        // The fast-two probe fills tags[1] while examining the first edge, so
        // a non-two-cycle continues without recomputing that raw selection.
        if (i == 0) tags[i] = raw;
        else if (i > 1) tags[i] = ops.tag(cur);
#else
        tags[i] = i == 0 ? raw : ops.tag(cur);
#endif
        Point next;
        ECC_CYCLE_PROFILE_NEXT();
        if (!ops.next(cur, tags[i], &next)) {
            ECC_CYCLE_PROFILE_OUTCOME(ECC_CYCLE_OUTCOME_EXCEPTIONAL_ABORT);
            return raw;
        }
        cur = next;
        if (ops.equal(cur, start)) { length = i + 1; break; }
#if ECC_CYCLE_FAST2
        if (i == 0) {
            // If the next raw tag is the exact inverse table addend, the
            // group law proves a two-cycle. oppositeCloses also verifies the
            // second affine step's denominator is nonzero, matching next()'s
            // exceptional-path contract without paying its inversion.
            if (ops.distinguished(cur)) {
                ECC_CYCLE_PROFILE_OUTCOME(ECC_CYCLE_OUTCOME_DP_ABORT);
                return raw;
            }
            const unsigned opposite = tags[1] = ops.tag(cur);
            if (eccTagNegates(raw, opposite) &&
                ops.oppositeCloses(start, cur, opposite)) {
                ECC_CYCLE_PROFILE_OUTCOME(ECC_CYCLE_OUTCOME_FAST2);
                unsigned long long h0 = ECC_HIST_EMPTY;
                h0 = eccHistPush(h0, raw);
                h0 = eccHistPush(h0, opposite);
                h0 = eccHistPush(h0, raw);
                h0 = eccHistPush(h0, opposite);
                unsigned long long h1 = ECC_HIST_EMPTY;
                h1 = eccHistPush(h1, opposite);
                h1 = eccHistPush(h1, raw);
                h1 = eccHistPush(h1, opposite);
                h1 = eccHistPush(h1, raw);
                const bool startEligible = eccTagFruitless(raw, h0, m);
                const bool otherEligible = eccTagFruitless(opposite, h1, m);
                if (!startEligible || (otherEligible && ops.less(cur, start)))
                    return raw;
                ECC_CYCLE_PROFILE_EXIT();
                return eccTag((eccTagH(raw) + 1) & (branches - 1),
                              eccTagK(raw), eccTagEps(raw));
            }
        }
#endif
    }
    if (!length) {
        ECC_CYCLE_PROFILE_OUTCOME(ECC_CYCLE_OUTCOME_OPEN_8);
        return raw;
    }
    ECC_CYCLE_PROFILE_OUTCOME(ECC_CYCLE_OUTCOME_GENERAL_1 + length - 1);
    int anchor = -1;
    bool startEligible = false;
    for (int i = 0; i < length; ++i) {
        unsigned long long cyclic = ECC_HIST_EMPTY;
        for (int back = 4; back >= 1; --back)
            cyclic = eccHistPush(cyclic, tags[(i + 4 * length - back) % length]);
        if (!eccTagFruitless(tags[i], cyclic, m)) continue;
        if (i == 0) startEligible = true;
        if (anchor < 0 || ops.less(points[i], points[anchor])) anchor = i;
    }
    // Equivalent orbit keys may tie: covariance gives equivalent exit edges.
    if (!startEligible || anchor < 0 || ops.less(points[anchor], start)) return raw;
    ECC_CYCLE_PROFILE_EXIT();
    return eccTag((eccTagH(raw) + 1) & (branches - 1), eccTagK(raw), eccTagEps(raw));
