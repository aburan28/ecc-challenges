# ECC2K-130 client.
#
#   make generate     regenerate the arithmetic headers from codegen/
#   make cpu          CPU build (64-bit lanes, OpenMP)
#   make test         run the validation suite
#   make gpu          CUDA build (needs nvcc)
#   make ptx          device-only compile + ptxas report (needs clang or nvcc,
#                     but no GPU) -- register, spill and code-size numbers
#   make verilog      emit the FPGA datapath from the same IR (python3 only)
#   make check-verilog  simulate the emitted RTL against the field model
#
# Build-time knobs:
#   BATCH      walks batched into one field inversion per thread (default 32)
#   THREADS    CUDA block size (default 128)
#   MINBLOCKS  resident blocks per SM to ask ptxas for (default 2)
#   WITNESS    carry the cairn witness in the walk (default 1; 0 is the control)
#   PACKED_SINGLE_PRODUCT  use one polynomial product for packed mul (default 0)
#   PACKED_ADD_COMBINE  join clmul32 class products with adds, not ors (default 0)
#   PACKED_PAIR_ILP  independent buffers for the two paired products (default 0)
#   PACKED_L2_PERSIST  one field blob + stream L2 persist window (default 0)
#   PACKED_TOP_HOIST  hoisted 3-bit product correction, bit-identical (default 0)
#   PACKED_ONB_INV  ONB two-product multiply inside inv131 (default 0)
#   PACKED_FROM_REDUCED  5-word inverse of toPolynomial131 (default 0)
#   PACKED_SIGMA_TABLE  fused-walk Frobenius steps from shared nibble tables (default 0)
#   ordinary sigma table  exact polynomial-coordinate table in the
#                       witness-carrying ordinary sigma walk (default 0)
#   PACKED_CLMUL_FLAT  issue 128-bit Karatsuba as 3x lo then 3x hi clmad
#   PACKED_PAIR_CLMUL  twelve-clmad interleaved pair product (default 0)
#   PACKED_ALU_SQR  ONB sqr131 on the logic pipe instead of clmad (default 0)
#   PACKED_SQUARE_TABLE  lambda^2 with its reduced half from a shared table
#                        (default 0; table walk only; ROOFLINE.md)
#   SIGMA_SQUARE_TABLE  the same exact table for lambda^2 in the sigma-fused
#                       walk only (default 0; benchmarks/sigma-square-table)
#   PACKED_INV_POLY  inversion accumulator kept in the polynomial basis
#                    (default 0; 2 = link product out of line; ROOFLINE.md)
#   PACKED_SLOT_PIPELINE  overlap next-slot compact loads with the current product
#   TABLE_ADDEND_GLOBAL  table-walk addends from global, selection in shared
#   TABLE_TAG_DENOM  reverse pass rebuilds d from the tag in hist; no
#                    denominator field (default 0; ONE-BLOCK-GEOMETRY.md)
#   TABLE_FUSED  one fused pass per step, alternating slot order (default 0)
#   TABLE_PIPE_SELECT  forward pass issues slot k's products before slot k+1's
#                      selection (default 0)
#   TABLE_SPLIT_FORWARD  report/select all slots before prefix products
#                        (default 0; requires TABLE_PIPE_SELECT)
#   TABLE_BATCH_HINTS  reconverge split-forward v3 cycle probes across slots
#                      before building the prefix (default 0)
#   TABLE_BLOCK_HINTS  compact hinted slots across a CUDA block (default 0;
#                      requires TABLE_BATCH_HINTS)
#   CYCLE_FAST2  prove raw two-cycles after one affine step (default 0)
#   CYCLE_PROFILE  diagnostic exact-v3 cold-probe counters (default 0)
#   TABLE_GLOBAL_HINT_THREADS  GPU-wide cold-resolver block width: 128, 256 or
#                    512 (default 128; does not change the 188-block grid)
#   TABLE_PHASE_POPC  phase via eight L-bit popc planes (default 0;
#                     CHEAPER-SELECTION.md)
#   PACKED_CHAIN_FIRST  reverse pass issues inv*d before inv*W (default 0)
#   PACKED_CHAINS  independent Montgomery chains per thread, 1 or 2 (default 1;
#                  TWO-CHAINS.md; 2 needs TAG_DENOM and an even BATCH)
#   SIGMA_FUSED_SHARED_SLOTS  full-word per-thread shared pchain/denominator
#                    slots for the exact fused B16/T256/min2 preset: 0/2/3/4
#   PHASE_PROFILE  clock64() timing of the walk's three phases, at exit
#   ARCH     CUDA architectures
#
# `make gpu-rtx-pro6000-20b` is the 20 B/s build of ONE-BLOCK-GEOMETRY.md;
# `make gpu-rtx-pro6000-chains2` its two-chain form (TWO-CHAINS.md).

MODAL   ?= modal
NVCC    ?= nvcc
CXX     ?= g++
# Apple's clang needs the runtime named separately; everywhere else the driver
# flag is enough.  Only the host walk is threaded, so this is all it reaches.
#   make cpu CXX=clang++ OMPFLAGS="-Xpreprocessor -fopenmp -lomp"
OMPFLAGS ?= -fopenmp
CLANG   ?= clang
PTXAS   ?= ptxas
PTX     ?= /tmp/autolab.ptx
CUDAPATH ?= /usr/local/cuda
BATCH   ?= 32
THREADS ?= 128
MINBLOCKS ?= 2
STREAM_KARAT ?= 0
SMEM_SPILL ?= 0
GLOBAL_CG ?= 0
PACKED_SINGLE_PRODUCT ?= 0
PACKED_CACHE_DENOM ?= 0
PACKED_BY_VALUE ?= 0
PACKED_PERM_SIGMA ?= 0
PACKED_PARTIAL_SIGMA ?= 0
PACKED_SIGMA_ORDER ?= 0
PACKED_BYTE_SIGMA ?= 0
PACKED_FAST_CONVERT ?= 0
PACKED_POLY_CHAIN ?= 0
PACKED_UNROLL_INV ?= 0
PACKED_PAIR_PRODUCTS ?= 0
PACKED_POLY_STATE ?= 0
PACKED_DIRECT_REDUCE ?= 0
PACKED_GENERATED_PRODUCT ?= 0
# Bits: 1 inline single polynomial products, 2 inline paired products.
PACKED_INLINE_POLY ?= 0
PACKED_CLMAD ?= 0
PACKED_NATIVE_PRODUCT ?= 0
PACKED_NATIVE_REDUCE ?= 0
PACKED_FUSED_SIGMA ?= 0
PACKED_INLINE ?= 0
PACKED_CLMAD_SQUARE ?= 0
PACKED_KARAT3 ?= 0
PACKED_WEIGHTED_PREFIX ?= 0
PACKED_COMPACT_STATE ?= 0
PACKED_SHARED_SIGMA ?= 0
PACKED_TOP_CLMAD ?= 0
PACKED_STATE_TILE ?= 0
PACKED_BLOCK_INVERSE ?= 0
PACKED_BATCH_SPLIT ?= 1
PACKED_LAST_SLOT_CACHE ?= 0
PACKED_TAIL_LAYOUT ?= 0
PACKED_SHARED_X_SLOTS ?= 0
PACKED_XONLY_23 ?= 0
PACKED_XONLY_DOUBLE_ONLY ?= 0
PACKED_XONLY_BRIDGE3 ?= 0
PACKED_XONLY_BRIDGE1_COMMON ?= 0
PACKED_XONLY_BRIDGE_MOD72 ?= 0
PACKED_XONLY_SKIP_EMPTY_BRIDGE ?= 0
PACKED_XONLY_ARITHMETIC_ONLY ?= 0
PACKED_XONLY_POLY_SELECT ?= 0
PACKED_XONLY_POLY_DP_CONVERT ?= 1
PACKED_XONLY_POLY_SELECT_MASK ?= 4095
PACKED_XONLY_POLY_SELECT_TARGET ?= 14
PACKED_ADD_COMBINE ?= 0
PACKED_PAIR_ILP ?= 0
UNROLL_SLOTS ?= 1
PACKED_SLOT_PREFETCH ?= 0
PACKED_SLOT_PIPELINE ?= 0
SIGMA_FUSED ?= 0
SIGMA_FUSED_LATE_Y ?= 0
SIGMA_FUSED_SHARED_SLOTS ?= 0
SIGMA_SQUARE_TABLE ?= 0
PACKED_L2_PERSIST ?= 0
PACKED_TOP_HOIST ?= 0
PACKED_ONB_INV ?= 0
PACKED_FROM_REDUCED ?= 0
PACKED_SIGMA_TABLE ?= 0
PACKED_CLMUL_FLAT ?= 0
PACKED_PAIR_CLMUL ?= 0
PACKED_ALU_SQR ?= 0
# The table walk of ITERATION-FUNCTION.md §4 (tablewalk.h) in place of sigma^j + 1.
WALK_TABLE ?= 0
TABLE_BRANCHES ?= 8
TABLE_PIVOT_BYTES ?= 0
# Phase via eight L-bit popc planes instead of 17 byte-table lookups
# (CHEAPER-SELECTION.md). Off by default.
TABLE_PHASE_POPC ?= 0
# Read the table from global memory instead of a per-block shared copy.
# Drops the 48 KB smem tax so 80-register builds can resident 3 blocks on
# this SKU's 100 KB/SM; random table lookups then miss the LDS pipe.
TABLE_GLOBAL ?= 0
# Hybrid: selection tables in shared, addend table in global.
TABLE_ADDEND_GLOBAL ?= 0
# Reverse pass rebuilds the denominator from the step tag in hist instead of
# loading the 17-byte field the forward pass stored (no denominator field).
TABLE_TAG_DENOM ?= 0
# One fused pass per step: the reverse pass of step s runs step s+1's
# selection and chain product on the point it just produced (needs TAG_DENOM).
TABLE_FUSED ?= 0
TABLE_FUSED_PIPE ?= 0
# clock64() phase timing of the two-pass walk, printed at exit (diagnostic).
PHASE_PROFILE ?= 0
# Forward pass software-pipelined: slot k's products issue before slot k+1's
# selection (needs TAG_DENOM; two-pass kernel only).
TABLE_PIPE_SELECT ?= 0
# Keep the established kernel as the default until a matched GPU A/B qualifies.
TABLE_SPLIT_FORWARD ?= 0
# Resolve the split-forward pass's cold v3 hints after all raw tags are known.
TABLE_BATCH_HINTS ?= 0
# Compact up to TABLE_HINT_QUEUE pending (thread,slot) pairs in shared memory.
TABLE_BLOCK_HINTS ?= 0
TABLE_GLOBAL_HINTS ?= 0
TABLE_GLOBAL_HINT_THREADS ?= 128
TABLE_HINT_QUEUE ?= 512
# Prove raw two-cycles after one affine step and one denominator check.
CYCLE_FAST2 ?= 0
# Count exact-v3 cold-probe outcomes. Diagnostic only; instrumented timing is
# never performance evidence.
CYCLE_PROFILE ?= 0
# Reverse pass issues the chain product before lambda's.
PACKED_CHAIN_FIRST ?= 0
# Two independent Montgomery chains of BATCH/2 slots per thread, interleaved
# slot by slot with a paired inversion (TWO-CHAINS.md).  Needs TAG_DENOM and
# an even BATCH; BATCH=32 keeps the one-chain BATCH=16 inversion share.
PACKED_CHAINS ?= 1
# Polynomial squaring on the logic pipe instead of the carryless one.
PACKED_ALU_SQUARE ?= 0
# The polynomial square with coefficients 66..130 looked up in an 8 KB shared
# table instead of spread and reduced (ROOFLINE.md; table walk only).
PACKED_SQUARE_TABLE ?= 0
# The walk's inversion with the Itoh-Tsujii accumulator kept in the
# polynomial basis: one conversion per link instead of three (ROOFLINE.md).
# 1 inlines the eight links; 2 shares one out-of-line copy of their product.
PACKED_INV_POLY ?= 0
# Carry the eight per-branch step counts that make a distinguished point
# payable on cairn (CAIRN-WITNESS.md).  WITNESS=0 compiles the counting out
# and is the control for the paired comparison that prices it; it also keeps
# the checkpoint and corpus formats byte-identical to what shipped before.
# The table walk cannot carry this witness, so it is not the default there;
# asking for both explicitly is still refused in walk.h rather than quietly
# turned off.
# The x-only walk (PACKED_XONLY_23) has no such counters either.
ifneq ($(filter 1,$(WALK_TABLE) $(PACKED_XONLY_23)),)
WITNESS ?= 0
else
WITNESS ?= 1
endif
# Keep experimental tuning independent: one change per comparison initially.
GPUFLAGS = -Xptxas -v
ifeq ($(GLOBAL_CG),1)
GPUFLAGS += -Xptxas --def-load-cache=cg
endif
# The CPU build picks the widest word the target supports (AVX-512 -> 512 lanes,
# AVX2 -> 256, NEON -> 128, otherwise 64).  Use a portable baseline when the
# build machine and the run machine may differ.  That lever does not work on
# Apple clang, whose driver keeps the host CPU's features regardless of -march:
# MARCH=armv8-a leaves __ARM_FEATURE_SHA3 undefined yet the backend still folds
# XOR trees into EOR3.  Build portable binaries on the target OS.
MARCH   ?= native

