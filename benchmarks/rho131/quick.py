import sys, time, os
sys.path.insert(0, "src"); sys.path.insert(0, "worker")
from ecc_challenges.multicurve.codec import Namespace
from generic import runtime
engine, backend, campaign, lanes, steps = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
ns = Namespace(campaign)
st = [runtime.NativeState(ns.start("18c3053b-96df-4879-a49d-18a52b8fdf8b", i)) for i in range(lanes)]
t = time.perf_counter(); r = runtime.run_round(engine, backend, ns, st, steps, timeout=3000); dt = time.perf_counter() - t
print(campaign, backend, lanes, steps, f"{lanes*steps/dt:.0f} steps/s", f"{dt:.2f}s")
