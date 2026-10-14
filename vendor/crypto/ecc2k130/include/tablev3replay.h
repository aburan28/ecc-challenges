// Strict, host-only replay of a deterministic spread through a table-v3 corpus.
//
// The corpus stores (seed, canonical orbit x), not the transient iteration
// count or y coordinate held by the producer.  Replaying a sample therefore
// checks that each retained seed reaches the named canonical orbit under the
// exact scalar table-walk reference.  It also reports how many selected trails
// actually take a step: an atomic report prefix can consist entirely of
// zero-step distinguished starting points and is not useful queue coverage.
#pragma once

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>

#include <algorithm>
#include <limits>
#include <string>
#include <vector>

#include "curveparams.h"
#include "kernel.h"
#include "solver.h"

#if !ECC_WALK_TABLE
#error "tablev3replay requires ECC_WALK_TABLE=1"
#endif
#if ECC_TABLE_BRANCHES != 8
#error "tablev3replay is pinned to the eight-branch table-v3 rule"
#endif
#if !ECC_CYCLE_FAST2
#error "tablev3replay is pinned to the exact-fast2 table-v3 rule"
#endif

namespace tableV3Replay {

static const size_t HEADER_BYTES = 16;
static const size_t RECORD_BYTES = 32;

struct Options {
    unsigned runId = 0;
    int dpWeight = 0;
    unsigned long long campaignMaxIters = 1ull << 32;
    size_t sampleCount = 300;
    size_t minimumNonzero = 1;
};

struct Record {
    unsigned long long seed = 0;
    unsigned long long canon[3] = {0, 0, 0};
};

struct Result {
    std::string path;
    bool valid = false;
    unsigned long long fileBytes = 0;
    unsigned long long records = 0;
    unsigned long long recordsScanned = 0;
    unsigned long long runIdMismatches = 0;
    unsigned long long invalidCanonicalRecords = 0;
    size_t requestedSamples = 0;
    size_t selectedSamples = 0;
    unsigned long long selectedFirstIndex = 0;
    unsigned long long selectedLastIndex = 0;
    size_t matchedCanonicalOrbit = 0;
    size_t mismatches = 0;
    size_t notDistinguished = 0;
    size_t zeroStep = 0;
    size_t nonzeroStep = 0;
    unsigned long long totalSteps = 0;
    unsigned long long minimumSteps = 0;
    unsigned long long medianLowerSteps = 0;
    unsigned long long medianUpperSteps = 0;
    unsigned long long maximumSteps = 0;
    std::vector<std::string> errors;
};

static inline uint32_t load32le(const unsigned char *p) {
    return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) |
           (uint32_t(p[3]) << 24);
}

static inline unsigned long long load64le(const unsigned char *p) {
    unsigned long long x = 0;
    for (int i = 7; i >= 0; --i) x = (x << 8) | p[i];
    return x;
}

static inline void addError(Result *out, const std::string &message) {
    out->errors.push_back(out->path + ": " + message);
}

// Select both endpoints and uniformly cover the intervening record indices.
// With records >= samples >= 2, floor(i*(records-1)/(samples-1)) is strictly
// increasing, so no selected record is duplicated.
static inline bool spreadIndices(unsigned long long records, size_t samples,
                                 std::vector<unsigned long long> *indices,
                                 std::string *why) {
    indices->clear();
    if (samples < 2) {
        *why = "sample count must be at least two";
        return false;
    }
    if (records < samples) {
        *why = "corpus has fewer records than the requested spread sample";
        return false;
    }
    indices->reserve(samples);
    for (size_t i = 0; i < samples; ++i) {
        const unsigned __int128 numerator =
            (unsigned __int128)i * (unsigned __int128)(records - 1);
        const unsigned long long index =
            (unsigned long long)(numerator / (unsigned long long)(samples - 1));
        if (!indices->empty() && index <= indices->back()) {
            *why = "spread selection produced a duplicate or decreasing index";
            indices->clear();
            return false;
        }
        indices->push_back(index);
    }
    return indices->front() == 0 && indices->back() == records - 1;
}