# On aarch64 the wide word is NEON, and EOR3 (FEAT_SHA3) is most of what makes
# it faster than the 64-bit fallback rather than merely wider.  -march=native
# does not always imply +sha3 -- GCC on Darwin reports no SHA3 for native on
# cores that have it -- so ask the compiler which spelling actually defines the
# feature macro and take the first that does.  A compiler-accepted +sha3
# spelling is not proof the host can execute EOR3 (armv8.2-a+sha3 is accepted
# on any recent toolchain), so those candidates are used only when the CPU
# reports FEAT_SHA3.  They also rewrite the requested -march, so only search
# when MARCH is native: an explicit MARCH names a target this host is not
# evidence about.  Falling through to plain MARCH still gets NEON and BSL,
# just not the three-input XOR.
HOSTARCH := $(shell uname -m)
HOSTOS   := $(shell uname -s)
ifneq (,$(filter arm64 aarch64,$(HOSTARCH)))
# Linux lists sha3 in cpuinfo Features when FEAT_SHA3 is present.  Darwin has
# no /proc, and every Apple Silicon core has SHA3, so a missing
# hw.optional.arm.FEAT_SHA3 key means present there and absent anywhere else.
ifeq ($(HOSTOS),Darwin)
HOSTSHA3 := $(shell sysctl -n hw.optional.arm.FEAT_SHA3 2>/dev/null || echo 1)
else
HOSTSHA3 := $(shell grep -m1 '^Features' /proc/cpuinfo 2>/dev/null | grep -c -w sha3)
endif
sha3flag = $(shell $(CXX) $(1) -dM -E -x c++ /dev/null 2>/dev/null | grep -c __ARM_FEATURE_SHA3)
SHA3TRY := $(if $(filter native,$(MARCH)), \
    $(if $(filter-out 0,$(HOSTSHA3)), \
        -march=native+sha3 -mcpu=native+sha3 -march=armv8.2-a+sha3))
ARCHFLAGS := $(firstword \
    $(foreach f,-march=$(MARCH) $(SHA3TRY), \
        $(if $(filter-out 0,$(call sha3flag,$(f))),$(f))) \
    -march=$(MARCH))
else
ARCHFLAGS := -march=$(MARCH)
endif
ARCH    ?= -gencode arch=compute_80,code=sm_80 \
           -gencode arch=compute_86,code=sm_86 \
           -gencode arch=compute_89,code=sm_89 \
           -gencode arch=compute_90,code=sm_90 \
           -gencode arch=compute_120,code=sm_120
# clang emits PTX for CLANGARCH; ptxas then compiles that PTX for SMARCH, which
# may be newer than any architecture clang itself knows about (e.g. sm_120).
CLANGARCH ?= sm_90
SMARCH  ?= sm_90

HDRS = include/durable.h include/bitslice.h include/fieldbs.h include/fieldpb.h include/walk.h \
       include/kernel.h include/ref.h include/solver.h include/bigmod.h include/tablewalk.h \
       include/curveparams.h include/packed131.h include/packedtransform131.h include/packedsigma131.h include/packedsigmascratch.h include/packedpolyreduce131.h include/packeddirectreduce131.h include/packedgeneratedproduct131.h include/packedcompactstate.cuh include/packednative131.h include/packedfusedsigma131.h include/packedblockinverse131.cuh include/packedblockinversereference131.cuh include/packedxonly23.cuh include/packedkernels.cuh include/packedengine.cuh
GEN  = generated/eccF131.h generated/eccF83.h generated/eccF41.h generated/eccF23.h \
       generated/eccP97.h generated/eccP41.h generated/eccP19.h generated/eccP13.h

DEFS = -DECC_BATCH=$(BATCH) -DECC_THREADS=$(THREADS) -DECC_MINBLOCKS=$(MINBLOCKS) \
       -DECC_STREAM_KARAT=$(STREAM_KARAT) -DECC_SMEM_SPILL=$(SMEM_SPILL) \
       -DECC_PACKED_SINGLE_PRODUCT=$(PACKED_SINGLE_PRODUCT) -DECC_PACKED_CACHE_DENOM=$(PACKED_CACHE_DENOM) \
       -DECC_PACKED_BY_VALUE=$(PACKED_BY_VALUE) -DECC_PACKED_PERM_SIGMA=$(PACKED_PERM_SIGMA) \
       -DECC_PACKED_PARTIAL_SIGMA=$(PACKED_PARTIAL_SIGMA) \
       -DECC_PACKED_SIGMA_ORDER=$(PACKED_SIGMA_ORDER) \
       -DECC_PACKED_BYTE_SIGMA=$(PACKED_BYTE_SIGMA) \
       -DECC_PACKED_FAST_CONVERT=$(PACKED_FAST_CONVERT) \
       -DECC_PACKED_POLY_CHAIN=$(PACKED_POLY_CHAIN) -DECC_PACKED_UNROLL_INV=$(PACKED_UNROLL_INV) \
       -DECC_PACKED_PAIR_PRODUCTS=$(PACKED_PAIR_PRODUCTS) -DECC_PACKED_POLY_STATE=$(PACKED_POLY_STATE) \
       -DECC_PACKED_DIRECT_REDUCE=$(PACKED_DIRECT_REDUCE) \
       -DECC_PACKED_DIRECT_REDUCE=$(PACKED_DIRECT_REDUCE) -DECC_PACKED_ADD_COMBINE=$(PACKED_ADD_COMBINE) \
       -DECC_PACKED_GENERATED_PRODUCT=$(PACKED_GENERATED_PRODUCT) \
       -DECC_PACKED_INLINE_POLY=$(PACKED_INLINE_POLY) \
       -DECC_PACKED_CLMAD=$(PACKED_CLMAD) -DECC_PACKED_CLMAD_SQUARE=$(PACKED_CLMAD_SQUARE) -DECC_PACKED_KARAT3=$(PACKED_KARAT3) \
       -DECC_PACKED_WEIGHTED_PREFIX=$(PACKED_WEIGHTED_PREFIX) \
       -DECC_PACKED_COMPACT_STATE=$(PACKED_COMPACT_STATE) \
       -DECC_PACKED_SHARED_SIGMA=$(PACKED_SHARED_SIGMA) \
       -DECC_PACKED_TOP_CLMAD=$(PACKED_TOP_CLMAD) \
       -DECC_PACKED_STATE_TILE=$(PACKED_STATE_TILE) \
       -DECC_PACKED_ADD_COMBINE=$(PACKED_ADD_COMBINE) \
       -DECC_PACKED_PAIR_ILP=$(PACKED_PAIR_ILP) \
       -DECC_UNROLL_SLOTS=$(UNROLL_SLOTS) \
       -DECC_PACKED_SLOT_PREFETCH=$(PACKED_SLOT_PREFETCH) \
       -DECC_PACKED_SLOT_PIPELINE=$(PACKED_SLOT_PIPELINE) \
       -DECC_SIGMA_FUSED=$(SIGMA_FUSED) \
       -DECC_SIGMA_FUSED_LATE_Y=$(SIGMA_FUSED_LATE_Y) \
       -DECC_SIGMA_FUSED_SHARED_SLOTS=$(SIGMA_FUSED_SHARED_SLOTS) \
       -DECC_SIGMA_SQUARE_TABLE=$(SIGMA_SQUARE_TABLE) \
       -DECC_PACKED_L2_PERSIST=$(PACKED_L2_PERSIST) \
       -DECC_PACKED_TOP_HOIST=$(PACKED_TOP_HOIST) \
       -DECC_PACKED_ONB_INV=$(PACKED_ONB_INV) \
       -DECC_PACKED_FROM_REDUCED=$(PACKED_FROM_REDUCED) \
       -DECC_PACKED_SIGMA_TABLE=$(PACKED_SIGMA_TABLE) \
       -DECC_PACKED_CLMUL_FLAT=$(PACKED_CLMUL_FLAT) \
       -DECC_PACKED_PAIR_CLMUL=$(PACKED_PAIR_CLMUL) \
       -DECC_PACKED_ALU_SQR=$(PACKED_ALU_SQR) \
       -DECC_WALK_TABLE=$(WALK_TABLE) -DECC_TABLE_BRANCHES=$(TABLE_BRANCHES) \
       -DECC_TABLE_PIVOT_BYTES=$(TABLE_PIVOT_BYTES) \
       -DECC_TABLE_PHASE_POPC=$(TABLE_PHASE_POPC) \
       -DECC_TABLE_TAG_DENOM=$(TABLE_TAG_DENOM) -DECC_TABLE_FUSED=$(TABLE_FUSED) -DECC_TABLE_FUSED_PIPE=$(TABLE_FUSED_PIPE) -DECC_PHASE_PROFILE=$(PHASE_PROFILE) -DECC_TABLE_PIPE_SELECT=$(TABLE_PIPE_SELECT) -DECC_TABLE_SPLIT_FORWARD=$(TABLE_SPLIT_FORWARD) -DECC_TABLE_BATCH_HINTS=$(TABLE_BATCH_HINTS) -DECC_TABLE_BLOCK_HINTS=$(TABLE_BLOCK_HINTS) -DECC_TABLE_HINT_QUEUE=$(TABLE_HINT_QUEUE) -DECC_CYCLE_FAST2=$(CYCLE_FAST2) -DECC_CYCLE_PROFILE=$(CYCLE_PROFILE) -DECC_PACKED_CHAIN_FIRST=$(PACKED_CHAIN_FIRST) \
       -DECC_PACKED_CHAINS=$(PACKED_CHAINS) \
       -DECC_TABLE_GLOBAL_HINTS=$(TABLE_GLOBAL_HINTS) \
       -DECC_TABLE_GLOBAL_HINT_THREADS=$(TABLE_GLOBAL_HINT_THREADS) \
       -DECC_TABLE_GLOBAL=$(TABLE_GLOBAL) \
       -DECC_TABLE_ADDEND_GLOBAL=$(TABLE_ADDEND_GLOBAL) \
       -DECC_PACKED_ALU_SQUARE=$(PACKED_ALU_SQUARE) \
       -DECC_PACKED_SQUARE_TABLE=$(PACKED_SQUARE_TABLE) \
       -DECC_PACKED_INV_POLY=$(PACKED_INV_POLY) \
       -DECC_WITNESS=$(WITNESS) \
       -DECC_PACKED_NATIVE_PRODUCT=$(PACKED_NATIVE_PRODUCT) \
       -DECC_PACKED_NATIVE_REDUCE=$(PACKED_NATIVE_REDUCE) \
       -DECC_PACKED_FUSED_SIGMA=$(PACKED_FUSED_SIGMA) \
       -DECC_PACKED_INLINE=$(PACKED_INLINE) \
       -DECC_PACKED_BLOCK_INVERSE=$(PACKED_BLOCK_INVERSE) \
       -DECC_PACKED_BATCH_SPLIT=$(PACKED_BATCH_SPLIT) \
       -DECC_PACKED_LAST_SLOT_CACHE=$(PACKED_LAST_SLOT_CACHE) \
       -DECC_PACKED_TAIL_LAYOUT=$(PACKED_TAIL_LAYOUT) \
       -DECC_PACKED_SHARED_X_SLOTS=$(PACKED_SHARED_X_SLOTS) \
       -DECC_PACKED_XONLY_23=$(PACKED_XONLY_23) \
       -DECC_PACKED_XONLY_DOUBLE_ONLY=$(PACKED_XONLY_DOUBLE_ONLY) \
       -DECC_PACKED_XONLY_BRIDGE3=$(PACKED_XONLY_BRIDGE3) \
       -DECC_PACKED_XONLY_BRIDGE1_COMMON=$(PACKED_XONLY_BRIDGE1_COMMON) \
       -DECC_PACKED_XONLY_BRIDGE_MOD72=$(PACKED_XONLY_BRIDGE_MOD72) \
       -DECC_PACKED_XONLY_SKIP_EMPTY_BRIDGE=$(PACKED_XONLY_SKIP_EMPTY_BRIDGE) \
       -DECC_PACKED_XONLY_ARITHMETIC_ONLY=$(PACKED_XONLY_ARITHMETIC_ONLY) \
       -DECC_PACKED_XONLY_POLY_SELECT=$(PACKED_XONLY_POLY_SELECT) \
       -DECC_PACKED_XONLY_POLY_DP_CONVERT=$(PACKED_XONLY_POLY_DP_CONVERT) \
       -DECC_PACKED_XONLY_POLY_SELECT_MASK=$(PACKED_XONLY_POLY_SELECT_MASK) \
       -DECC_PACKED_XONLY_POLY_SELECT_TARGET=$(PACKED_XONLY_POLY_SELECT_TARGET)

all: cpu

# The generated leaf is sized to a register file: the largest halving-chain
# multiply whose peak liveness still fits REGS live values.  255 is a GPU's
# register budget and gives a 66-word leaf at m=131.
#
# A smaller leaf for the host is the obvious thing to want and it is measurably
# wrong.  Single core, M4 Pro, NEON words, curve 131, median of five:
#
#   REGS=32   leaf 9     9.820 M it/s
#   REGS=64   leaf 17   10.052
#   REGS=128  leaf 33   10.799
#   REGS=255  leaf 66   11.353
#
# The leaf spills either way -- 59% of the emitted multiply is loads and stores
# even at leaf 66 -- and spilling to a hot stack is cheaper than the extra
# Karatsuba level a small leaf buys.  Both targets want the same leaf.
REGS ?= 255

generate:
	cd codegen && python3 gen.py --out ../generated --regs $(REGS)
	cd codegen && python3 genpacked.py
	cd codegen && python3 gensigma.py
	cd codegen && python3 genpolyreduce.py
	cd codegen && python3 gendirectreduce.py
	python3 codegen/genpackedproduct.py
	python3 codegen/genfusedsigma.py

# FPGA datapath: the same IR emitted as pipelined Verilog instead of C.
# See ../FPGA-CEILING.md; needs python3 only, no FPGA toolchain.
verilog:
	cd codegen && python3 genverilog.py --out ../generated/rtl

# Read the emitted RTL back and simulate it against field.Onb.  Checks the
# emission (stage crossings, register chains, output wiring), not the IR.
check-verilog:
	cd codegen && python3 testverilog.py --rtl ../generated/rtl

cpu: ecc2k130-cpu

ecc2k130-cpu: src/main.cu $(HDRS) $(GEN)
	$(CXX) -O3 -std=c++17 $(ARCHFLAGS) $(OMPFLAGS) $(DEFS) -DECC_NO_CUDA \
	    -Wall -Wno-unused-function -Wno-unknown-pragmas -x c++ src/main.cu -o $@

gpu: ecc2k130

ecc2k130: src/main.cu $(HDRS) $(GEN)
	$(NVCC) -O3 -std=c++17 $(ARCH) $(DEFS) $(GPUFLAGS) -lineinfo -Xcompiler -O3 \
	    -Xcompiler -fopenmp src/main.cu -o $@ -lgomp

