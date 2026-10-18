# Native platform check, 2026-10-17

The attached receipt checks the merged platform implementation at commit
`2b08525e1010181165805af2a10b968ab0fa8e4d` on an Apple M4 Pro (arm64).
The native engine was built from that commit with `cmake -S worker -B build`
and `cmake --build build --parallel`. The resulting engine SHA-256 was
`99961f7c82ecfec0953a5571e9b8e3fb5998c43cd45272f847d0dfedc78be2c5`.
Its probe listed the Apple M4 Pro Metal device and both CPU and Metal backends.

The independent reference runner was invoked with that same engine as both
`--cpu` and `--metal`:

```sh
python3.12 scripts/check_native_curves.py \
  --cpu build/generic-engine \
  --metal build/generic-engine \
  --out check-output
```

It completed 72 cases across all 24 published campaigns, including exact
state and distinguished-point comparisons with the Python reference and
malformed-input rejection controls. The JSON receipt is
`macos-m4pro-merged-main-receipt.json` (SHA-256
`7031a5b4b9ab51cbd8863d8527a9fef785029538161fc5e47bbbc71e08f45804`).
The macOS Intel and Windows builds and worker tests are covered by the
repository's Worker platforms workflow; this local receipt is specific to
Apple silicon Metal execution.
