"""The learning loop: train the reward model on accumulating edits, measure on held-out (Part 2).

For each held-out section we generate candidates once (cached). Then, round by round, we feed the
reward model one more training pair and re-evaluate best-of-N selection on the held-out set. The
metric is the mean *actual* edit burden of the selected drafts (computed via the edit source,
which selection never sees), plotted against the number of training pairs. The baseline is the
agent's own draft (candidate 0). Safety retention is measured every round and must stay at 1.0;
that is the evidence the curve does not improve by dropping flags or getting vaguer.
"""

from __future__ import annotations

from dataclasses import dataclass

from .candidates import candidates_for
from .metrics import edit_burden, safety_retained
from .reward import RewardModel
from .select import best_of_n


@dataclass
class CurvePoint:
    n_pairs: int
    mean_burden: float
    safety_retention: float


@dataclass
class LearningReport:
    baseline_burden: float
    final_burden: float
    curve: list[CurvePoint]
    n_train: int
    n_heldout: int
    n_blocked_unsafe: int

    @property
    def improvement(self) -> float:
        return self.baseline_burden - self.final_burden


def run_learning(provider, edit_source, train, heldout, config, *, n_candidates: int = 4,
                 cache_dir: str = "outputs/learning", regenerate: bool = False,
                 tracer=None) -> LearningReport:
    # Candidates for each held-out section, generated once and reused across rounds.
    held_candidates = [
        (it, candidates_for(provider, it.section_key, it.draft_text, n_candidates, config,
                            cache_dir=cache_dir, regenerate=regenerate, tracer=tracer))
        for it in heldout
    ]
    baseline = _mean(
        edit_burden(it.draft_text, edit_source.edit(it.section_key, it.draft_text))
        for it in heldout) if heldout else 0.0

    model = RewardModel(provider, config, tracer)
    curve: list[CurvePoint] = []
    blocked = 0
    for k in range(1, len(train) + 1):
        pairs = [(it.draft_text, edit_source.edit(it.section_key, it.draft_text)) for it in train[:k]]
        model.fit(pairs)
        burdens, safe_ok = [], 0
        for it, cands in held_candidates:
            sel = best_of_n(cands, model, reference_text=it.draft_text)
            blocked += sel.n_blocked
            edited = edit_source.edit(it.section_key, sel.text)
            burdens.append(edit_burden(sel.text, edited))
            safe_ok += 1 if safety_retained(it.draft_text, sel.text) else 0
        retention = safe_ok / len(held_candidates) if held_candidates else 1.0
        curve.append(CurvePoint(k, _mean(burdens), retention))
        if tracer:
            tracer.emit("learn", message=f"round {k}: held-out burden {curve[-1].mean_burden:.3f}, "
                        f"safety {retention:.2f}")

    final = curve[-1].mean_burden if curve else baseline
    return LearningReport(baseline, final, curve, len(train), len(heldout), blocked)


def _mean(xs) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0