# Modal entry-point parameters must survive the trip through click; see
# codegen/checkcli.py.  Cheap, needs nothing installed, and the failure it
# catches is otherwise invisible until someone runs that entry point.
check-cli:
	python3 -m unittest discover -s codegen -p testbenchmarkhardware.py
	python3 codegen/genfusedsigma.py --check
	python3 codegen/genpacked.py --check --out include/packedtransform131.h
	python3 codegen/gensigma.py --check --out include/packedsigma131.h
	python3 codegen/genpolyreduce.py --check --out include/packedpolyreduce131.h
	python3 codegen/gendirectreduce.py --check --out include/packeddirectreduce131.h
	python3 codegen/checkcli.py modal_app.py
	python3 codegen/checkcli.py profile_diagnostic.py
	python3 codegen/checkcli.py packed_audit.py
	python3 codegen/checkcli.py polystate_audit.py
	python3 codegen/autolab.py --self-test
	python3 -m unittest discover -s codegen -p 'test*report.py'
	python3 -m unittest discover -s codegen -p 'testautotune*.py'
	python3 -m unittest discover -s codegen -p 'testpolystateaudit.py'
	python3 -m unittest discover -s codegen -p 'testdirectbuild.py'
	python3 -m unittest discover -s codegen -p 'testnativebench.py'
	python3 -m unittest discover -s codegen -p 'testirschedule.py'
	python3 -m unittest discover -s codegen -p 'testwdsat.py'
	python3 -m unittest discover -s codegen -p 'testlambdaprojective.py'
	python3 -m unittest benchmarks/test_dp_identity.py

# Exhaustively rule out sigma-walk orbit returns through CYCLE_LENGTH.
# Equivariance alone does not rule out cycles. This check does not cover the
# additive table walk; test-cycle-rule and test-cycle-escape cover its policy.
CYCLE_LENGTH ?= 24
check-cycles:
	cargo run --release --quiet --manifest-path ../Cargo.toml --bin ecc2k-guard -- \
	    cycles --max-length $(CYCLE_LENGTH)

# The campaign dashboard (ecc2k-status, which replaced aws/status.py) and the
# Python-compatible JSON and number formatting it prints through. Their
# expected bytes were printed by CPython.
test-status:
	cargo test --release --quiet --manifest-path ../Cargo.toml --lib -- \
	    ecc2k130_status ecc2k130_pyjson

# Offline half of aws/dp_ingest.py (Phase A). Own workspace under
# ecc2k130/dp-ingest/ so the IC autolab frozen root Cargo.lock stays untouched.
# Live daemon remains aws/dp_ingest.py until Phase B/C.
test-dp-ingest:
	cargo +1.98 test --quiet --manifest-path dp-ingest/Cargo.toml
	cargo +1.98 clippy --quiet --manifest-path dp-ingest/Cargo.toml --all-targets -- -D warnings

# SAT-based point decomposition (codegen/decomp.py, indexcalc.py).  Kept out of
# `test` because it needs solvers the rest of the repo does not:
#   pip install python-sat pycryptosat
test-decomp:
	cd codegen && python3 testdecomp.py

# Drive the whole campaign shape on a curve that finishes in seconds: several
# workers with disjoint seed spaces, one resumed from its checkpoint, and a
# collision that only the merge can see because no single worker holds both
# halves.  Those are the parts a long run only reaches at the very end.
rehearse: ecc2k130-cpu
	./rehearse.sh

build/test-production: src/testproduction.cpp src/main.cu $(HDRS) $(GEN)
	mkdir -p build
	$(CXX) -O2 -std=c++17 -DECC_NO_CUDA -DECC_BATCH=2 \
	    -DECC_WALK_TABLE=$(WALK_TABLE) -DECC_TABLE_BRANCHES=$(TABLE_BRANCHES) -DECC_TABLE_PIVOT_BYTES=$(TABLE_PIVOT_BYTES) \
	    -Wno-unknown-pragmas -Wno-unused-function src/testproduction.cpp -o $@

# The walks behind a corpus, orbit by orbit, for the status page's forest
# figure (docs/ecc2k130-status/walk-forest/README.md).  Host only.
build/trailforest: src/trailforest.cpp $(HDRS) $(GEN)
	mkdir -p build
	$(CXX) -O2 -std=c++17 -fopenmp $(DEFS) -DECC_NO_CUDA -Wall -Wno-unused-function -Wno-unused-variable \
	    -Wno-unknown-pragmas src/trailforest.cpp -o $@

trailforest: build/trailforest

# Strict host replay for versioned table-v3 corpora.  Keep every rule switch
# explicit: this is an evidence checker for the selected reference walk, not a
# build that inherits whichever experimental knobs happen to be on a command
# line.  Run-id, DP weight and producer max-iters remain required runtime
# context because the v3 file header does not carry them.
TABLE_V3_REPLAY_DEFS = -DECC_NO_CUDA -DECC_WALK_TABLE=1 -DECC_TABLE_BRANCHES=8 \
	-DECC_TABLE_PIVOT_BYTES=1 -DECC_TABLE_PHASE_POPC=0 -DECC_CYCLE_FAST2=1

build/table-v3-replay: src/tablev3replay.cpp include/tablev3replay.h $(HDRS) $(GEN)
	mkdir -p build
	$(CXX) -O2 -std=c++17 $(TABLE_V3_REPLAY_DEFS) -Wall -Wextra \
	    -Wno-unused-function -Wno-unknown-pragmas src/tablev3replay.cpp -o $@

table-v3-replay: build/table-v3-replay

build/test-table-v3-replay: src/testtablev3replay.cpp include/tablev3replay.h $(HDRS) $(GEN)
	mkdir -p build
	$(CXX) -O2 -std=c++17 $(TABLE_V3_REPLAY_DEFS) -Wall -Wextra \
	    -Wno-unused-function -Wno-unknown-pragmas src/testtablev3replay.cpp -o $@

test-table-v3-replay: build/table-v3-replay build/test-table-v3-replay
	./build/test-table-v3-replay
	./build/table-v3-replay --help >/dev/null

# Orbit witnesses for a corpus: the j-counts a cairn piecework node can check
# with one double scalar multiplication.  Host only.
build/witness: src/witness.cpp $(HDRS) $(GEN)
	mkdir -p build
	$(CXX) -O2 -std=c++17 $(OMPFLAGS) $(DEFS) -DECC_NO_CUDA -Wall -Wno-unused-function -Wno-unused-variable \
	    -Wno-unknown-pragmas src/witness.cpp -o $@

witness: build/witness

# The witness generator end to end: corpus -> claims, the structural rules a
# payer applies, and (with CAIRN_ROOT or --cairn) cairn's own checker.
test-witness: build/witness ecc2k130-cpu
	python3 test_witness.py $(if $(CAIRN_ROOT),--cairn $(CAIRN_ROOT),)

test-production: ecc2k130-cpu build/test-production
	./build/test-production
	python3 -m unittest discover -s aws -p test_certification.py -v
	python3 -m unittest discover -s aws -p test_bootstrap_contract.py -v
	python3 -m unittest discover -s aws -p test_campaign_guard.py -v
	python3 -m unittest discover -s aws -p test_worker_family.py -v
	python3 -m unittest discover -s aws -p test_worker_banner.py -v
	python3 -m unittest discover -s aws -p test_fleet_roll.py -v
	python3 -m unittest discover -s aws -p test_rollout.py -v
	python3 -m unittest discover -s aws -p test_dp_ingest.py -v
	python3 -m unittest discover -s aws -p test_worker_spool.py -v
	python3 -m unittest discover -s aws -p test_controlplane.py -v
	python3 -m unittest discover -s aws -p test_seed_registry.py -v
	python3 -m unittest discover -s aws -p test_seed_audit.py -v
	python3 -m unittest discover -s . -p test_modal_sync.py -v
	python3 -m unittest discover -s . -p test_modal_app.py -v
	python3 -m unittest discover -s . -p test_modal_campaign.py -v

# The control plane against real Postgres, Redis and S3 rather than SQLite, a
# fake and a directory.  `services.sh` brings the three up (docker compose
# where there is a daemon, the host's own binaries where there is not), and
# takes them down again whether the suite passed or failed.
#
# Not part of `test` or `test-production`: those must pass on a laptop with no
# services and no network, which is what test_controlplane.py is for.
test-integration:
	aws/integration/services.sh up
	@set -a; . build/integration/env; set +a; \
	  python3 -m unittest discover -s aws -p test_integration.py -v; rc=$$?; \
	  aws/integration/services.sh down; \
	  exit $$rc

# The clients production imports lazily.  Separate from the target above so
# that a run cannot quietly install into somebody's system python.
test-integration-deps:
	python3 -m pip install -r aws/integration/requirements.txt

certify-local: ecc2k130-cpu build/test-production
	python3 aws/certify.py --output build/certification-local.json

# Offline search over the build space against ptxas output, which needs no GPU.
# It ranks nothing it can measure the speed of -- it produces the few
# configurations worth spending GPU time on, for `modal run
# modal_app.py::autotune` to actually measure.  Uses nvcc when CUDAPATH has one
# and the check-cuda clang route when it does not; `modal run
# modal_app.py::autolab` runs the same search in a CPU container that has nvcc.
# Where a walk iteration actually goes, weighted by how many times a step calls
# each routine, plus the model's error against every rate ever measured.  Needs
# PTX for the build in question; make ptx or autolab leaves one behind.
perfmodel:
	cd codegen && python3 perfmodel.py --ptx $(PTX)

autolab:
	cd codegen && python3 autolab.py --cuda-path=$(CUDAPATH) --ptxas=$(PTXAS) \
	    --clang=$(CLANG) --arch=$(patsubst sm_%,%,$(SMARCH))

test-schedule:
	mkdir -p build
	$(CXX) -O2 -std=c++17 -DECC_STREAM_KARAT=0 -Wno-unknown-pragmas src/testschedule.cpp -o build/test-schedule-base
	$(CXX) -O2 -std=c++17 -DECC_STREAM_KARAT=1 -Wno-unknown-pragmas src/testschedule.cpp -o build/test-schedule-stream
	./build/test-schedule-base
	./build/test-schedule-stream

test-packed:
	mkdir -p build
	$(CXX) -O2 -std=c++17 -DECC_PACKED_SINGLE_PRODUCT=0 -DECC_PACKED_DIRECT_REDUCE=0 -DECC_PACKED_GENERATED_PRODUCT=0 -Wno-unknown-pragmas src/testpacked.cpp -o build/test-packed-base
	$(CXX) -O2 -std=c++17 -DECC_PACKED_SINGLE_PRODUCT=1 -DECC_PACKED_DIRECT_REDUCE=0 -DECC_PACKED_GENERATED_PRODUCT=0 -Wno-unknown-pragmas src/testpacked.cpp -o build/test-packed-single
	./build/test-packed-base
	./build/test-packed-single

test-packed-network:
	mkdir -p build
	$(CXX) -O2 -std=c++17 -DECC_PACKED_SINGLE_PRODUCT=1 -DECC_PACKED_BY_VALUE=1 -DECC_PACKED_PERM_SIGMA=3 -DECC_PACKED_DIRECT_REDUCE=0 -DECC_PACKED_GENERATED_PRODUCT=0 -Wno-unknown-pragmas src/testpacked.cpp -o build/test-packed-network
	$(CXX) -O2 -std=c++17 -DECC_PACKED_SINGLE_PRODUCT=1 -DECC_PACKED_BY_VALUE=1 -DECC_PACKED_PERM_SIGMA=3 -DECC_PACKED_DIRECT_REDUCE=1 -DECC_PACKED_GENERATED_PRODUCT=0 -Wno-unknown-pragmas src/testpacked.cpp -o build/test-packed-network-direct
	$(CXX) -O2 -std=c++17 -DECC_PACKED_SINGLE_PRODUCT=1 -DECC_PACKED_BY_VALUE=1 -DECC_PACKED_PERM_SIGMA=3 -DECC_PACKED_DIRECT_REDUCE=1 -DECC_PACKED_GENERATED_PRODUCT=1 -Wno-unknown-pragmas src/testpacked.cpp -o build/test-packed-network-generated
	$(CXX) -O2 -std=c++17 -DECC_PACKED_SINGLE_PRODUCT=1 -DECC_PACKED_BY_VALUE=1 -DECC_PACKED_PERM_SIGMA=3 -DECC_PACKED_DIRECT_REDUCE=1 -DECC_PACKED_ADD_COMBINE=1 -Wno-unknown-pragmas src/testpacked.cpp -o build/test-packed-network-add
	./build/test-packed-network
	./build/test-packed-network-direct
	./build/test-packed-network-generated
	./build/test-packed-network-add

test-sigma-square-table-native:
	CXX="$(CXX)" /bin/sh benchmarks/sigma-square-table/run-native.sh

test-generated-product:
	python3 codegen/genpackedproduct.py --check
	python3 -m unittest discover -s codegen -p testgeneratedproduct.py

