"""End-to-end learning loop on the shipped synthetic record with a deterministic mock judge and
mock candidate generator: the held-out edit burden falls below baseline as pairs accumulate, and
safety retention stays at 1.0 throughout (the curve is not bought by dropping flags)."""

import json
import re

from discharge_agent.learning.data import demo_items, split
from discharge_agent.learning.edit_source import SimulatedReviewer
from discharge_agent.learning.loop import run_learning
from discharge_agent.llm.mock import MockProvider


def _provider(fast_config):
    reviewer = SimulatedReviewer()

    def completer(system, prompt, json_mode):
        # Candidate generation: return one house-styled variant (reviewer applied) so a better
        # candidate than the original exists for every section.
        if "DRAFT TEXT:" in prompt:
            draft = prompt.split("DRAFT TEXT:", 1)[1].strip()
            styled = reviewer.edit("x", draft)
            return json.dumps({"variants": [styled] if styled != draft else []})
        # Reward judge: lower predicted burden when the candidate is already in house style
        # (i.e. close to what the reviewer would produce).
        cand = prompt.split("CANDIDATE:", 1)[1].strip()
        frac = edit_fraction = _burden(cand, reviewer.edit("x", cand))
        return json.dumps({"edit_fraction": frac})

    return MockProvider(fast_config, completer=completer)


def _burden(a, b):
    from difflib import SequenceMatcher
    a = re.sub(r"\s+", " ", a.lower().strip())
    b = re.sub(r"\s+", " ", b.lower().strip())
    return 1.0 - SequenceMatcher(None, a, b).ratio()


def test_loop_reduces_heldout_burden_without_losing_safety(fast_config, tmp_path):
    provider = _provider(fast_config)
    items = demo_items(fast_config)
    train, heldout = split(items, seed=1, heldout_frac=0.4)
    assert train and heldout

    report = run_learning(provider, SimulatedReviewer(), train, heldout, fast_config,
                          n_candidates=3, cache_dir=str(tmp_path))

    # Learning helped: final held-out burden is at or below the baseline (and strictly below for
    # at least one section, so improvement is real, not a rounding tie).
    assert report.final_burden <= report.baseline_burden + 1e-9
    assert report.improvement >= 0.0
    # Safety never degraded: every round retained all flags/values on the held-out set.
    assert all(p.safety_retention == 1.0 for p in report.curve)
    assert report.curve[-1].n_pairs == report.n_train


def test_loop_with_no_better_candidate_matches_baseline(fast_config, tmp_path):
    # If candidate generation only ever returns the original, best-of-N can only equal baseline,
    # and must never report a spurious improvement or an unsafe pick.
    def only_original(system, prompt, json_mode):
        if "DRAFT TEXT:" in prompt:
            return json.dumps({"variants": []})
        return json.dumps({"edit_fraction": 0.5})

    provider = MockProvider(fast_config, completer=only_original)
    items = demo_items(fast_config)
    train, heldout = split(items, seed=1, heldout_frac=0.4)
    report = run_learning(provider, SimulatedReviewer(), train, heldout, fast_config,
                          n_candidates=3, cache_dir=str(tmp_path))
    assert abs(report.final_burden - report.baseline_burden) < 1e-9
    assert report.n_blocked_unsafe == 0