static inline Result inspectCorpus(const std::string &path, const Options &options,
                                   const Solver<CfgF131> &solver) {
    Result out;
    out.path = path;
    out.requestedSamples = options.sampleCount;
    if (options.runId > 0xffffu) {
        addError(&out, "run id is outside the 16-bit seed namespace");
        return out;
    }
    if (options.dpWeight < 0 || options.dpWeight > CfgF131::M) {
        addError(&out, "distinguished-point weight is outside [0,131]");
        return out;
    }
    if (options.minimumNonzero > options.sampleCount) {
        addError(&out, "minimum nonzero coverage exceeds the sample count");
        return out;
    }
    if (options.campaignMaxIters >
        std::numeric_limits<unsigned long long>::max() - (ECC_GUARD_PERIOD - 1)) {
        addError(&out, "campaign max-iters overflows the producer guard overshoot");
        return out;
    }
    const unsigned long long expectedReplayMax = options.campaignMaxIters
        ? options.campaignMaxIters + ECC_GUARD_PERIOD - 1
        : (1ull << 40);
    if (solver.dpWeight != options.dpWeight || solver.maxIters != expectedReplayMax) {
        addError(&out, "host reference was not initialized with the requested DP/guard context");
        return out;
    }

    FILE *in = fopen(path.c_str(), "rb");
    if (!in) {
        addError(&out, std::string("cannot open corpus: ") + strerror(errno));
        return out;
    }
    struct stat before;
    if (fstat(fileno(in), &before) != 0 || !S_ISREG(before.st_mode)) {
        addError(&out, "corpus is missing or is not a regular file");
        fclose(in);
        return out;
    }
    if (before.st_size < (off_t)HEADER_BYTES) {
        addError(&out, "corpus is shorter than the 16-byte table-v3 header");
        fclose(in);
        return out;
    }
    out.fileBytes = (unsigned long long)before.st_size;
    const unsigned long long payload = out.fileBytes - HEADER_BYTES;
    if (payload % RECORD_BYTES != 0) {
        addError(&out, "payload is not an exact multiple of the 32-byte record size");
        fclose(in);
        return out;
    }
    out.records = payload / RECORD_BYTES;

    unsigned char header[HEADER_BYTES];
    if (fread(header, sizeof header, 1, in) != 1) {
        addError(&out, "cannot read the complete table-v3 header");
        fclose(in);
        return out;
    }
    if (memcmp(header, "ECC2KDT3", 8) != 0 || load32le(header + 8) != 3 ||
        load32le(header + 12) != RECORD_BYTES) {
        addError(&out, "expected exact ECC2KDT3/version 3/recordBytes 32 framing");
        fclose(in);
        return out;
    }

    std::vector<unsigned long long> indices;
    std::string selectionError;
    if (!spreadIndices(out.records, options.sampleCount, &indices, &selectionError)) {
        addError(&out, selectionError);
        fclose(in);
        return out;
    }
    out.selectedFirstIndex = indices.front();
    out.selectedLastIndex = indices.back();
    std::vector<Record> selected;
    selected.reserve(options.sampleCount);
    size_t nextSelected = 0;
    for (unsigned long long index = 0; index < out.records; ++index) {
        unsigned char bytes[RECORD_BYTES];
        if (fread(bytes, sizeof bytes, 1, in) != 1) {
            addError(&out, "corpus changed or became truncated while it was read");
            fclose(in);
            return out;
        }
        Record rec;
        rec.seed = load64le(bytes);
        rec.canon[0] = load64le(bytes + 8);
        rec.canon[1] = load64le(bytes + 16);
        rec.canon[2] = load64le(bytes + 24);
        ++out.recordsScanned;
        if ((rec.seed >> 48) != options.runId) ++out.runIdMismatches;
        if (rec.canon[2] & ~7ull) ++out.invalidCanonicalRecords;
        if (nextSelected < indices.size() && index == indices[nextSelected]) {
            selected.push_back(rec);
            ++nextSelected;
        }
    }
    const int extra = fgetc(in);
    struct stat after;
    const bool stable = fstat(fileno(in), &after) == 0 &&
                        before.st_dev == after.st_dev && before.st_ino == after.st_ino &&
                        before.st_size == after.st_size;
    const bool readError = ferror(in) != 0;
    fclose(in);
    if (extra != EOF || readError || !stable) {
        addError(&out, "corpus changed or contains bytes outside its declared framing");
        return out;
    }
    out.selectedSamples = selected.size();
    if (selected.size() != options.sampleCount) {
        addError(&out, "did not retain every deterministic spread index");
    }
    if (out.runIdMismatches) {
        addError(&out, "one or more records are outside the expected run-id namespace");
    }
    if (out.invalidCanonicalRecords) {
        addError(&out, "one or more canonical orbit keys exceed 131 bits");
    }
    if (!out.errors.empty()) return out;

    typedef Ref<CfgF131> R;
    std::vector<unsigned long long> steps;
    steps.reserve(selected.size());
    for (const Record &rec : selected) {
        const typename Solver<CfgF131>::WalkResult replay = solver.rewalk(rec.seed);
        if (!replay.ok) {
            ++out.notDistinguished;
            continue;
        }
        steps.push_back(replay.iters);
        out.totalSteps += replay.iters;
        const typename R::Elem canon = R::canonical(replay.endPoint.x);
        if (canon.v[0] == rec.canon[0] && canon.v[1] == rec.canon[1] &&
            canon.v[2] == rec.canon[2])
            ++out.matchedCanonicalOrbit;
        else
            ++out.mismatches;
    }
    std::sort(steps.begin(), steps.end());
    out.zeroStep = (size_t)std::count(steps.begin(), steps.end(), 0ull);
    out.nonzeroStep = steps.size() - out.zeroStep;
    if (!steps.empty()) {
        out.minimumSteps = steps.front();
        out.medianLowerSteps = steps[(steps.size() - 1) / 2];
        out.medianUpperSteps = steps[steps.size() / 2];
        out.maximumSteps = steps.back();
    }
    if (out.notDistinguished) addError(&out, "a selected seed did not reach a distinguished point");
    if (out.mismatches) addError(&out, "a selected seed reached a different canonical orbit");
    if (out.matchedCanonicalOrbit != options.sampleCount) {
        addError(&out, "fewer than the requested sample count matched their canonical orbits");
    }
    if (out.nonzeroStep < options.minimumNonzero) {
        addError(&out, "spread sample did not meet the required nonzero-trail coverage");
    }
    out.valid = out.errors.empty();
    return out;
}

}  // namespace tableV3Replay