test-clmad:
	python3 -m unittest discover -s codegen -p testclmad.py
	mkdir -p build
	$(CXX) -O2 -std=c++17 -DECC_PACKED_SINGLE_PRODUCT=1 -DECC_PACKED_BY_VALUE=1 -DECC_PACKED_PERM_SIGMA=3 -DECC_PACKED_DIRECT_REDUCE=1 -DECC_PACKED_GENERATED_PRODUCT=1 -DECC_PACKED_CLMAD=1 -Wno-unknown-pragmas src/testpacked.cpp -o build/test-packed-clmad-host
	$(CXX) -O2 -std=c++17 -DECC_PACKED_SINGLE_PRODUCT=1 -DECC_PACKED_BY_VALUE=1 -DECC_PACKED_PERM_SIGMA=3 -DECC_PACKED_DIRECT_REDUCE=1 -DECC_PACKED_GENERATED_PRODUCT=1 -DECC_PACKED_CLMAD=1 -DECC_PACKED_TOP_CLMAD=1 -Wno-unknown-pragmas src/testpacked.cpp -o build/test-packed-topclmad-host
	$(CXX) -O2 -std=c++17 -DECC_PACKED_SINGLE_PRODUCT=1 -DECC_PACKED_BY_VALUE=1 -DECC_PACKED_PERM_SIGMA=3 -DECC_PACKED_DIRECT_REDUCE=1 -DECC_PACKED_GENERATED_PRODUCT=1 -DECC_PACKED_CLMAD=1 -DECC_PACKED_PAIR_ILP=1 -Wno-unknown-pragmas src/testpacked.cpp -o build/test-packed-pair-ilp-host
	$(CXX) -O2 -std=c++17 -DECC_PACKED_SINGLE_PRODUCT=1 -DECC_PACKED_BY_VALUE=1 -DECC_PACKED_PERM_SIGMA=3 -DECC_PACKED_DIRECT_REDUCE=1 -DECC_PACKED_GENERATED_PRODUCT=1 -DECC_PACKED_CLMAD=1 -DECC_PACKED_TOP_HOIST=1 -Wno-unknown-pragmas src/testpacked.cpp -o build/test-packed-top-hoist-host
	$(CXX) -O2 -std=c++17 -DECC_PACKED_SINGLE_PRODUCT=1 -DECC_PACKED_BY_VALUE=1 -DECC_PACKED_PERM_SIGMA=3 -DECC_PACKED_DIRECT_REDUCE=1 -DECC_PACKED_GENERATED_PRODUCT=1 -DECC_PACKED_CLMAD=1 -DECC_PACKED_UNROLL_INV=1 -DECC_PACKED_ONB_INV=1 -Wno-unknown-pragmas src/testpacked.cpp -o build/test-packed-onb-inv-host
	$(CXX) -O2 -std=c++17 -DECC_PACKED_SINGLE_PRODUCT=1 -DECC_PACKED_BY_VALUE=1 -DECC_PACKED_PERM_SIGMA=3 -DECC_PACKED_DIRECT_REDUCE=1 -DECC_PACKED_GENERATED_PRODUCT=1 -DECC_PACKED_CLMAD=1 -DECC_PACKED_FROM_REDUCED=1 -Wno-unknown-pragmas src/testpacked.cpp -o build/test-packed-from-reduced-host
	$(CXX) -O2 -std=c++17 -DECC_PACKED_SINGLE_PRODUCT=1 -DECC_PACKED_BY_VALUE=1 -DECC_PACKED_PERM_SIGMA=3 -DECC_PACKED_DIRECT_REDUCE=1 -DECC_PACKED_GENERATED_PRODUCT=1 -DECC_PACKED_CLMAD=1 -DECC_PACKED_PAIR_ILP=1 -DECC_PACKED_PAIR_CLMUL=1 -Wno-unknown-pragmas src/testpacked.cpp -o build/test-packed-pair-clmul-host
	$(CXX) -O2 -std=c++17 -DECC_PACKED_SINGLE_PRODUCT=1 -DECC_PACKED_BY_VALUE=1 -DECC_PACKED_PERM_SIGMA=3 -DECC_PACKED_DIRECT_REDUCE=1 -DECC_PACKED_GENERATED_PRODUCT=1 -DECC_PACKED_CLMAD=1 -DECC_PACKED_ALU_SQUARE=1 -DECC_PACKED_ALU_SQR=1 -Wno-unknown-pragmas src/testpacked.cpp -o build/test-packed-alu-sqr-host
	./build/test-packed-clmad-host
	./build/test-packed-topclmad-host
	./build/test-packed-pair-ilp-host
	./build/test-packed-top-hoist-host
	./build/test-packed-onb-inv-host
	./build/test-packed-from-reduced-host
	./build/test-packed-pair-clmul-host
	./build/test-packed-alu-sqr-host

test-clmad-square:
	python3 -m unittest discover -s codegen -p testclmad.py
	mkdir -p build
	$(CXX) -O2 -std=c++17 -DECC_PACKED_SINGLE_PRODUCT=1 -DECC_PACKED_BY_VALUE=1 -DECC_PACKED_PERM_SIGMA=3 -DECC_PACKED_DIRECT_REDUCE=1 -DECC_PACKED_GENERATED_PRODUCT=1 -DECC_PACKED_CLMAD=1 -DECC_PACKED_CLMAD_SQUARE=1 -Wno-unknown-pragmas src/testpacked.cpp -o build/test-packed-clmad-square-host
	./build/test-packed-clmad-square-host

test-karat3:
	mkdir -p build
	$(CXX) -O2 -std=c++17 -DECC_PACKED_SINGLE_PRODUCT=1 -DECC_PACKED_BY_VALUE=1 -DECC_PACKED_PERM_SIGMA=3 -DECC_PACKED_DIRECT_REDUCE=1 -DECC_PACKED_GENERATED_PRODUCT=1 -DECC_PACKED_CLMAD=1 -DECC_PACKED_KARAT3=1 -Wno-unknown-pragmas src/testpacked.cpp -o build/test-packed-karat3-host
	./build/test-packed-karat3-host
	$(CXX) -O2 -std=c++17 -DECC_PACKED_CLMAD=1 -DECC_PACKED_KARAT3=1 -Wno-unknown-pragmas src/testkarat3.cpp -o build/test-karat3-product
	./build/test-karat3-product

test-packed-cuda:
	mkdir -p build
	$(NVCC) -O3 -std=c++17 $(ARCH) $(DEFS) src/testpackedcuda.cu -o build/test-packed-cuda
	./build/test-packed-cuda

test-packed-block-inverse-cuda:
	mkdir -p build
	$(NVCC) -O3 -std=c++17 $(ARCH) $(DEFS) src/testblockinversecuda.cu -o build/test-packed-block-inverse-cuda
	./build/test-packed-block-inverse-cuda

test-packed-storage-cuda:
	mkdir -p build
	$(NVCC) -O3 -std=c++17 $(ARCH) $(DEFS) src/testpackedstatecuda.cu -o build/test-packed-storage-cuda
	./build/test-packed-storage-cuda

test-shared-sigma-cuda:
	mkdir -p build
	$(NVCC) -O3 -std=c++17 $(ARCH) $(DEFS) src/testsharedsigmacuda.cu -o build/test-shared-sigma-cuda
	./build/test-shared-sigma-cuda

# The table walk's cycle rule on its own: every tau-relation 4-cycle and every
# pairwise cycle up to 8 steps refused, the rule a class function
# (WALK-CONSTANT.md section 11).  No curve, a second.
test-cycle-rule:
	mkdir -p build
	$(CXX) -O2 -std=c++17 -Wall -Wno-unused-function src/testcyclerule.cpp -o build/test-cycle-rule
	./build/test-cycle-rule

# Device primitives of the table walk against the host reference; needs a GPU.
# The same probe on the host, for both selection layouts, so a layout change
# is held to the reference before any GPU sees it.  PHASE_POPC is checked on
# top of each pivot layout (CHEAPER-SELECTION.md).
TABLE_PROBE_POINTS ?= 4096
test-table-walk-host:
	mkdir -p build
	$(CXX) -O2 -std=c++17 -DECC_WALK_TABLE=1 -DECC_TABLE_PIVOT_BYTES=0 -DECC_TABLE_PHASE_POPC=0 -Wno-unknown-pragmas -Wno-unused-function -DECC_TABLE_PROBE_POINTS=$(TABLE_PROBE_POINTS) src/testtablewalkhost.cpp -o build/test-table-walk-host-nibble
	$(CXX) -O2 -std=c++17 -DECC_WALK_TABLE=1 -DECC_TABLE_PIVOT_BYTES=1 -DECC_TABLE_PHASE_POPC=0 -Wno-unknown-pragmas -Wno-unused-function -DECC_TABLE_PROBE_POINTS=$(TABLE_PROBE_POINTS) src/testtablewalkhost.cpp -o build/test-table-walk-host-bytes
	$(CXX) -O2 -std=c++17 -DECC_WALK_TABLE=1 -DECC_TABLE_PIVOT_BYTES=0 -DECC_TABLE_PHASE_POPC=1 -Wno-unknown-pragmas -Wno-unused-function -DECC_TABLE_PROBE_POINTS=$(TABLE_PROBE_POINTS) src/testtablewalkhost.cpp -o build/test-table-walk-host-nibble-popc
	$(CXX) -O2 -std=c++17 -DECC_WALK_TABLE=1 -DECC_TABLE_PIVOT_BYTES=1 -DECC_TABLE_PHASE_POPC=1 -Wno-unknown-pragmas -Wno-unused-function -DECC_TABLE_PROBE_POINTS=$(TABLE_PROBE_POINTS) src/testtablewalkhost.cpp -o build/test-table-walk-host-bytes-popc
	./build/test-table-walk-host-nibble
	./build/test-table-walk-host-bytes
	./build/test-table-walk-host-nibble-popc
	./build/test-table-walk-host-bytes-popc

# The full RTX PRO 6000 redesign arithmetic path on the same host/reference
# probe. Keep this separate from the broad layout test: the polynomial cold
# probe is intentionally expensive on a CPU, while this small fixture catches
# basis, square-table and reduced-conversion regressions before a GPU launch.
TABLE_REDESIGN_PROBE_POINTS ?= 64
test-table-walk-host-redesign:
	mkdir -p build
	$(CXX) -O2 -std=c++17 -DECC_WALK_TABLE=1 -DECC_TABLE_BRANCHES=8 -DECC_TABLE_PIVOT_BYTES=1 -DECC_TABLE_PHASE_POPC=0 -DECC_CYCLE_FAST2=1 \
	    -DECC_PACKED_INV_POLY=2 -DECC_PACKED_SQUARE_TABLE=1 -DECC_PACKED_FROM_REDUCED=1 \
	    -DECC_PACKED_ALU_SQUARE=1 -DECC_PACKED_DIRECT_REDUCE=1 -DECC_PACKED_GENERATED_PRODUCT=1 \
	    -DECC_PACKED_INLINE_POLY=3 -Wno-unknown-pragmas -Wno-unused-function \
	    -DECC_TABLE_PROBE_POINTS=$(TABLE_REDESIGN_PROBE_POINTS) src/testtablewalkhost.cpp -o build/test-table-walk-host-redesign
	./build/test-table-walk-host-redesign
.PHONY: test-table-walk-host-redesign

# Host static price of twPhase byte-table vs popc planes (CHEAPER-SELECTION.md).
test-phase-cost:
	mkdir -p build
	$(CXX) -O3 -std=c++17 -DPHASE_POPC=0 -Wno-unknown-pragmas -Wno-unused-function src/testphasecost.cpp -o build/test-phase-cost-table
	$(CXX) -O3 -std=c++17 -DPHASE_POPC=1 -Wno-unknown-pragmas -Wno-unused-function src/testphasecost.cpp -o build/test-phase-cost-popc
	./build/test-phase-cost-table
	./build/test-phase-cost-popc

# GPU paired measurement of TABLE_PHASE_POPC (needs Modal/RunPod/AWS + a 6000).
#   make bench-cheaper-selection OUT=/tmp/cheaper-selection
bench-cheaper-selection:
	@test -n "$(OUT)" || (echo "usage: make bench-cheaper-selection OUT=DIR"; exit 1)
	modal run --detach modal_job.py --job benchmarks/cheaper-selection/gpujob.sh --out "$(OUT)" --gpu "RTX PRO 6000"

# The walks' iteration constants on the device walks' own host reference
# (WALK-CONSTANT.md): the n = 23 rows of benchmarks/walk-constant, at the
# campaign's H = 8.  About ten minutes on four cores; not part of `test`.
WALK_CONSTANT_FLAGS = -O2 -std=c++17 -DECC_WALK_TABLE=1 -DECC_TABLE_PIVOT_BYTES=0 -Wno-unknown-pragmas -Wno-unused-function -pthread
walk-constant:
	mkdir -p build
	$(CXX) $(WALK_CONSTANT_FLAGS) -DECC_TABLE_BRANCHES=8 src/walkconstant.cpp -o build/walk-constant-host-h8
	$(CXX) $(WALK_CONSTANT_FLAGS) -DECC_TABLE_BRANCHES=16 src/walkconstant.cpp -o build/walk-constant-host-h16
	for d in ecc2k130 native; do ./build/walk-constant-host-h8 --n 23 --walk table --dist $$d --walks 8 --trials 20000 --seed 230; done
	for d in ecc2k130 native; do ./build/walk-constant-host-h8 --n 23 --walk sigma --dist $$d --walks 8 --trials 20000 --seed 230; done

test-table-walk-cuda:
	mkdir -p build
	$(NVCC) -O3 -std=c++17 $(ARCH) $(filter-out -DECC_WALK_TABLE=% -DECC_WITNESS=%,$(DEFS)) -DECC_WALK_TABLE=1 -DECC_WITNESS=0 \
	    -Xcompiler -fopenmp src/testtablewalkcuda.cu -o build/test-table-walk-cuda -lgomp
	./build/test-table-walk-cuda
	$(NVCC) -O3 -std=c++17 $(ARCH) $(filter-out -DECC_WALK_TABLE=% -DECC_TABLE_ADDEND_GLOBAL=% -DECC_TABLE_PIVOT_BYTES=% -DECC_WITNESS=%,$(DEFS)) \
	    -DECC_WALK_TABLE=1 -DECC_TABLE_PIVOT_BYTES=1 -DECC_TABLE_ADDEND_GLOBAL=1 -DECC_WITNESS=0 \
	    -Xcompiler -fopenmp src/testtablewalkcuda.cu -o build/test-table-walk-cuda-addend-global -lgomp
	./build/test-table-walk-cuda-addend-global

test-timing:
	mkdir -p build
	$(CXX) -O1 -std=c++17 -Wno-unknown-pragmas src/testtiming.cpp -o build/test-timing
	./build/test-timing

# Every host word type this machine can build, against the scalar definitions.
# Built at the tuned architecture so the wide word under test is the one the
# walk will actually use.
test-logic:
	mkdir -p build
	$(CXX) -O2 -std=c++17 $(ARCHFLAGS) -Wno-unknown-pragmas src/testlogic.cpp -o build/test-logic
	./build/test-logic

