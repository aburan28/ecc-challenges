#!/usr/bin/env python3
"""Render the nine requested curves from the immutable final-image receipt."""

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
receipt = json.loads((ROOT / "l4-image-check.json").read_text())
campaigns = (
    "ecc2k-130", "ecc2-131", "ecc2k-163", "ecc2-163", "ecc2-191",
    "ecc2k-238", "ecc2-238", "ecc2k-358", "ecc2-353",
)
rows = []
for campaign in campaigns:
    cases = [r for r in receipt["rows"] if r["campaign"] == campaign]
    if len(cases) != 7 or any(not r["ok"] for r in cases):
        raise ValueError(f"incomplete or failed controls for {campaign}")
    dp1 = next(r for r in cases if r["case"] == "mixed-dp1-cpu")
    if dp1["dp_bytes"] <= 0:
        raise ValueError(f"missing positive DP output for {campaign}")
    rows.append((campaign.upper(), dp1["dp_bytes"]))

width, height = 880, 510
left, right, top, step = 170, 75, 95, 38
scale = (width - left - right) / 5000
out = [
    f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
    '<rect width="100%" height="100%" fill="#fff"/>',
    '<text x="24" y="35" font-family="sans-serif" font-size="20" font-weight="bold" fill="#16213b">Published CUDA image: nine binary curves</text>',
    '<text x="24" y="58" font-family="sans-serif" font-size="13" fill="#53627a">All seven controls passed per curve; bars show bytes from the positive DP1 case</text>',
]
for tick in range(0, 5001, 1000):
    x = left + tick * scale
    out.append(f'<line x1="{x:.1f}" y1="77" x2="{x:.1f}" y2="435" stroke="#e5eaf2"/>')
    out.append(f'<text x="{x:.1f}" y="458" text-anchor="middle" font-family="sans-serif" font-size="12" fill="#53627a">{tick}</text>')
for i, (name, count) in enumerate(rows):
    y = top + step * i
    out.append(f'<text x="20" y="{y+17}" font-family="sans-serif" font-size="13" fill="#16213b">{html.escape(name)}</text>')
    out.append(f'<rect x="{left}" y="{y}" width="{count*scale:.1f}" height="22" rx="3" fill="#1679c5"/>')
    out.append(f'<text x="{left+count*scale+9:.1f}" y="{y+17}" font-family="sans-serif" font-size="12" fill="#16213b">{count} B</text>')
    out.append(f'<text x="815" y="{y+17}" text-anchor="end" font-family="sans-serif" font-size="12" fill="#177245">7/7</text>')
out.extend([
    '<text x="488" y="492" text-anchor="middle" font-family="sans-serif" font-size="12" fill="#53627a">DP1 output bytes, exact control workload</text>',
    '</svg>',
])
(ROOT / "l4-nine-curves.svg").write_text("\n".join(out) + "\n")
print({"curves": len(rows), "passed_controls": 7 * len(rows), "all_positive_dp1": True})
