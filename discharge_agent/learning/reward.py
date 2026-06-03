"""The reward model (Part 2 learning mechanism, the learned part).

Best-of-N needs to pick the candidate a reviewer would edit least, but at draft time the reviewer
is not available; that is the whole point. So a reward model predicts the edit burden from what
it has learned. Crucially it learns the editor's style from the accumulated (draft, edited)
pairs themselves, given as in-context examples, rather than from rules we wrote. That is what
makes it general: point it at a real clinician's edits and it learns that style the same way,
with no code change. As pairs accumulate, the estimate sharpens and selection improves.

Cold start is handled explicitly: with no examples the model returns a flat prior, so best-of-N
ties and keeps the agent's own draft (candidate 0). Learning can only help, never regress below
the baseline. A judge failure degrades the same way.
"""

from __future__ import annotations

import json

from ..retry import call_with_retries

_JUDGE_SYSTEM = (
    "You estimate how heavily a specific (unknown to you) clinician would edit a draft section, "
    "having seen examples of their past edits. Output the fraction of the text you expect them "
    "to change, from 0.0 (they would leave it as-is) to 1.0 (they would rewrite it). Judge only "
    "wording and formatting style, not clinical correctness. Reply with JSON only."
)

_MAX_EXEMPLARS = 8
_NEUTRAL = 0.5


def _parse_score(raw: str):
    s = (raw or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
    try:
        data = json.loads(s[s.find("{"):] if "{" in s else s)
        return float(data.get("edit_fraction"))
    except (json.JSONDecodeError, TypeError, ValueError):
        import re
        m = re.search(r"[01](?:\.\d+)?", s)
        return float(m.group(0)) if m else None


class RewardModel:
    """Predicts a candidate's edit burden in [0, 1] from accumulated (draft, edited) examples."""

    def __init__(self, provider, config, tracer=None):
        self._provider = provider
        self._config = config
        self._tracer = tracer
        self._exemplars: list[tuple[str, str]] = []
        self._cache: dict[tuple[int, str], float] = {}

    def fit(self, pairs: list[tuple[str, str]]) -> None:
        """Learn from (draft_text, edited_text) pairs. Keeps the informative ones (where the
        editor actually changed something) as in-context exemplars; resets the score cache."""
        informative = [(d, e) for d, e in pairs if (d or "").strip() != (e or "").strip()]
        self._exemplars = informative[-_MAX_EXEMPLARS:]
        self._cache = {}

    @property
    def n_exemplars(self) -> int:
        return len(self._exemplars)

    def predict(self, candidate_text: str) -> float:
        """Predicted edit burden for one candidate. Flat prior when untrained or on failure."""
        if not self._exemplars:
            return _NEUTRAL
        ck = (len(self._exemplars), candidate_text)
        if ck in self._cache:
            return self._cache[ck]
        examples = "\n\n".join(
            f"DRAFT:\n{d}\nEDITED:\n{e}" for d, e in self._exemplars)
        prompt = (
            "Examples of this clinician's past edits:\n\n" + examples
            + "\n\nNow estimate the edit fraction for this candidate. Reply JSON: "
            "{\"edit_fraction\": float}.\n\nCANDIDATE:\n" + candidate_text)
        try:
            raw = call_with_retries(
                lambda: self._provider.complete(_JUDGE_SYSTEM, prompt, json_mode=True),
                config=self._config, tracer=self._tracer, what="reward-judge")
        except Exception as exc:  # noqa: BLE001 - a failed judge is neutral, never fatal
            if self._tracer:
                self._tracer.emit("learn", message=f"reward judge failed: {exc}")
            return _NEUTRAL
        score = _parse_score(raw)
        if score is None:
            return _NEUTRAL
        score = max(0.0, min(1.0, score))
        self._cache[ck] = score
        return score
