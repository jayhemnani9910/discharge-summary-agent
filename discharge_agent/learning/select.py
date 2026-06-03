"""Best-of-N selection with the safety gate in front of it (Part 2 #3 and #5).

The gate runs before the reward model: any candidate that fails ``safety_retained`` against the
agent's own draft is dropped, so a flag-stripping or fact-inventing rewrite can never be chosen
no matter how low its predicted edit burden. Among the survivors we take the lowest predicted
burden, breaking ties toward the earliest candidate. Candidate 0 is always the agent's draft and
always passes the gate, so selection can never do worse than the baseline, and never unsafe.
"""

from __future__ import annotations

from dataclasses import dataclass

from .metrics import safety_retained


@dataclass
class Selection:
    text: str
    index: int          # which candidate was chosen (0 == the agent's own draft)
    score: float        # predicted edit burden of the chosen candidate
    n_candidates: int
    n_safe: int         # how many candidates passed the safety gate
    n_blocked: int      # how many were disqualified by the gate


def best_of_n(candidates: list[str], reward_model, reference_text: str | None = None) -> Selection:
    if not candidates:
        return Selection("", -1, 0.0, 0, 0, 0)
    reference = reference_text if reference_text is not None else candidates[0]

    safe = [(i, c) for i, c in enumerate(candidates) if safety_retained(reference, c)]
    blocked = len(candidates) - len(safe)
    if not safe:
        # Defensive: the agent's own draft should always pass; if nothing did, keep it unchanged.
        return Selection(reference, 0, reward_model.predict(reference), len(candidates), 0, blocked)

    best_i, best_c, best_s = safe[0][0], safe[0][1], reward_model.predict(safe[0][1])
    for i, c in safe[1:]:
        s = reward_model.predict(c)
        if s < best_s:
            best_i, best_c, best_s = i, c, s
    return Selection(best_c, best_i, best_s, len(candidates), len(safe), blocked)
