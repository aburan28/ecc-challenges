# Published challenge results

The website links the ten challenges marked solved in the 2009 catalogue to
`/results/{campaign}`. Each page displays the published scalar in hexadecimal
and decimal, the public point, generator and curve parameters, and links to
the original result. Existing typography, colors, navigation and table styles
are reused. Result values wrap on small screens.

The values come from the existing
[`instances.json`](../../src/ecc_challenges/multicurve/instances.json) registry.
Dates and result communicators were checked against section 6.1 of
[Certicom's report](https://www.certicom.com/content/dam/certicom/images/pdfs/challenge-2009.pdf#page=27),
updated 10 November 2009. Those entries occupy PDF pages 27–30; Appendix A
starts on PDF page 35. ECCp-109's scalar is printed in decimal in the report;
the page shows that exact value, `281183840311601949668207954530684`.
ECCp-89's coefficient a uses the existing registry's reduction modulo p,
with a note explaining that normalization.

## Arithmetic check

Run from the repository root, with C++17 and jq installed:

```sh
bash verification/solved-results-20261019/verify.sh
```

The shell script exports the ten existing reference vectors with jq, compiles
the native C++ checker, and evaluates `k * P == Q` using
[`worker/generic/ec_core.h`](../../worker/generic/ec_core.h).
It also checks that both points lie on the curve, that `0 < k < n`, and that
`(k + 1) * P` fails to match Q. It exits unsuccessfully on any failed check
or if it does not receive exactly ten distinct challenges.

[`native-check.json`](native-check.json) records the 19 October 2026 run:
all ten solutions match, and all ten changed-scalar controls are rejected.
Host: macOS arm64; Apple clang 17.0.0, C++17, `-O2 -Wall -Wextra -pedantic`.
This checks transcription and display inputs using the existing worker
arithmetic; it is not a second implementation of that arithmetic.

Input SHA-256 digests:

| Input | SHA-256 |
| --- | --- |
| Certicom PDF | `3a0cff3f84e933e9f9b22a0972fee5673a2ed16a406421f2daa945ffa33d5c13` |
| `instances.json` | `72fb96daecdc4f474c70aa083d50d25c38e95f9eefee44fd6994e48735b3d5af` |
| `ec_core.h` | `90b2d04da9c626e2d0a0f6e63f3f50929a93b28caf522211bfdae43b414eff80` |
| `verify.cpp` | `263677ab71f3c4d239071e55e430780381510254d381c95043809a9bb7fb315f` |

## Website checks

`python -m pytest -q tests/test_catalog.py tests/test_results.py`: **25 passed**.
Coverage includes all ten public result pages with the database unavailable,
all solved links on both catalog pages with and without live counts, HTML
404s for unsolved and unknown names, the source's decimal value, and the
ECCp-89 normalization note.

Chrome inspection followed all ten solved links and their return links.
Result content fits at 320, 390 and 1440 pixels in light and dark modes;
desktop and 390-pixel screenshots were inspected. At 320 pixels the existing
shared header extends six pixels beyond the viewport on both the status
page and result pages; the new result tables introduce no content overflow.
