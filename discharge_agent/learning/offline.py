"""A deterministic, no-key provider for the learning loop, so `learn` runs in CI and as a
reproducible demo without an API.

It plays both LLM roles the loop needs. As the candidate generator it returns one house-styled
variant per section (the reviewer's transform). As the reward judge it returns a predicted edit
burden whose calibration improves with the number of accumulated (draft, edited) examples in the
prompt: with few examples its estimate is dominated by a deterministic per-candidate hash (so
selection is near-random and stays close to the baseline), and as examples accumulate it
converges on the true burden (so selection locks onto the house-styled candidate). That makes the
offline before/after curve a real downward trend, reproducibly, with no network.

This is a stand-in. The `--live` path uses a real LLM judge that learns the editor's style purely
from the in-context pairs, with no oracle access to the reviewer.
"""

from __future__ import annotations

import hashlib
import json

from ..llm.mock import MockProvider
from .edit_source import SimulatedReviewer
from .metrics import edit_burden


def _pseudo(text: str) -> float:
    return (int(hashlib.sha1(text.encode("utf-8")).hexdigest(), 16) % 1000) / 1000.0


def offline_provider(config):
    reviewer = SimulatedReviewer()

    def completer(system: str, prompt: str, json_mode: bool) -> str:
        if "DRAFT TEXT:" in prompt:
            draft = prompt.split("DRAFT TEXT:", 1)[1].strip()
            styled = reviewer.edit("x", draft)
            return json.dumps({"variants": [styled] if styled.strip() != draft.strip() else []})
        if "CANDIDATE:" in prompt:
            cand = prompt.split("CANDIDATE:", 1)[1].strip()
            n = prompt.count("EDITED:\n")               # number of accumulated examples
            true = edit_burden(cand, reviewer.edit("x", cand))
            # Model an in-context judge: its estimate is the true burden plus noise whose weight
            # shrinks as examples accumulate. The noise re-rolls each round (it depends on n), so
            # with few examples selection is unreliable (burden near baseline) and it sharpens
            # toward the true signal as the editor's style is seen more often.
            w = n / (n + 4)
            noise = _pseudo(f"{cand}#{n}")
            pred = w * true + (1 - w) * noise
            return json.dumps({"edit_fraction": max(0.0, min(1.0, pred))})
        return "{}"

    return MockProvider(config, completer=completer)
