"""Records for the learning loop, built generically from Part 1 output (Part 2 #4).

An ``Item`` is one section of one patient's draft: ``(record_id, section_key, draft_text)``. The
loop never assumes which patient it is, so the evaluator's hidden patients are the same code
path. ``items_from_result`` turns any Part 1 ``AgentResult`` into items, which is the production
entry point (run the agent on a patient, hand the result here). ``demo_items`` builds the one
synthetic record that already ships with Part 1, fully offline, so the loop and its before/after
curve run with no API key.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from ..render import section_text
from ..schema import SECTION_KEYS


@dataclass(frozen=True)
class Item:
    record_id: str
    section_key: str
    draft_text: str


def items_from_result(record_id: str, result) -> list[Item]:
    """Every section of a Part 1 draft as learning items. Works for any patient."""
    return [Item(record_id, k, section_text(result.state, k)) for k in SECTION_KEYS]


def demo_items(config) -> list[Item]:
    """The shipped synthetic record, produced offline by the Part 1 demo pipeline."""
    from ..agent import run_agent
    from ..demo import build_demo_provider, build_demo_store
    from ..trace import Tracer

    store = build_demo_store()
    provider = build_demo_provider(config)
    result = run_agent(None, "demo-synthetic", provider, config, Tracer(), store=store)
    return items_from_result("demo-synthetic", result)


def split(items: list[Item], seed: int = 0, heldout_frac: float = 0.5) -> tuple[list[Item], list[Item]]:
    """Train / held-out split. With more than one record, hold out a whole record (the strongest
    generalization test: learn the style on some patients, apply it to an unseen one). With a
    single record, split its sections deterministically."""
    record_ids = sorted({it.record_id for it in items})
    if len(record_ids) > 1:
        held_id = record_ids[-1]
        train = [it for it in items if it.record_id != held_id]
        heldout = [it for it in items if it.record_id == held_id]
        return train, heldout

    shuffled = list(items)
    random.Random(seed).shuffle(shuffled)
    cut = max(1, int(len(shuffled) * (1 - heldout_frac)))
    return shuffled[:cut], shuffled[cut:]