test: check-cli check-cycles test-status test-schedule test-packed test-packed-network test-generated-product test-clmad test-clmad-square test-karat3 test-timing test-logic test-table-walk-host test-table-v3-replay test-cycle-rule test-cycle-escape test-cycle-profile check-verilog ecc2k130-cpu
	./ecc2k130-cpu --test

# End-to-end: recover planted discrete logarithms on the small curves, through
# both field backends.
break-small: ecc2k130-cpu
	@echo "-- normal basis --"
	@for i in 0 1 2 3; do \
	    ./ecc2k130-cpu --curve 23 --instance $$i --threads 1 --steps 32 --verify 2 | grep -E "planted|MISMATCH"; \
	done
	@for i in 0 1 2 3; do \
	    ./ecc2k130-cpu --curve 41 --instance $$i --threads 4 --steps 64 --verify 2 | grep -E "planted|MISMATCH"; \
	done
	@echo "-- polynomial basis --"
	@for i in 0 1 2 3; do \
	    ./ecc2k130-cpu --curve 19 --instance $$i --threads 1 --steps 16 --verify 2 | grep -E "planted|MISMATCH"; \
	done
	@for i in 0 1 2 3; do \
	    ./ecc2k130-cpu --curve 41 --poly-basis --instance $$i --threads 4 --steps 32 --verify 2 | grep -E "planted|MISMATCH"; \
	done

# Throughput on ECC2K-95's curve.
bench95: ecc2k130-cpu
	./ecc2k130-cpu --curve 97 --bench --steps 32 --launches 8 --verify 0

bench: ecc2k130-cpu
	./ecc2k130-cpu --curve 131 --bench --steps 32 --launches 8 --verify 0

# Type-check the whole CUDA translation unit, host and device, with clang.
# Needs no GPU and no nvcc, only a CUDA include tree at CUDAPATH.  On a machine
# with no toolkit installed, pip supplies one:
#   pip install nvidia-cuda-runtime-cu12 nvidia-cuda-nvcc-cu12 \
#               nvidia-curand-cu12 nvidia-cuda-cccl-cu12
# then point CUDAPATH at a directory holding those include trees merged into
# include/, and PTXAS at nvidia/cuda_nvcc/bin/ptxas.  CLANGARCH must be an
# architecture the local clang knows: clang 18 stops at sm_90.
check-cuda: src/main.cu $(HDRS) $(GEN)
	printf '#include <cuda_runtime.h>\nextern "C" cudaError_t cudaConfigureCall(dim3, dim3, size_t, cudaStream_t);\n' > build/cudashim.h
	$(CLANG) -x cuda --cuda-host-only --cuda-path=$(CUDAPATH) -nocudalib \
	    -Wno-unknown-cuda-version -O2 -std=c++17 $(DEFS) -include build/cudashim.h \
	    -c src/main.cu -o build/main_host.o
	$(CLANG) -x cuda --cuda-device-only --cuda-gpu-arch=$(CLANGARCH) --cuda-path=$(CUDAPATH) \
	    -nocudalib -Wno-unknown-cuda-version -O3 -std=c++17 $(DEFS) \
	    -include build/cudashim.h -S -o build/main_dev.ptx src/main.cu
	$(PTXAS) -arch=$(SMARCH) -O3 -v build/main_dev.ptx -o build/main_dev.cubin

# Where the packed walk's instructions go: no GPU needed.  Prints the ALU
# composition of a scalar update and what a throughput objective implies.
# See THROUGHPUT-30B.md.
path-cost:
	CUDAPATH=$(CUDAPATH) CLANG=$(CLANG)++ python3 path_cost.py $(PATH_COST_ARGS)

# Offline device compile: no GPU needed, reports registers, spills and size.
ptx: src/ptxspike.cu $(HDRS) $(GEN)
	$(CLANG) -x cuda --cuda-device-only --cuda-gpu-arch=$(CLANGARCH) --cuda-path=$(CUDAPATH) \
	    -nocudalib -Wno-unknown-cuda-version -O3 -std=c++17 $(DEFS) -S -o build/spike.ptx src/ptxspike.cu
	$(PTXAS) -arch=$(SMARCH) -O3 -v build/spike.ptx -o build/spike.cubin

# Run on Modal GPUs (see modal_app.py).
modal:
	modal run modal_app.py::bench --gpu H100

# Audited packed configuration for the RTX PRO 6000 Blackwell Server Edition.
RTX_PRO6000_WORKERS ?= 385024
RTX_PRO6000_COMPACT_STATE ?= 1
RTX_PRO6000_SHARED_SIGMA ?= 1
RTX_PRO6000_TOP_CLMAD ?= 0
RTX_PRO6000_CLMAD_SQUARE ?= 0
RTX_PRO6000_KARAT3 ?= 0
RTX_PRO6000_WALK_TABLE ?= 0
RTX_PRO6000_TABLE_PIVOT_BYTES ?= 0
RTX_PRO6000_ENV = ECC_GPU=RTX-PRO-6000 ECC_CUDA_VERSION=13.3.1 \
                 ECC_PACKED_SINGLE_PRODUCT=1 ECC_PACKED_CACHE_DENOM=1 \
                 ECC_PACKED_BY_VALUE=1 ECC_PACKED_PERM_SIGMA=3 \
                 ECC_PACKED_POLY_CHAIN=1 ECC_PACKED_UNROLL_INV=1 \
                 ECC_PACKED_PAIR_PRODUCTS=1 ECC_PACKED_POLY_STATE=1 ECC_PACKED_DIRECT_REDUCE=1 \
                 ECC_PACKED_GENERATED_PRODUCT=1 ECC_PACKED_CLMAD=1 ECC_PACKED_STATE_TILE=256 \
                 ECC_PACKED_WEIGHTED_PREFIX=2 ECC_PACKED_COMPACT_STATE=$(RTX_PRO6000_COMPACT_STATE) \
                 ECC_PACKED_SHARED_SIGMA=$(RTX_PRO6000_SHARED_SIGMA) \
                 ECC_PACKED_TOP_CLMAD=$(RTX_PRO6000_TOP_CLMAD) \
                 ECC_PACKED_CLMAD_SQUARE=$(RTX_PRO6000_CLMAD_SQUARE) \
                 ECC_PACKED_KARAT3=$(RTX_PRO6000_KARAT3) \
                 ECC_WALK_TABLE=$(RTX_PRO6000_WALK_TABLE) \
                 ECC_TABLE_PIVOT_BYTES=$(RTX_PRO6000_TABLE_PIVOT_BYTES)

bench-rtx-pro6000:
	$(RTX_PRO6000_ENV) $(MODAL) run modal_app.py::bench --packed \
	    --batch 16 --threads 256 --leaf 0 --min-blocks 2 \
	    --steps 1024 --launches 32 --repeats 3 --workers $(RTX_PRO6000_WORKERS)

# Confirmed 15.436677 B/s sigma configuration. The existing Modal preset
# above has its own historical validation; this native target gives the exact
# compiler settings used by the fused comparison without extending its cache.
PRO6000_SIGMA_FUSED_KNOBS = BATCH=16 THREADS=256 MINBLOCKS=2 \
    PACKED_SINGLE_PRODUCT=1 PACKED_CACHE_DENOM=1 PACKED_BY_VALUE=1 PACKED_PERM_SIGMA=3 \
    PACKED_POLY_CHAIN=1 PACKED_UNROLL_INV=1 PACKED_PAIR_PRODUCTS=1 PACKED_POLY_STATE=1 \
    PACKED_DIRECT_REDUCE=1 PACKED_GENERATED_PRODUCT=1 PACKED_CLMAD=1 PACKED_STATE_TILE=256 \
    PACKED_WEIGHTED_PREFIX=2 PACKED_COMPACT_STATE=1 PACKED_SHARED_SIGMA=1 \
    PACKED_INLINE_POLY=3 SIGMA_FUSED=1 WITNESS=0 WALK_TABLE=0
gpu-rtx-pro6000-sigma-fused:
	$(MAKE) -B ecc2k130 ARCH="$(PRO6000_ARCH)" $(PRO6000_SIGMA_FUSED_KNOBS)

# The fused sigma walk with the Frobenius steps read from shared-memory nibble
# tables (SIGMA-TABLE.md): 68 KB of shared memory per block, so one 512-thread
# block per SM keeps the register budget and resident warps of the preset.
PRO6000_SIGMA_TABLE_KNOBS = $(PRO6000_SIGMA_FUSED_KNOBS) THREADS=512 MINBLOCKS=1 PACKED_SIGMA_TABLE=1
gpu-rtx-pro6000-sigma-table:
	$(MAKE) -B ecc2k130 ARCH="$(PRO6000_ARCH)" $(PRO6000_SIGMA_TABLE_KNOBS)

# Same-walk DP32 candidate: retain the ordinary witness-carrying sigma walk,
# but evaluate I+sigma^j directly from the exact polynomial-coordinate nibble
# table. Keep this separate until device replay and DP corpus identity qualify
# it for the production campaign.
PRO6000_DP32_SIGMA_TABLE_KNOBS = BATCH=16 THREADS=512 MINBLOCKS=1 \
	PACKED_SINGLE_PRODUCT=1 PACKED_CACHE_DENOM=1 PACKED_BY_VALUE=1 PACKED_PERM_SIGMA=3 \
	PACKED_POLY_CHAIN=1 PACKED_UNROLL_INV=1 PACKED_PAIR_PRODUCTS=1 PACKED_POLY_STATE=1 \
	PACKED_DIRECT_REDUCE=1 PACKED_GENERATED_PRODUCT=1 PACKED_CLMAD=1 PACKED_STATE_TILE=256 \
	PACKED_WEIGHTED_PREFIX=2 PACKED_COMPACT_STATE=1 PACKED_SHARED_SIGMA=1 \
	PACKED_INLINE_POLY=3 PACKED_SIGMA_TABLE=1 SIGMA_FUSED=0 WITNESS=1 WALK_TABLE=0
gpu-rtx-pro6000-dp32-sigma-table:
	$(MAKE) -B ecc2k130 ARCH="$(PRO6000_ARCH)" $(PRO6000_DP32_SIGMA_TABLE_KNOBS)

# Headline protocol (A/A panel, five A/B pairs, 64 launches) for the table
# against the fused preset; benchmark-only, receipts in benchmarks/sigma-table/.
bench-rtx-pro6000-sigma-table:
	$(MODAL) run --detach modal_job.py --job benchmarks/sigma-table/gpujob-headline.sh \
	    --out $(TABLE_BENCH_OUT)
TABLE_BENCH_OUT ?= /tmp/ecc2k-sigma-table-benchmark
.PHONY: gpu-rtx-pro6000-sigma-table gpu-rtx-pro6000-dp32-sigma-table gpu-rtx-pro6000-iteration-redesign bench-rtx-pro6000-sigma-table

# Matched replay, A/A noise and five-pair confirmation; benchmark-only.
bench-rtx-pro6000-sigma-fused:
	$(MODAL) run --detach modal_job.py --job benchmarks/sigma-fused/gpujob-headline.sh \
	    --out $(FUSED_BENCH_OUT)
FUSED_BENCH_OUT ?= /tmp/ecc2k-sigma-fused-benchmark
.PHONY: gpu-rtx-pro6000-sigma-fused bench-rtx-pro6000-sigma-fused

# Architecture of the two RTX PRO 6000 builds below.  Override to run the same
# knob set on another part, e.g. PRO6000_ARCH="-gencode arch=compute_100,code=sm_100"
# for a B200 (benchmarks/fast-clmad/gpujob.sh derives it from the compute capability).
PRO6000_ARCH ?= -gencode arch=compute_120,code=sm_120
# The knob set that measured 20 B/s under the historical v2 table rule, now
# carrying the exact v3 schedule selected by the current matched comparison:
# split selection from the prefix product and reconverge hinted slots before
# the cold full-point probe.  See CYCLE-ESCAPE-V3.md and benchmarks/batch-hints.
# ALU squaring is on in this preset.  The five-pair block-v3 confirmation also
# selected the shared square table and out-of-line polynomial inversion as a
# bounded +0.66% same-walk engineering gain.  Values are expanded into the
# child make, and KNOBS remains the explicit override route.
# The knob set itself, so the recipe below and gpu-preset cannot drift apart.
PRO6000_20B_KNOBS = BATCH=16 THREADS=512 MINBLOCKS=1 \
	    PACKED_SINGLE_PRODUCT=1 PACKED_CACHE_DENOM=1 PACKED_BY_VALUE=1 PACKED_PERM_SIGMA=3 \
	    PACKED_POLY_CHAIN=1 PACKED_UNROLL_INV=1 PACKED_PAIR_PRODUCTS=1 PACKED_POLY_STATE=1 \
	    PACKED_DIRECT_REDUCE=1 PACKED_GENERATED_PRODUCT=1 PACKED_CLMAD=1 PACKED_STATE_TILE=256 \
	    PACKED_WEIGHTED_PREFIX=2 PACKED_COMPACT_STATE=1 PACKED_SHARED_SIGMA=1 \
	    WALK_TABLE=1 TABLE_PIVOT_BYTES=1 PACKED_PAIR_ILP=1 PACKED_L2_PERSIST=1 PACKED_ALU_SQUARE=$(PACKED_ALU_SQUARE) \
	    PACKED_SQUARE_TABLE=1 PACKED_INV_POLY=2 \
	    TABLE_TAG_DENOM=1 TABLE_PIPE_SELECT=1 TABLE_SPLIT_FORWARD=1 TABLE_BATCH_HINTS=1 \
	    TABLE_BLOCK_HINTS=1 TABLE_HINT_QUEUE=512 CYCLE_FAST2=1 PACKED_CHAIN_FIRST=1 \
	    PACKED_INLINE_POLY=3 UNROLL_SLOTS=1 PACKED_FROM_REDUCED=1
