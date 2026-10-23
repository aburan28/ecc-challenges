# 131-bit rho engine benchmarks

Steps per second of the engine behind ECC2-131 and ECCp-131, old against new,
one stage at a time. `bench.py` drives the engine the way
`worker/generic/client.py` does (rounds validated in Python), `table.py` renders
`results/*.jsonl` into the tables below, `toy_gain.py` measures what the
negation map saves.

## How to read the numbers

* **Host**: Apple M4 Pro (10 performance + 4 efficiency cores), macOS 26.6,
  Apple Clang, `cmake` Release build. No NVIDIA GPU was available, so the CUDA
  kernel is **not measured** (see "What was not run").
* **The host was not quiet.** It was shared with other jobs throughout: load
  average 50 to 250 on 14 cores. Wall-clock columns are therefore low and
  noisy, and some rows are not monotone for no reason the code explains.
  *Engine CPU-s* is the engine's own process CPU time per round, which is much
  less disturbed than wall time, and *best* is the highest observed round of up
  to two runs. CPU-s excludes Python and service work; these are observed
  component rates, not bounds on quiet-host throughput. Per-row best selection
  can favor different runs. The GPU rows only have wall time and are the least reliable.
* One thread, 128 lanes, 256 steps per round (the client's old round size)
  unless a table says otherwise. S0 is the engine as it was on `main`
  (`ECC_ENGINE_FAST=0`), driven through the same `run_round`.
* Stages are cumulative: S1 specialised field arithmetic with batch size 1
  (walk 2, bit-identical outputs); S3 adds Montgomery batch inversion (batch
  swept 4 to 64); S4a a long-lived engine process; S4b the x-only partition and
  DP test (walk 3, no SHA-256); S5 the negation map (walk 4). S0 to S4a keep the
  published walk, so their outputs are byte-identical to S0 (tests below);
  walks 3 and 4 are new protocol versions and are checked against a Python
  reference instead.

## Result in one paragraph

The single-thread runs went from about 0.11 M (ECC2-131) and 0.06 M (ECCp-131) steps
per CPU-second to about 9.1 M and 3.0 M: roughly **80x** and **50x**. About
60% of the per-step cost left after batching was SHA-256 (S4a to S4b: 3.6 to
9.8 M on ECC2-131), and batch inversion was the other large step. The negation
map is not free per step (on ECC2-131 walk 4 steps are about 7% slower than
walk 3; on ECCp-131 the difference is inside the noise) and saves fewer steps than the ideal sqrt(2): **1.26 +/- 0.05** on the
25-bit control curve (below), because a table of 32 jumps gives short fruitless
cycles that cost steps to detect and leave. On the GPU the prime curve gains
3 to 5x over the old Metal kernel, but **ECC2-131 on the GPU is a loss**: the
carry-less product is emulated with integer multiplies and the M4 Pro GPU does
not beat one CPU core (S1 1.5x, S4b 0.4x of the old GPU rate, all within noise
of a shared GPU).

## Tables

### m4pro-cpu-10threads-ecc2-131

| Stage | Walk | Engine CPU-s median | Engine CPU-s best | Wall, with Python checks | vs S0 (wall) |
| --- | --- | --- | --- | --- | --- |
| S0 reference engine, walk 2 | 2 | 72,873 | 75,333 | 93,973 | 1.0x |
| S1 fast field, batch 1, walk 2 | 2 | 459,425 | 462,024 | 179,078 | 1.9x |
| S3 + batch inversion 16 | 2 | 1,594,189 | 1,778,849 | 554,929 | 5.9x |
| S3 + batch inversion 64 | 2 | 1,823,972 | 1,976,268 | 428,770 | 4.6x |
| S4a + persistent engine (batch 16) | 2 | 1,570,588 | 1,643,149 | 326,532 | 3.5x |
| S4b + x-only partition/DP (walk 3) | 3 | 7,334,863 | 7,479,400 | 942,153 | 10.0x |
| S5 + negation map (walk 4) | 4 | 6,632,932 | 6,757,403 | 1,004,386 | 10.7x |
| S4a + persistent engine (batch 64) | 2 | 1,875,345 | 2,087,965 | 761,579 | 8.1x |

10 thread(s), 640 lanes, 1024 steps per round; best of 2 run(s) per row; host load average at the end of a run 220.

### m4pro-cpu-10threads-eccp-131

| Stage | Walk | Engine CPU-s median | Engine CPU-s best | Wall, with Python checks | vs S0 (wall) |
| --- | --- | --- | --- | --- | --- |
| S0 reference engine, walk 2 | 2 | 85,253 | 86,828 | 118,652 | 1.0x |
| S1 fast field, batch 1, walk 2 | 2 | 210,361 | 213,903 | 297,792 | 2.5x |
| S3 + batch inversion 16 | 2 | 1,267,951 | 1,320,029 | 1,831,749 | 15.4x |
| S3 + batch inversion 64 | 2 | 1,650,634 | 1,680,393 | 2,126,752 | 17.9x |
| S4a + persistent engine (batch 64) | 2 | 2,372,170 | 2,434,717 | 2,457,187 | 20.7x |
| S4b + x-only partition/DP (walk 3) | 3 | 5,212,930 | 5,585,946 | 9,109,307 | 76.8x |
| S5 + negation map (walk 4) | 4 | 4,719,950 | 4,910,204 | 6,537,224 | 55.1x |

10 thread(s), 640 lanes, 1024 steps per round; best of 2 run(s) per row; host load average at the end of a run 119.

### m4pro-cpu-1thread-ecc2-131

| Stage | Walk | Engine CPU-s median | Engine CPU-s best | Wall, with Python checks | vs S0 (wall) |
| --- | --- | --- | --- | --- | --- |
| S0 reference engine, walk 2 | 2 | 103,235 | 112,130 | 16,853 | 1.0x |
| S1 fast field, batch 1, walk 2 | 2 | 621,748 | 625,212 | 111,434 | 6.6x |
| S3 + batch inversion 4 | 2 | 1,209,151 | 1,347,258 | 170,585 | 10.1x |
| S3 + batch inversion 8 | 2 | 1,403,220 | 1,592,535 | 223,589 | 13.3x |
| S3 + batch inversion 16 | 2 | 1,656,539 | 2,084,081 | 176,257 | 10.5x |
| S3 + batch inversion 32 | 2 | 2,652,420 | 3,412,622 | 280,722 | 16.7x |
| S3 + batch inversion 64 | 2 | 2,593,636 | 3,076,519 | 140,365 | 8.3x |
| S4a + persistent engine (batch 64) | 2 | 3,415,112 | 3,599,297 | 244,047 | 14.5x |
| S4b + x-only partition/DP (walk 3) | 3 | 6,430,141 | 9,796,114 | 296,888 | 17.6x |
| S5 + negation map (walk 4) | 4 | 8,820,458 | 9,071,982 | 317,362 | 18.8x |
| S4a + persistent engine (batch 32) | 2 | 2,239,169 | 2,409,412 | 221,626 | 13.2x |

1 thread(s), 128 lanes, 256 steps per round; best of 2 run(s) per row; host load average at the end of a run 121.

### m4pro-cpu-1thread-eccp-131

| Stage | Walk | Engine CPU-s median | Engine CPU-s best | Wall, with Python checks | vs S0 (wall) |
| --- | --- | --- | --- | --- | --- |
| S0 reference engine, walk 2 | 2 | 53,679 | 57,550 | 4,835 | 1.0x |
| S1 fast field, batch 1, walk 2 | 2 | 167,862 | 179,932 | 21,420 | 4.4x |
| S3 + batch inversion 4 | 2 | 517,613 | 564,576 | 91,606 | 18.9x |
| S3 + batch inversion 8 | 2 | 740,302 | 882,592 | 217,797 | 45.0x |
| S3 + batch inversion 16 | 2 | 1,092,813 | 1,197,048 | 197,457 | 40.8x |
| S3 + batch inversion 32 | 2 | 1,080,098 | 1,190,006 | 193,924 | 40.1x |
| S3 + batch inversion 64 | 2 | 1,668,687 | 1,697,120 | 296,097 | 61.2x |
| S4a + persistent engine (batch 64) | 2 | 1,460,640 | 1,636,845 | 240,087 | 49.7x |
| S4b + x-only partition/DP (walk 3) | 3 | 2,588,310 | 2,674,284 | 633,216 | 131.0x |
| S5 + negation map (walk 4) | 4 | 2,675,157 | 2,952,072 | 872,183 | 180.4x |

1 thread(s), 128 lanes, 256 steps per round; best of 1 run(s) per row; host load average at the end of a run 105.

### m4pro-metal-ecc2-131

| Stage | Walk | Steps/s median | Steps/s best | vs S0 |
| --- | --- | --- | --- | --- |
| S0 reference engine, walk 2 | 2 | 205,206 | 214,301 | 1.0x |
| S1 fast field + batch inversion, walk 2 | 2 | 298,757 | 306,380 | 1.5x |
| S4a + persistent engine | 2 | 159,893 | 164,301 | 0.8x |
| S4b + x-only partition/DP (walk 3) | 3 | 85,772 | 114,378 | 0.4x |
| S5 + negation map (walk 4) | 4 | 208,630 | 209,464 | 1.0x |

1 thread(s), 2048 lanes, 256 steps per round; best of 2 run(s) per row; host load average at the end of a run 67.

### m4pro-metal-eccp-131

| Stage | Walk | Steps/s median | Steps/s best | vs S0 |
| --- | --- | --- | --- | --- |
| S0 reference engine, walk 2 | 2 | 509,106 | 582,835 | 1.0x |
| S1 fast field + batch inversion, walk 2 | 2 | 1,487,307 | 1,669,456 | 2.9x |
| S4a + persistent engine | 2 | 2,405,313 | 2,477,012 | 4.7x |
| S4b + x-only partition/DP (walk 3) | 3 | 2,031,691 | 2,166,678 | 4.0x |
| S5 + negation map (walk 4) | 4 | 1,908,725 | 2,901,466 | 3.7x |

1 thread(s), 2048 lanes, 256 steps per round; best of 2 run(s) per row; host load average at the end of a run 253.


## What the negation map saves (toy curve)

`toy_gain.py` solves a 25-bit prime-order curve 300 times per walk through the
production engine, ECCJOB04 jobs and record checks (`results/toy-negation-gain.json`):

| | mean steps to a collision | theory |
| --- | --- | --- |
| walk 3 (points) | 4798 +/- 142 | sqrt(pi n / 2) = 5134 |
| walk 4 (classes {P, -P}) | 3820 +/- 116 | sqrt(pi n / 4) = 3631 |
| ratio | **1.26** | 1.41 |

Walk 4 costs 5% more steps than the theory and walk 3 7% fewer, so the ratio
sits between the two; 300 trials give about +/-0.05. The table size affects short cycles, but this control does not measure their
rate on either 131-bit challenge. It does not establish the same gain there.
A larger jump table changes the walk and needs its own correctness and cost measurements.

## What the speedup means for the challenges

Arithmetic only, from the best single-core rates above. ECCp-131 needs about
2^65.4 steps by the ideal walk-2 birthday model and 2^64.9 by the ideal
walk-4 model, before cycle overhead. The old engine at 57 k steps/s is 2^65.4 / 57,550 =
1.5e7 core-years; walk 4 at 2.95 M steps/s is 2^64.9 / 2.95e6 = 3.0e5
core-years. These extrapolations omit cycle overhead, Python validation,
checkpointing, upload, indexing and collision processing. They are not measured
completion times or an end-to-end speedup.

## What was not run

* **CUDA.** There is no NVIDIA GPU on this host. The CUDA kernel reuses the
  device code the Metal kernel runs (`gpu_chunk`) and was only syntax-checked
  with clang's CUDA front end (host and device passes, stubbed runtime), never
  built with `nvcc` or executed. The GPU image build will compile it first.
* **x86-64.** Only arm64 (PMULL) was built and measured. The PCLMULQDQ path is
  covered by the arithmetic self-test when runtime CPU detection enables it.
  These benchmark records contain no x86-64 measurement.
* **A quiet host.** See above. Rerun `bench.py` on an idle machine before
  quoting a figure.
* **Windows/MSVC** builds of the new headers.

## Reproduce

```sh
cmake -S worker -B build -DECC_ENABLE_CUDA=OFF && cmake --build build -j8
python benchmarks/rho131/bench.py build/generic-engine eccp-131 --lanes 128 --steps 256 --rounds 5
python benchmarks/rho131/bench.py build/generic-engine ecc2-131 --threads 10 --lanes 640 --steps 1024
python benchmarks/rho131/bench.py build/generic-engine eccp-131 --backend metal --lanes 2048
python benchmarks/rho131/toy_gain.py build/generic-engine --trials 300
python benchmarks/rho131/table.py
```
