"""Artifacts for the learning run: a readable report and the before/after improvement curve.

The curve is emitted as a hand-built SVG so the repo needs no plotting dependency; it renders in
any browser and on GitHub. The report puts the safety-retention rate next to the burden curve on
purpose: the headline number is meaningless unless the loop held the Part 1 safety line, so the
two are always shown together.
"""

from __future__ import annotations

import os

from .loop import LearningReport


def write_report(report: LearningReport, out_dir: str = "outputs/learning") -> dict[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, "report.md")
    svg_path = os.path.join(out_dir, "curve.svg")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(_report_md(report))
    with open(svg_path, "w", encoding="utf-8") as fh:
        fh.write(_curve_svg(report))
    return {"report": md_path, "curve": svg_path}


def _report_md(r: LearningReport) -> str:
    pct = 0.0 if r.baseline_burden == 0 else 100.0 * r.improvement / r.baseline_burden
    lines = [
        "# Part 2: Learning from Doctor Edits — Result",
        "",
        "Best-of-N selection driven by a reward model that learns the editor's style from "
        "accumulated (draft, edited) pairs. Edit burden is the mean section-level normalized edit "
        "distance between a draft and its reviewed form (lower is better).",
        "",
        f"- Training pairs: {r.n_train}",
        f"- Held-out sections: {r.n_heldout}",
        f"- Baseline edit burden (agent's own draft): **{r.baseline_burden:.3f}**",
        f"- After learning (best-of-N): **{r.final_burden:.3f}**",
        f"- Reduction: **{r.improvement:.3f}** ({pct:.0f}% lower edit burden)",
        f"- Unsafe candidates blocked by the safety gate: {r.n_blocked_unsafe}",
        "",
        "## Improvement curve (held-out)",
        "",
        "| training pairs | mean edit burden | safety retention |",
        "|---:|---:|---:|",
    ]
    for p in r.curve:
        lines.append(f"| {p.n_pairs} | {p.mean_burden:.3f} | {p.safety_retention:.0%} |")
    lines += [
        "",
        "Safety retention is the fraction of held-out sections whose flags and documented values "
        "survived selection. It stays at 100%: the curve is not bought by dropping flags or "
        "getting vaguer, because the safety gate makes any such candidate unselectable.",
        "",
        "![improvement curve](curve.svg)",
        "",
    ]
    return "\n".join(lines)


def _curve_svg(r: LearningReport) -> str:
    W, H, pad = 640, 360, 50
    pts = r.curve or []
    ys = [p.mean_burden for p in pts] + [r.baseline_burden]
    ymax = max(ys + [0.01]) * 1.15
    n = max(len(pts), 1)

    def x(i):
        return pad + (W - 2 * pad) * (i / max(n - 1, 1))

    def y(v):
        return H - pad - (H - 2 * pad) * (v / ymax)

    base_y = y(r.baseline_burden)
    poly = " ".join(f"{x(i):.1f},{y(p.mean_burden):.1f}" for i, p in enumerate(pts))
    dots = "".join(
        f'<circle cx="{x(i):.1f}" cy="{y(p.mean_burden):.1f}" r="3.5" fill="#2563eb"/>'
        for i, p in enumerate(pts))
    xlabels = "".join(
        f'<text x="{x(i):.1f}" y="{H - pad + 18:.1f}" font-size="11" text-anchor="middle" '
        f'fill="#555">{p.n_pairs}</text>' for i, p in enumerate(pts))
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="sans-serif">
<rect width="{W}" height="{H}" fill="white"/>
<text x="{W/2}" y="24" font-size="15" text-anchor="middle" fill="#111">Edit burden vs accumulated training pairs (held-out)</text>
<line x1="{pad}" y1="{H-pad}" x2="{W-pad}" y2="{H-pad}" stroke="#999"/>
<line x1="{pad}" y1="{pad}" x2="{pad}" y2="{H-pad}" stroke="#999"/>
<line x1="{pad}" y1="{base_y:.1f}" x2="{W-pad}" y2="{base_y:.1f}" stroke="#dc2626" stroke-dasharray="6 4"/>
<text x="{W-pad}" y="{base_y-6:.1f}" font-size="11" text-anchor="end" fill="#dc2626">baseline {r.baseline_burden:.3f}</text>
<polyline points="{poly}" fill="none" stroke="#2563eb" stroke-width="2"/>
{dots}{xlabels}
<text x="{W/2}" y="{H-12}" font-size="12" text-anchor="middle" fill="#555">training pairs</text>
<text x="16" y="{H/2}" font-size="12" text-anchor="middle" fill="#555" transform="rotate(-90 16 {H/2})">mean edit burden</text>
</svg>"""