gpu-rtx-pro6000-20b: PACKED_ALU_SQUARE = 1
gpu-rtx-pro6000-20b:
	$(MAKE) -B ecc2k130 ARCH="$(PRO6000_ARCH)" $(PRO6000_20B_KNOBS)

# Major iteration-function redesign from ITERATION-FUNCTION.md: replace the
# per-step sigma/Frobenius path with the class-covariant r-adding table walk.
# This is the fully tuned v3 RTX PRO 6000 recipe, with a separate v3
# walk/corpus. It is intentionally never a campaign default or a drop-in
# DP32 worker.
PRO6000_ITERATION_REDESIGN_KNOBS = $(PRO6000_20B_KNOBS)
gpu-rtx-pro6000-iteration-redesign: PACKED_ALU_SQUARE = 1
gpu-rtx-pro6000-iteration-redesign:
	$(MAKE) -B ecc2k130 ARCH="$(PRO6000_ARCH)" $(PRO6000_ITERATION_REDESIGN_KNOBS)

# The same knob set with overrides appended: on a make command line the last
# assignment of a variable wins, so every knob and the geometry are reachable
# from KNOBS, which the recipes above cannot offer for the values they name.
#   make gpu-preset KNOBS="THREADS=256 MINBLOCKS=2 PACKED_TOP_CLMAD=1"
# benchmarks/autosweep/gpujob.sh builds its arms this way.
gpu-preset: PACKED_ALU_SQUARE = 1
gpu-preset:
	$(MAKE) -B ecc2k130 ARCH="$(PRO6000_ARCH)" $(PRO6000_20B_KNOBS) $(KNOBS)

# Build the queue control with exactly the same arithmetic/layout preset.
build/test-global-hints-cuda: benchmarks/global-hints/test_device.cu $(HDRS) $(GEN)
	mkdir -p build
	$(NVCC) -O3 -std=c++17 $(ARCH) $(DEFS) -Xcompiler -fopenmp $< -o $@ -lgomp
build/test-global-hints-cuda-128: TABLE_GLOBAL_HINT_THREADS = 128
build/test-global-hints-cuda-256: TABLE_GLOBAL_HINT_THREADS = 256
build/test-global-hints-cuda-512: TABLE_GLOBAL_HINT_THREADS = 512
build/test-global-hints-cuda-128 build/test-global-hints-cuda-256 build/test-global-hints-cuda-512: benchmarks/global-hints/test_device.cu $(HDRS) $(GEN)
	mkdir -p build
	$(NVCC) -O3 -std=c++17 $(ARCH) $(DEFS) -Xcompiler -fopenmp $< -o $@ -lgomp
compile-global-hints-geometries: PACKED_ALU_SQUARE = 1
compile-global-hints-geometries:
	$(MAKE) -B build/test-global-hints-cuda-128 ARCH="$(PRO6000_ARCH)" $(PRO6000_20B_KNOBS) TABLE_BLOCK_HINTS=0 TABLE_GLOBAL_HINTS=1
	$(MAKE) -B build/test-global-hints-cuda-256 ARCH="$(PRO6000_ARCH)" $(PRO6000_20B_KNOBS) TABLE_BLOCK_HINTS=0 TABLE_GLOBAL_HINTS=1
	$(MAKE) -B build/test-global-hints-cuda-512 ARCH="$(PRO6000_ARCH)" $(PRO6000_20B_KNOBS) TABLE_BLOCK_HINTS=0 TABLE_GLOBAL_HINTS=1
test-global-hints-geometries: compile-global-hints-geometries
	./build/test-global-hints-cuda-128
	./build/test-global-hints-cuda-256
	./build/test-global-hints-cuda-512
test-global-hints-preset: PACKED_ALU_SQUARE = 1
test-global-hints-preset:
	$(MAKE) -B build/test-global-hints-cuda ARCH="$(PRO6000_ARCH)" $(PRO6000_20B_KNOBS) $(KNOBS)
	./build/test-global-hints-cuda
.PHONY: test-global-hints-preset compile-global-hints-geometries test-global-hints-geometries

# The RTX PRO 4500 build of POWER-BOUND.md: the 20 B/s knob set with one fused
# pass per step, which does the same work per clock as the 20 B/s build and
# 7% more per joule; on a 165 W part that sits on its power limit, per joule is
# the rate.  Predicted 7.2 - 7.4 B/s on a 4500 (receipts: 5.107 / 5.552);
# measured only on an RTX PRO 6000 so far.  RTX4500_EXTRA adds knobs, e.g.
# PACKED_ALU_SQUARE=0 for the second arm POWER-BOUND.md section 4 names.
RTX4500_EXTRA ?=
gpu-rtx-pro4500:
	$(MAKE) gpu-preset KNOBS="TABLE_FUSED=1 TABLE_PIPE_SELECT=0 $(RTX4500_EXTRA)"

# The B200 build the automatic sweep of AUTOSWEEP.md found (sm_100): the 20 B/s
# knob set with the products' 3-bit correction on the carry-less unit as a
# three-limb Karatsuba, the squaring on the unit, the ONB inversion, one fused
# pass per step, and batch 32.  19.40 B/s median on one B200, verified.
gpu-b200-19b:
	$(MAKE) gpu-preset PRO6000_ARCH="-gencode arch=compute_100,code=sm_100" \
	    KNOBS="PACKED_ALU_SQUARE=0 PACKED_ONB_INV=1 PACKED_KARAT3=1 TABLE_FUSED=1 TABLE_PIPE_SELECT=0 BATCH=32 THREADS=512 MINBLOCKS=1"

# The two-chain form of the 20 B/s build (TWO-CHAINS.md): the same arithmetic
# and the same state footprint per SM, as 256 threads x 32 slots in two
# interleaved chains of 16 instead of 512 threads x one chain of 16.
# CHAINS2_THREADS and CHAINS2_BATCH are the geometry knobs of that note.
CHAINS2_THREADS ?= 256
CHAINS2_BATCH ?= 32
gpu-rtx-pro6000-chains2: PACKED_ALU_SQUARE = 1
gpu-rtx-pro6000-chains2:
	$(MAKE) -B ecc2k130 ARCH="$(PRO6000_ARCH)" \
	    BATCH=$(CHAINS2_BATCH) THREADS=$(CHAINS2_THREADS) MINBLOCKS=1 \
	    PACKED_SINGLE_PRODUCT=1 PACKED_CACHE_DENOM=1 PACKED_BY_VALUE=1 PACKED_PERM_SIGMA=3 \
	    PACKED_POLY_CHAIN=1 PACKED_UNROLL_INV=1 PACKED_PAIR_PRODUCTS=1 PACKED_POLY_STATE=1 \
	    PACKED_DIRECT_REDUCE=1 PACKED_GENERATED_PRODUCT=1 PACKED_CLMAD=1 PACKED_STATE_TILE=256 \
	    PACKED_WEIGHTED_PREFIX=2 PACKED_COMPACT_STATE=1 PACKED_SHARED_SIGMA=1 \
	    WALK_TABLE=1 TABLE_PIVOT_BYTES=1 PACKED_PAIR_ILP=1 PACKED_L2_PERSIST=1 PACKED_ALU_SQUARE=$(PACKED_ALU_SQUARE) \
	    TABLE_TAG_DENOM=1 PACKED_CHAINS=2 \
	    PACKED_INLINE_POLY=3 UNROLL_SLOTS=1 PACKED_FROM_REDUCED=1

# Ada preset for the EC2 g6 (L4) and g6e (L40S) instance types, both sm_89.
# It is the RTX PRO 6000 arithmetic/storage preset. CLMAD is on: the
# preblackwell receipt measured 1.811× on L40S and 1.881× on L4 against the
# software product. ADA_CLMAD=0 is the superseded before-arm.
ADA_GPU ?= L40S
ADA_CUDA ?= 13.3.1
ADA_CLMAD ?= 1
ADA_COMPACT_STATE ?= 1
ADA_SHARED_SIGMA ?= 1
ADA_ENV = ECC_GPU=$(ADA_GPU) ECC_CUDA_VERSION=$(ADA_CUDA) \
                 ECC_PACKED_SINGLE_PRODUCT=1 ECC_PACKED_CACHE_DENOM=1 \
                 ECC_PACKED_BY_VALUE=1 ECC_PACKED_PERM_SIGMA=3 \
                 ECC_PACKED_POLY_CHAIN=1 ECC_PACKED_UNROLL_INV=1 \
                 ECC_PACKED_PAIR_PRODUCTS=1 ECC_PACKED_POLY_STATE=1 ECC_PACKED_DIRECT_REDUCE=1 \
                 ECC_PACKED_GENERATED_PRODUCT=1 ECC_PACKED_CLMAD=$(ADA_CLMAD) ECC_PACKED_STATE_TILE=256 \
                 ECC_PACKED_WEIGHTED_PREFIX=2 ECC_PACKED_COMPACT_STATE=$(ADA_COMPACT_STATE) \
                 ECC_PACKED_SHARED_SIGMA=$(ADA_SHARED_SIGMA) \
                 ECC_PACKED_TOP_CLMAD=0

# Workers stay automatic.  The RTX PRO 6000 preset's 385,024 is four waves on a
# 188-SM part and has no claim on a 142-SM (L40S) or 58-SM (L4) one; automatic
# scales with multiProcessorCount and the occupancy the build actually admits
# (autoThreads in include/packedengine.cuh).
bench-ada-modal:
	$(ADA_ENV) $(MODAL) run modal_app.py::bench --packed \
	    --batch 16 --threads 256 --leaf 0 --min-blocks 2 \
	    --steps 1024 --launches 32 --repeats 3

# Instance-type aliases. g6 is L4, g6e is L40S, both sm_89. Workers stay
# automatic. CLMAD defaults on after the preblackwell receipt.
bench-g6-modal:
	$(MAKE) bench-ada-modal ADA_GPU=L4

bench-g6e-modal:
	$(MAKE) bench-ada-modal ADA_GPU=L40S

# Datacenter Blackwell B200 (Modal), sm_100. Same arithmetic/storage preset as
# the RTX PRO 6000; workers stay automatic — 385,024 is a 188-SM occupancy
# and has no claim on a 148-SM part. See B200.md. B200_CLMAD=0 is the
# software-product before-arm.
B200_CLMAD ?= 1
B200_ENV = ECC_GPU=B200 ECC_CUDA_VERSION=13.3.1 \
                 ECC_PACKED_SINGLE_PRODUCT=1 ECC_PACKED_CACHE_DENOM=1 \
                 ECC_PACKED_BY_VALUE=1 ECC_PACKED_PERM_SIGMA=3 \
                 ECC_PACKED_POLY_CHAIN=1 ECC_PACKED_UNROLL_INV=1 \
                 ECC_PACKED_PAIR_PRODUCTS=1 ECC_PACKED_POLY_STATE=1 ECC_PACKED_DIRECT_REDUCE=1 \
                 ECC_PACKED_GENERATED_PRODUCT=1 ECC_PACKED_CLMAD=$(B200_CLMAD) ECC_PACKED_STATE_TILE=256 \
                 ECC_PACKED_WEIGHTED_PREFIX=2 ECC_PACKED_COMPACT_STATE=1 \
                 ECC_PACKED_SHARED_SIGMA=1 \
                 ECC_PACKED_TOP_CLMAD=0

bench-b200-modal:
	$(B200_ENV) $(MODAL) run modal_app.py::bench --packed \
	    --batch 16 --threads 256 --leaf 0 --min-blocks 2 \
	    --steps 1024 --launches 32 --repeats 3

validate-b200-modal:
	$(B200_ENV) $(MODAL) run modal_app.py::validate --packed \
	    --batch 16 --threads 256 --leaf 0 --min-blocks 2

# Per-SM occupancy: several wave multipliers of automatic workers on one
# allocation. See benchmarks/per-sm/WAVES.md. WAVES_GPU selects the part;
# default is the campaign 6000. Does not change the shipping 385,024 preset.
WAVES_GPU ?= RTX-PRO-6000
WAVES_LIST ?= 1,4,6,8
WAVES_ENV = ECC_GPU=$(WAVES_GPU) ECC_CUDA_VERSION=13.3.1 \
                 ECC_PACKED_SINGLE_PRODUCT=1 ECC_PACKED_CACHE_DENOM=1 \
                 ECC_PACKED_BY_VALUE=1 ECC_PACKED_PERM_SIGMA=3 \
                 ECC_PACKED_POLY_CHAIN=1 ECC_PACKED_UNROLL_INV=1 \
                 ECC_PACKED_PAIR_PRODUCTS=1 ECC_PACKED_POLY_STATE=1 ECC_PACKED_DIRECT_REDUCE=1 \
                 ECC_PACKED_GENERATED_PRODUCT=1 ECC_PACKED_CLMAD=1 ECC_PACKED_STATE_TILE=256 \
                 ECC_PACKED_WEIGHTED_PREFIX=2 ECC_PACKED_COMPACT_STATE=1 \
                 ECC_PACKED_SHARED_SIGMA=1 \
                 ECC_PACKED_TOP_CLMAD=0

bench-waves-modal:
	$(WAVES_ENV) $(MODAL) run modal_app.py::waves --packed \
	    --batch 16 --threads 256 --leaf 0 --min-blocks 2 \
	    --steps 1024 --launches 32 --repeats 3 --wave-list $(WAVES_LIST)

# Turing T4 (EC2 g4dn), sm_75. Software product only; see T4-G4DN.md.
T4_ENV = ECC_GPU=T4 ECC_CUDA_VERSION=13.3.1 \
                 ECC_PACKED_SINGLE_PRODUCT=1 ECC_PACKED_CACHE_DENOM=1 \
                 ECC_PACKED_BY_VALUE=1 ECC_PACKED_PERM_SIGMA=3 \
                 ECC_PACKED_POLY_CHAIN=1 ECC_PACKED_UNROLL_INV=1 \
                 ECC_PACKED_PAIR_PRODUCTS=1 ECC_PACKED_POLY_STATE=1 ECC_PACKED_DIRECT_REDUCE=1 \
                 ECC_PACKED_GENERATED_PRODUCT=1 ECC_PACKED_CLMAD=0 ECC_PACKED_STATE_TILE=256 \
                 ECC_PACKED_WEIGHTED_PREFIX=2 ECC_PACKED_COMPACT_STATE=1 \
                 ECC_PACKED_SHARED_SIGMA=1 \
                 ECC_PACKED_TOP_CLMAD=0

