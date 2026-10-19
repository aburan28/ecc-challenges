#!/usr/bin/env bash
set -euo pipefail
repo="$(cd "$(dirname "$0")/../.." && pwd)"
scratch="$(mktemp -d "${TMPDIR:-/tmp}/ecc-published-results.XXXXXX")"
trap 'rm -rf "$scratch"' EXIT
"${CXX:-c++}" -std=c++17 -O2 -Wall -Wextra -pedantic \
  -I "$repo/worker/generic" "$repo/verification/solved-results-20261019/verify.cpp" \
  -o "$scratch/verify"
jq -r '. as $registry | .instances[] | select($registry.reference_vectors[.id]) |
  [.id, (if .field_kind == "binary" then 1 else 0 end), .bits,
   .modulus, .a, .b, .n, .G[0], .G[1], .Q[0], .Q[1],
   $registry.reference_vectors[.id].scalar] | @tsv' \
  "$repo/src/ecc_challenges/multicurve/instances.json" > "$scratch/inputs.tsv"
"$scratch/verify" < "$scratch/inputs.tsv" > "$scratch/results.jsonl"
jq -s '{check: "published scalar times generator equals challenge point",
  arithmetic: "worker/generic/ec_core.h (C++17 CPU)", results: .}' "$scratch/results.jsonl"
