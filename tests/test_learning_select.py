"""Best-of-N + reward model + safety gate, driven by a deterministic mock judge. A trained
model picks the house-styled candidate; the gate blocks unsafe candidates; cold start keeps the
agent's own draft."""

import json

from discharge_agent.learning.candidates import candidates_for
from discharge_agent.learning.reward import RewardModel
from discharge_agent.learning.select import best_of_n
from discharge_agent.llm.mock import MockProvider


def _styled(text: str) -> bool:
    # The mock judge's notion of "house style": abbreviations expanded.
    return "Tablet" in text and "twice daily" in text


def _judge(system, prompt, json_mode):
    # The candidate is the text after "CANDIDATE:". Low burden if already in house style.
    cand = prompt.split("CANDIDATE:", 1)[1]
    return json.dumps({"edit_fraction": 0.1 if _styled(cand) else 0.8})


def test_best_of_n_picks_house_styled_candidate(fast_config):
    model = RewardModel(MockProvider(fast_config, completer=_judge), fast_config)
    model.fit([("Tab X 40mg BD", "Tablet X 40 mg twice daily")])  # one informative pair
    cands = ["Tab Pantoprazole 40 mg BD", "Tablet Pantoprazole 40 mg twice daily"]
    sel = best_of_n(cands, model)
    assert sel.index == 1 and _styled(sel.text)
    assert sel.n_blocked == 0


def test_cold_start_keeps_agent_draft(fast_config):
    # Untrained model returns a flat prior; ties resolve to candidate 0 (the agent's draft).
    model = RewardModel(MockProvider(fast_config, completer=_judge), fast_config)
    assert model.n_exemplars == 0
    cands = ["Tab Pantoprazole 40 mg BD", "Tablet Pantoprazole 40 mg twice daily"]
    sel = best_of_n(cands, model)
    assert sel.index == 0


def test_safety_gate_blocks_unsafe_candidate(fast_config):
    # A "nicer" candidate that drops the CONFLICT marker and a number must never be selected,
    # even though the judge would score it as needing no edits.
    def judge_loves_unsafe(system, prompt, json_mode):
        cand = prompt.split("CANDIDATE:", 1)[1]
        return json.dumps({"edit_fraction": 0.0 if "gastroenteritis only" in cand else 0.9})

    model = RewardModel(MockProvider(fast_config, completer=judge_loves_unsafe), fast_config)
    model.fit([("a", "b")])
    ref = "Principal Diagnosis: CONFLICT. gastroenteritis (page 1) vs DKA (page 3)."
    unsafe = "Principal diagnosis: gastroenteritis only."   # marker + numbers + a value dropped
    sel = best_of_n([ref, unsafe], model, reference_text=ref)
    assert sel.text == ref and sel.index == 0
    assert sel.n_blocked == 1


def test_candidate_generation_persists_and_replays(fast_config, tmp_path):
    def gen(system, prompt, json_mode):
        return json.dumps({"variants": ["Tablet Pantoprazole 40 mg twice daily"]})

    provider = MockProvider(fast_config, completer=gen)
    out = candidates_for(provider, "discharge_medications", "Tab Pantoprazole 40mg BD", 2,
                         fast_config, cache_dir=str(tmp_path))
    assert out[0] == "Tab Pantoprazole 40mg BD" and len(out) == 2
    assert provider.complete_calls == 1
    # Second call replays from disk: the provider is not invoked again.
    out2 = candidates_for(provider, "discharge_medications", "Tab Pantoprazole 40mg BD", 2,
                          fast_config, cache_dir=str(tmp_path))
    assert out2 == out and provider.complete_calls == 1


def test_candidate_generation_failure_degrades_to_original(fast_config, tmp_path):
    def boom(system, prompt, json_mode):
        raise RuntimeError("generator offline")

    provider = MockProvider(fast_config, completer=boom)
    out = candidates_for(provider, "allergies", "NKDA", 3, fast_config, cache_dir=str(tmp_path))
    assert out == ["NKDA"]