bench-g4dn-modal:
	$(T4_ENV) $(MODAL) run modal_app.py::bench --packed \
	    --batch 16 --threads 256 --leaf 0 --min-blocks 2 \
	    --steps 1024 --launches 32 --repeats 3

# One matched packed-walk recipe on any Modal GPU type. Workers stay
# automatic. SURVEY_CLMAD defaults on; T4 must pass 0 (no clmad on sm_75).
# See benchmarks/modal-gpus/SURVEY.md. Do not pass --workers: 385,024 is a
# 188-SM occupancy.
SURVEY_GPU ?= L40S
SURVEY_CLMAD ?= 1
SURVEY_ENV = ECC_GPU=$(SURVEY_GPU) ECC_CUDA_VERSION=13.3.1 \
                 ECC_PACKED_SINGLE_PRODUCT=1 ECC_PACKED_CACHE_DENOM=1 \
                 ECC_PACKED_BY_VALUE=1 ECC_PACKED_PERM_SIGMA=3 \
                 ECC_PACKED_POLY_CHAIN=1 ECC_PACKED_UNROLL_INV=1 \
                 ECC_PACKED_PAIR_PRODUCTS=1 ECC_PACKED_POLY_STATE=1 ECC_PACKED_DIRECT_REDUCE=1 \
                 ECC_PACKED_GENERATED_PRODUCT=1 ECC_PACKED_CLMAD=$(SURVEY_CLMAD) ECC_PACKED_STATE_TILE=256 \
                 ECC_PACKED_WEIGHTED_PREFIX=2 ECC_PACKED_COMPACT_STATE=1 \
                 ECC_PACKED_SHARED_SIGMA=1 \
                 ECC_PACKED_TOP_CLMAD=0

bench-modal-gpu:
	$(SURVEY_ENV) $(MODAL) run modal_app.py::bench --packed \
	    --batch 16 --threads 256 --leaf 0 --min-blocks 2 \
	    --steps 1024 --launches 32 --repeats 3

# Thin native clients for one architecture. Need nvcc. These are the
# binaries a g6 / g6e / g4dn worker should run; the campaign fat default
# is still sm_120. Ada (g6/g6e) ships CLMAD=1; Turing (g4dn) cannot.
ADA_PACKED = BATCH=16 THREADS=256 MINBLOCKS=2 \
	PACKED_SINGLE_PRODUCT=1 PACKED_CACHE_DENOM=1 PACKED_BY_VALUE=1 \
	PACKED_PERM_SIGMA=3 PACKED_POLY_CHAIN=1 PACKED_UNROLL_INV=1 \
	PACKED_PAIR_PRODUCTS=1 PACKED_POLY_STATE=1 PACKED_DIRECT_REDUCE=1 \
	PACKED_GENERATED_PRODUCT=1 PACKED_CLMAD=1 PACKED_STATE_TILE=256 \
	PACKED_WEIGHTED_PREFIX=2 PACKED_COMPACT_STATE=1 PACKED_SHARED_SIGMA=1 \
	PACKED_TOP_CLMAD=0
T4_PACKED = BATCH=16 THREADS=256 MINBLOCKS=2 \
	PACKED_SINGLE_PRODUCT=1 PACKED_CACHE_DENOM=1 PACKED_BY_VALUE=1 \
	PACKED_PERM_SIGMA=3 PACKED_POLY_CHAIN=1 PACKED_UNROLL_INV=1 \
	PACKED_PAIR_PRODUCTS=1 PACKED_POLY_STATE=1 PACKED_DIRECT_REDUCE=1 \
	PACKED_GENERATED_PRODUCT=1 PACKED_CLMAD=0 PACKED_STATE_TILE=256 \
	PACKED_WEIGHTED_PREFIX=2 PACKED_COMPACT_STATE=1 PACKED_SHARED_SIGMA=1 \
	PACKED_TOP_CLMAD=0

gpu-g6 gpu-g6e:
	$(MAKE) gpu ARCH="-gencode arch=compute_89,code=sm_89" $(ADA_PACKED)

gpu-g4dn:
	$(MAKE) gpu ARCH="-gencode arch=compute_75,code=sm_75" $(T4_PACKED)

# On the g6/g6e instance itself, where the part actually is.  Measures both
# arms of the CLMAD question rather than assuming either.
bench-ada:
	bash benchmarks/ada/run.sh

bench-g4dn:
	bash benchmarks/t4/run.sh

# The g7 fleet runs the RTX PRO 4500, for which this tree records no rate at
# all; see RTX-PRO4500.md for the purchasing decision that waits on one. Unlike
# the RTX PRO 6000 targets this does not go through Modal -- it runs on the EC2
# instance itself, where the part actually is.
bench-rtx-pro4500:
	bash benchmarks/rtx-pro4500/run.sh

audit-rtx-pro6000:
	mkdir -p build
	$(RTX_PRO6000_ENV) $(MODAL) run packed_audit.py --batch 16 --min-blocks 2 --block-threads 256 --repeats 3 --workers $(RTX_PRO6000_WORKERS) \
	    --output build/rtx-pro6000-audit.json

test-sigma-fused-star:
	bash benchmarks/sigma-fused/test_star_native.sh

build/sigma-fused-shared-scratch-model: benchmarks/sigma-fused/shared-scratch/model.cpp
	mkdir -p build
	$(CXX) -O2 -std=c++17 -Wall -Wextra -Werror $< -o $@

test-sigma-fused-shared-scratch-model: build/sigma-fused-shared-scratch-model
	./build/sigma-fused-shared-scratch-model --self-test
	./build/sigma-fused-shared-scratch-model > build/sigma-fused-shared-scratch-model.json
	cmp build/sigma-fused-shared-scratch-model.json benchmarks/sigma-fused/shared-scratch/model.json

build/test-sigma-fused-shared-scratch-native: benchmarks/sigma-fused/shared-scratch/test_native.cpp include/packedsigmascratch.h include/packedcompactstate.cuh include/packed131.h
	mkdir -p build
	$(CXX) -O2 -std=c++17 -Wall -Wextra -Werror -Wno-unknown-pragmas $< -o $@

build/sigma-fused-shared-scratch-summarize: benchmarks/sigma-fused/shared-scratch/summarize.cpp
	mkdir -p build
	$(CXX) -O2 -std=c++17 -Wall -Wextra -Werror $< -o $@

build/sigma-fused-shared-scratch-source-audit: benchmarks/sigma-fused/shared-scratch/source_audit.cpp
	mkdir -p build
	$(CXX) -O2 -std=c++17 -Wall -Wextra -Werror $< -o $@

build/sigma-fused-shared-scratch-compile-audit: benchmarks/sigma-fused/shared-scratch/compile_audit.cpp
	mkdir -p build
	$(CXX) -O2 -std=c++17 -Wall -Wextra -Werror $< -o $@

build/sigma-fused-shared-scratch-log-check: benchmarks/sigma-fused/shared-scratch/log_check.cpp
	mkdir -p build
	$(CXX) -O2 -std=c++17 -Wall -Wextra -Werror $< -o $@

test-sigma-fused-shared-scratch-native: build/test-sigma-fused-shared-scratch-native \
                                      build/sigma-fused-shared-scratch-summarize \
                                      build/sigma-fused-shared-scratch-source-audit \
                                      build/sigma-fused-shared-scratch-compile-audit \
                                      build/sigma-fused-shared-scratch-log-check
	./build/test-sigma-fused-shared-scratch-native
	./build/sigma-fused-shared-scratch-summarize --self-test
	./build/sigma-fused-shared-scratch-source-audit --self-test
	./build/sigma-fused-shared-scratch-source-audit .
	./build/sigma-fused-shared-scratch-compile-audit --self-test
	./build/sigma-fused-shared-scratch-log-check --self-test

build/test-sigma-fused-shared-scratch-cuda-0: SIGMA_FUSED_SHARED_SLOTS = 0
build/test-sigma-fused-shared-scratch-cuda-2: SIGMA_FUSED_SHARED_SLOTS = 2
build/test-sigma-fused-shared-scratch-cuda-3: SIGMA_FUSED_SHARED_SLOTS = 3
build/test-sigma-fused-shared-scratch-cuda-4: SIGMA_FUSED_SHARED_SLOTS = 4
build/test-sigma-fused-shared-scratch-cuda-0 build/test-sigma-fused-shared-scratch-cuda-2 build/test-sigma-fused-shared-scratch-cuda-3 build/test-sigma-fused-shared-scratch-cuda-4: src/testsigmasharedscratchcuda.cu $(HDRS) $(GEN)
	mkdir -p build
	$(NVCC) -O3 -std=c++17 $(ARCH) $(DEFS) $(GPUFLAGS) -lineinfo $< -o $@

compile-sigma-fused-shared-scratch-cuda:
	$(MAKE) -B build/test-sigma-fused-shared-scratch-cuda-0 ARCH="$(PRO6000_ARCH)" $(PRO6000_SIGMA_FUSED_KNOBS)
	$(MAKE) -B build/test-sigma-fused-shared-scratch-cuda-2 ARCH="$(PRO6000_ARCH)" $(PRO6000_SIGMA_FUSED_KNOBS)
	$(MAKE) -B build/test-sigma-fused-shared-scratch-cuda-3 ARCH="$(PRO6000_ARCH)" $(PRO6000_SIGMA_FUSED_KNOBS)
	$(MAKE) -B build/test-sigma-fused-shared-scratch-cuda-4 ARCH="$(PRO6000_ARCH)" $(PRO6000_SIGMA_FUSED_KNOBS)

test-sigma-fused-shared-scratch-cuda: compile-sigma-fused-shared-scratch-cuda
	./build/test-sigma-fused-shared-scratch-cuda-0
	./build/test-sigma-fused-shared-scratch-cuda-2
	./build/test-sigma-fused-shared-scratch-cuda-3
	./build/test-sigma-fused-shared-scratch-cuda-4

