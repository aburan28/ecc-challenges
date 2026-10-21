"""Render the frozen positive-DP GPU parity control as SVG and PDF."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tarfile

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from verify_positive_dp import NINE, verify


def render(receipt: Path, archive_path: Path, report: Path) -> None:
    verify(receipt, archive_path)
    report.mkdir(parents=True, exist_ok=False)
    counts = {}
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            if member.name.endswith("/mixed-dp1-cpu/dp.bin"):
                curve = member.name.split("/")[1]
                assert member.size % 216 == 0
                counts[curve] = member.size // 216
    assert len(counts) == 24
    names = sorted(counts)
    fig, axis = plt.subplots(figsize=(11, 4.8), layout="constrained")
    axis.bar(range(24), [counts[name] for name in names],
             color=["#17638a" if name in NINE else "#adb9bf" for name in names])
    axis.set_xticks(range(24), names, rotation=60, ha="right", fontsize=8)
    axis.set_ylabel("Expected and GPU-matched DP1 records")
    axis.set_title("Optimized CUDA parity: 24 curves, 168 controls; blue = requested nine")
    axis.grid(axis="y", alpha=0.25)
    fig.savefig(report / "dp1-counts.svg")
    plt.close(fig)
    typ = '''#set page(width: 11in, height: 8.5in, margin: 0.65in)
#set text(size: 10pt)
= Optimized CUDA positive-DP control

Observation record. GPU receipt: `positive-dp-cuda-receipt.json`.
Frozen controls: `cpu-controls.tar.gz`, SHA-256
`ce502b916d286a7204430e598f4117ea1c17e8dfb4f6aa991a4edf1168ef4629`.
The checker compared GPU output and DP bytes against the frozen CPU cases;
the independent replay checks receipt hashes and requires positive DP1 output.

#image("dp1-counts.svg", width: 100%)

All 168 GPU checks completed across 24 curves. Each curve emitted at least one
DP1 record in its positive control. The nine requested binary curves contributed
137 expected records, with exact GPU equality checked by the launcher. The
three DP40 timing panels emitted zero records, so this is their separate
positive-DP correctness control. No long-run DP density or campaign throughput
is inferred from these finite cases.
'''
    (report / "report.typ").write_text(typ)
    subprocess.run(["typst", "compile", "report.typ", "report.pdf"],
                   cwd=report, check=True, timeout=60)


if __name__ == "__main__":
    render(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