clean:
	rm -f ecc2k130 ecc2k130-cpu build/*.ptx build/*.cubin build/*.o build/cudashim.h
	rm -f build/test-schedule-base build/test-schedule-stream
	rm -f build/test-packed build/test-packed-base build/test-packed-single
	rm -f build/test-packed-network build/test-packed-network-direct build/test-packed-network-generated build/test-packed-network-add
	rm -f build/test-packed-clmad-host build/test-packed-topclmad-host build/test-packed-pair-ilp-host build/test-packed-clmad-square-host
	rm -f build/test-packed-top-hoist-host build/test-packed-onb-inv-host
	rm -f build/test-packed-from-reduced-host
	rm -f build/test-packed-pair-clmul-host build/test-packed-alu-sqr-host
	rm -f build/test-packed-cuda build/test-packed-storage-cuda
	rm -f build/test-shared-sigma-cuda
	rm -f build/test-timing build/test-logic build/test-table-walk-host-nibble build/test-table-walk-host-bytes
	rm -f build/table-v3-replay build/test-table-v3-replay
	rm -f build/walk-constant-host-h8 build/walk-constant-host-h16 build/test-cycle-rule
	rm -f build/test-cycle-profile build/test-cycle-profile-fast2
	rm -f build/sigma-fused-shared-scratch-model build/sigma-fused-shared-scratch-model.json
	rm -f build/test-sigma-fused-shared-scratch-native build/test-sigma-fused-shared-scratch-cuda-*
	rm -f build/sigma-fused-shared-scratch-summarize build/sigma-fused-shared-scratch-source-audit build/sigma-fused-shared-scratch-compile-audit
	rm -f build/sigma-fused-shared-scratch-log-check

	rm -f build/test-packed-block-inverse-cuda build/test-last-slot-cache-cuda build/test-tail-product-cuda build/test-shared-x-cache-cuda

.PHONY: path-cost all generate check-cli check-cycles perfmodel test-decomp test-schedule test-packed test-packed-network test-generated-product test-clmad test-clmad-square test-karat3 test-packed-cuda test-packed-storage-cuda test-shared-sigma-cuda test-timing test-logic test-table-walk-host test-cycle-rule walk-constant test-table-walk-cuda test-phase-cost test-production certify-local rehearse autolab cpu gpu test break-small bench bench95 ptx check-cuda modal bench-rtx-pro6000 bench-rtx-pro4500 bench-ada bench-ada-modal bench-g6-modal bench-g6e-modal bench-g4dn-modal bench-g4dn bench-b200-modal validate-b200-modal bench-waves-modal bench-modal-gpu bench-cheaper-selection gpu-g6 gpu-g6e gpu-g4dn audit-rtx-pro6000 clean
.PHONY: path-cost all generate check-cli check-cycles perfmodel test-decomp test-schedule test-packed test-packed-network test-generated-product test-clmad test-clmad-square test-karat3 test-packed-cuda test-packed-storage-cuda test-shared-sigma-cuda test-timing test-logic test-table-walk-host table-v3-replay test-table-v3-replay test-cycle-rule walk-constant test-table-walk-cuda test-phase-cost test-production certify-local rehearse autolab cpu gpu test break-small bench bench95 ptx check-cuda modal bench-rtx-pro6000 bench-rtx-pro4500 bench-ada bench-ada-modal bench-g6-modal bench-g6e-modal bench-g4dn-modal bench-g4dn bench-b200-modal validate-b200-modal bench-waves-modal bench-modal-gpu bench-cheaper-selection gpu-g6 gpu-g6e gpu-g4dn audit-rtx-pro6000 clean test-cycle-profile test-sigma-fused-star test-sigma-fused-shared-scratch-model test-sigma-fused-shared-scratch-native compile-sigma-fused-shared-scratch-cuda test-sigma-fused-shared-scratch-cuda
.PHONY: path-cost all generate check-cli check-cycles perfmodel test-decomp test-schedule test-packed test-packed-network test-sigma-square-table-native test-generated-product test-clmad test-clmad-square test-karat3 test-packed-cuda test-packed-storage-cuda test-shared-sigma-cuda test-timing test-logic test-table-walk-host table-v3-replay test-table-v3-replay test-cycle-rule walk-constant test-table-walk-cuda test-phase-cost test-production certify-local rehearse autolab cpu gpu test break-small bench bench95 ptx check-cuda modal bench-rtx-pro6000 bench-rtx-pro4500 bench-ada bench-ada-modal bench-g6-modal bench-g6e-modal bench-g4dn-modal bench-g4dn bench-b200-modal validate-b200-modal bench-waves-modal bench-modal-gpu bench-cheaper-selection gpu-g6 gpu-g6e gpu-g4dn audit-rtx-pro6000 clean test-cycle-profile test-sigma-fused-star test-sigma-fused-shared-scratch-model
.PHONY: path-cost all generate check-cli check-cycles test-status perfmodel test-decomp test-schedule test-packed test-packed-network test-generated-product test-clmad test-clmad-square test-karat3 test-packed-cuda test-packed-storage-cuda test-shared-sigma-cuda test-timing test-logic test-table-walk-host table-v3-replay test-table-v3-replay test-cycle-rule walk-constant test-table-walk-cuda test-phase-cost test-production certify-local rehearse autolab cpu gpu test break-small bench bench95 ptx check-cuda modal bench-rtx-pro6000 bench-rtx-pro4500 bench-ada bench-ada-modal bench-g6-modal bench-g6e-modal bench-g4dn-modal bench-g4dn bench-b200-modal validate-b200-modal bench-waves-modal bench-modal-gpu bench-cheaper-selection gpu-g6 gpu-g6e gpu-g4dn audit-rtx-pro6000 clean test-cycle-profile test-sigma-fused-star test-sigma-fused-shared-scratch-model


# Local packed preset. Prefer the measured optional assembler-only prefix,
# then pinned CUDA 13.3, then NVCC. Native CLMAD needs CUDA 13.3 or newer.
# Separate output and forced rebuild prevent stale binaries when knobs change.
LOCAL_GPU_ARCH ?= -gencode arch=compute_120,code=sm_120
LOCAL_PACKED_NVCC ?= $(if $(wildcard build/assembler134-toolchain/bin/nvcc),build/assembler134-toolchain/bin/nvcc,$(if $(wildcard build/cuda133/nvidia/cu13/bin/nvcc),build/cuda133/nvidia/cu13/bin/nvcc,$(NVCC)))
LOCAL_PACKED_CLMAD ?= $(shell $(LOCAL_PACKED_NVCC) --version 2>/dev/null | awk '/release / { split($$5,v,"."); print (v[1]+0>13 || (v[1]+0==13 && v[2]+0>=3)); found=1 } END { if (!found) print 0 }')
LOCAL_PACKED_INLINE ?= $(if $(filter 1,$(LOCAL_PACKED_CLMAD)),3,0)
# Exact seven-stage basis conversion, confirmed on the native G7 preset.
LOCAL_PACKED_FAST_CONVERT ?= $(if $(filter 1,$(LOCAL_PACKED_CLMAD)),1,0)
# Native normal products avoid basis conversions inside inversion. The
# software fallback keeps its established single-product implementation.
LOCAL_PACKED_SINGLE_PRODUCT ?= $(if $(filter 1,$(LOCAL_PACKED_CLMAD)),0,1)
# Share inversion across eight full warps in the validated native preset.
LOCAL_PACKED_BLOCK_INVERSE ?= $(if $(filter 1,$(LOCAL_PACKED_CLMAD)),1,0)
LOCAL_PACKED_BATCH ?= 16
# Exact 131-port walk routing is measured on the native RTX PRO 4500 preset.
LOCAL_PACKED_PARTIAL_SIGMA ?= $(if $(filter 1,$(LOCAL_PACKED_CLMAD)),1,0)
# Measured Frobenius instruction reductions in the native G7 preset.
LOCAL_PACKED_SIGMA_ORDER ?= $(if $(and $(filter 1,$(LOCAL_PACKED_CLMAD)),$(filter 1 3,$(LOCAL_PACKED_PARTIAL_SIGMA))),1,0)
LOCAL_PACKED_BYTE_SIGMA ?= $(if $(and $(filter 1,$(LOCAL_PACKED_CLMAD)),$(filter 1 3,$(LOCAL_PACKED_PARTIAL_SIGMA))),1,0)
# The measured RTX PRO 4500 native preset uses two physical workers per batch.
LOCAL_PACKED_BATCH_SPLIT ?= $(if $(and $(filter 1,$(LOCAL_PACKED_CLMAD)),$(filter 1,$(LOCAL_PACKED_BLOCK_INVERSE)),$(filter 16,$(LOCAL_PACKED_BATCH))),2,1)
LOCAL_PACKED_MINBLOCKS ?= $(if $(filter 2,$(LOCAL_PACKED_BATCH_SPLIT)),3,2)
LOCAL_PACKED_LAST_SLOT_CACHE ?= $(if $(and $(filter 1,$(LOCAL_PACKED_CLMAD)),$(filter 1,$(LOCAL_PACKED_BLOCK_INVERSE)),$(filter 16,$(LOCAL_PACKED_BATCH)),$(filter 2,$(LOCAL_PACKED_BATCH_SPLIT))),1,0)
# Regroup the high three product bits using measured native funnel shifts.
LOCAL_PACKED_TAIL_LAYOUT ?= $(if $(filter 1,$(LOCAL_PACKED_CLMAD)),1,0)
LOCAL_PACKED_SHARED_X_SLOTS ?= $(if $(and $(filter 1,$(LOCAL_PACKED_CLMAD)),$(filter 1,$(LOCAL_PACKED_BLOCK_INVERSE)),$(filter 16,$(LOCAL_PACKED_BATCH)),$(filter 2,$(LOCAL_PACKED_BATCH_SPLIT))),4,0)
LOCAL_PACKED_FLAGS = NVCC="$(LOCAL_PACKED_NVCC)" ARCH="$(LOCAL_GPU_ARCH)" \
    BATCH=$(LOCAL_PACKED_BATCH) THREADS=256 MINBLOCKS=$(LOCAL_PACKED_MINBLOCKS) \
    PACKED_SINGLE_PRODUCT=$(LOCAL_PACKED_SINGLE_PRODUCT) PACKED_CACHE_DENOM=1 PACKED_BY_VALUE=1 \
    PACKED_PERM_SIGMA=3 PACKED_POLY_CHAIN=1 PACKED_UNROLL_INV=1 \
    PACKED_PAIR_PRODUCTS=1 PACKED_POLY_STATE=1 PACKED_DIRECT_REDUCE=1 \
    PACKED_GENERATED_PRODUCT=1 PACKED_CLMAD=$(LOCAL_PACKED_CLMAD) PACKED_INLINE=$(LOCAL_PACKED_INLINE) PACKED_WEIGHTED_PREFIX=2 \
    PACKED_COMPACT_STATE=1 PACKED_SHARED_SIGMA=1 PACKED_STATE_TILE=256 \
    PACKED_BLOCK_INVERSE=$(LOCAL_PACKED_BLOCK_INVERSE) PACKED_BATCH_SPLIT=$(LOCAL_PACKED_BATCH_SPLIT) PACKED_PARTIAL_SIGMA=$(LOCAL_PACKED_PARTIAL_SIGMA) \
    PACKED_SIGMA_ORDER=$(LOCAL_PACKED_SIGMA_ORDER) PACKED_BYTE_SIGMA=$(LOCAL_PACKED_BYTE_SIGMA) \
    PACKED_FAST_CONVERT=$(LOCAL_PACKED_FAST_CONVERT) \
    PACKED_LAST_SLOT_CACHE=$(LOCAL_PACKED_LAST_SLOT_CACHE) PACKED_TAIL_LAYOUT=$(LOCAL_PACKED_TAIL_LAYOUT) \
    PACKED_SHARED_X_SLOTS=$(LOCAL_PACKED_SHARED_X_SLOTS) \
    PACKED_XONLY_23=1 PACKED_XONLY_DOUBLE_ONLY=1 PACKED_XONLY_BRIDGE3=1 \
    PACKED_XONLY_BRIDGE1_COMMON=1 PACKED_XONLY_BRIDGE_MOD72=1 \
    PACKED_XONLY_SKIP_EMPTY_BRIDGE=$(PACKED_XONLY_SKIP_EMPTY_BRIDGE)
LOCAL_PACKED_WORKERS ?= 524288
LOCAL_PACKED_STEPS ?= 1024
LOCAL_PACKED_LAUNCHES ?= 16

# Explicit opt-in install; the regular build never downloads a compiler.
setup-local-cuda133:
	python3 -m pip install --no-deps --upgrade --target build/cuda133 \
	    nvidia-cuda-nvcc==13.3.73 nvidia-cuda-runtime==13.3.29 \
	    nvidia-nvvm==13.3.73 nvidia-cuda-crt==13.3.73

# Explicit opt-in: retain the 13.3 front end and replace only ptxas.
setup-local-assembler134: setup-local-cuda133
	python3 setup_cuda_assembler.py

.PHONY: setup-local-cuda133 setup-local-assembler134 gpu-local-packed bench-local-packed

gpu-local-packed:
	$(MAKE) -B build/ecc2k130-local-packed $(LOCAL_PACKED_FLAGS)

build/ecc2k130-local-packed: src/main.cu $(HDRS) $(GEN)
	mkdir -p build
	$(NVCC) -O3 -std=c++17 $(ARCH) $(DEFS) $(GPUFLAGS) -lineinfo \
	    -Xcompiler -O3 -Xcompiler -fopenmp src/main.cu -o $@ -lgomp

bench-local-packed: gpu-local-packed
	python3 benchmark_hardware.py
	./build/ecc2k130-local-packed --packed --bench --curve 131 \
	    --threads $(LOCAL_PACKED_WORKERS) --steps $(LOCAL_PACKED_STEPS) \
	    --launches $(LOCAL_PACKED_LAUNCHES) --verify 0

test-last-slot-cache-cuda:
	mkdir -p build
	$(NVCC) -O3 -std=c++17 $(ARCH) $(DEFS) $(GPUFLAGS) -lineinfo \
	    -Xcompiler -O3 -Xcompiler -fopenmp src/testlastslotcuda.cu -o build/test-last-slot-cache-cuda -lgomp
	./build/test-last-slot-cache-cuda

test-tail-product-cuda:
	mkdir -p build
	$(NVCC) -O3 -std=c++17 $(ARCH) $(DEFS) $(GPUFLAGS) -lineinfo \
	    -Xcompiler -O3 -Xcompiler -fopenmp src/testtailproductcuda.cu -o build/test-tail-product-cuda -lgomp
	./build/test-tail-product-cuda

test-shared-x-cache-cuda:
	mkdir -p build
	$(NVCC) -O3 -std=c++17 $(ARCH) $(DEFS) $(GPUFLAGS) -lineinfo \
	    -Xcompiler -O3 -Xcompiler -fopenmp src/testsharedxcuda.cu -o build/test-shared-x-cache-cuda -lgomp
	./build/test-shared-x-cache-cuda

test-local-packed:
	$(MAKE) test-packed-cuda test-packed-storage-cuda test-shared-sigma-cuda $(if $(filter 1,$(LOCAL_PACKED_BLOCK_INVERSE)),test-packed-block-inverse-cuda) $(if $(filter 1 2,$(LOCAL_PACKED_LAST_SLOT_CACHE)),test-last-slot-cache-cuda) $(if $(filter 1,$(LOCAL_PACKED_CLMAD)),test-tail-product-cuda) $(if $(filter 2 4,$(LOCAL_PACKED_SHARED_X_SLOTS)),test-shared-x-cache-cuda) $(LOCAL_PACKED_FLAGS)

.PHONY: setup-local-cuda133 gpu-local-packed bench-local-packed test-local-packed test-packed-block-inverse-cuda test-last-slot-cache-cuda test-tail-product-cuda

.PHONY: test-shared-x-cache-cuda

# Cold cycle escape, entry-phase invariance, F131 regression and packed parity.
test-cycle-escape:
	mkdir -p build
	$(CXX) -O2 -std=c++17 -Wno-unknown-pragmas src/testcycleescape.cpp -o build/test-cycle-escape
	./build/test-cycle-escape
	$(CXX) -O2 -std=c++17 -Wno-unknown-pragmas -DECC_TABLE_PIVOT_BYTES=1 -DECC_TABLE_ADDEND_GLOBAL=1 src/testcycleescape.cpp -o build/test-cycle-escape-hybrid
	./build/test-cycle-escape-hybrid
	$(CXX) -O2 -std=c++17 -Wno-unknown-pragmas -DECC_CYCLE_FAST2=1 src/testcycleescape.cpp -o build/test-cycle-escape-fast2
	./build/test-cycle-escape-fast2
	$(CXX) -O2 -std=c++17 -Wno-unknown-pragmas -DECC_CYCLE_FAST2=1 -DECC_TABLE_PIVOT_BYTES=1 -DECC_TABLE_ADDEND_GLOBAL=1 src/testcycleescape.cpp -o build/test-cycle-escape-fast2-hybrid
	./build/test-cycle-escape-fast2-hybrid
.PHONY: test-cycle-escape

# Diagnostic counter reconciliation, both with the fast2 shortcut absent and
# with the exact branch used by the B16 GPU profile.
test-cycle-profile:
	mkdir -p build
	$(CXX) -O2 -std=c++17 -Wno-unknown-pragmas -DECC_CYCLE_PROFILE=1 -DECC_CYCLE_FAST2=0 src/testcycleprofile.cpp -o build/test-cycle-profile
	./build/test-cycle-profile
	$(CXX) -O2 -std=c++17 -Wno-unknown-pragmas -DECC_CYCLE_PROFILE=1 -DECC_CYCLE_FAST2=1 src/testcycleprofile.cpp -o build/test-cycle-profile-fast2
	./build/test-cycle-profile-fast2
