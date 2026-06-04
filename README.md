# Discharge Summary Agent

An agentic system that reads a patient's messy, scanned source notes and assembles a
structured **discharge summary draft for clinician review**. Its defining behaviour is
that it never invents a clinical fact: any value it cannot source from the documents is
marked MISSING / PENDING / CONFLICT and flagged, never filled with a plausible guess.

Built for the Dscribe take-home (Part 1). From-scratch agent loop, no agent framework.

## Quickstart

```bash
# 1. Install (Python 3.10+)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. See it run end-to-end with NO API key, on a small synthetic record:
python -m discharge_agent demo
#   -> writes outputs/demo/{draft.md, draft.json, flags.md, trace.md, trace.jsonl}

# 3. Tests (offline, no key):
pytest -q

# 4a. Run on the real patient with live vision OCR (Gemini does both OCR and reasoning):
cp .env.example .env          # add GEMINI_API_KEY (https://aistudio.google.com/apikey)
#   Default vision model is gemini-2.5-flash. The free tier is limited per key per day, so to
#   transcribe all 71 pages in one run supply several keys (rotated automatically on a 429):
#       GEMINI_API_KEYS=key1,key2,key3
#   The synthetic source PDF is not committed (see data/patient2/README.md); drop the provided
#   file in as data/patient2/source.pdf first. With no key, skip to step 4b (committed transcript).
python -m discharge_agent run --pdf data/patient2/source.pdf --patient patient2

# 4b. Or run with DeepSeek reasoning over a pre-extracted transcript (no vision quota needed):
#     add DEEPSEEK_API_KEY to .env, then:
python -m discharge_agent run --chat-provider deepseek \
    --transcript reference/ground_truth_transcript.json --patient patient2

# 5. Part 2 (learning from doctor edits), offline and reproducible with no key:
python -m discharge_agent learn
#   -> writes outputs/learning/{report.md, curve.svg}: the before/after edit-burden curve.
#   --live uses a real LLM judge; --patients <dir> learns across real patient folders.
```

Keys are read from the environment / `.env` and are never written to disk or committed
(`.env` is gitignored).

### Providers: vision vs reasoning

The system separates two LLM roles, which can use different providers:

- **Vision (OCR):** transcribes the scanned pages. Only a vision-capable model can do
  this (Gemini here). Run `4a` to do it live.
- **Reasoning:** runs the agent loop and the verifier (Gemini or DeepSeek; both work).

DeepSeek has no vision model, so it cannot read the scans on its own. For the run in
`outputs/patient2/` I used DeepSeek for reasoning over a **pre-extracted transcript**
(`reference/ground_truth_transcript.json`, produced once by a vision model over the same
71 pages) because the available Gemini free-tier vision quota was exhausted that day.
`--transcript` is the supported way to plug in an already-OCR'd record; with a vision key
the same run regenerates the transcript itself via `4a`. The transcript content is still
extracted from the source PDFs; only the OCR provider differs. Multiple Gemini keys
(`GEMINI_API_KEYS`, comma-separated) are rotated automatically on quota errors and the vision
model has a fallback list (`GEMINI_VISION_MODELS`), which is what makes a full live OCR of all
71 pages feasible on free-tier keys.

## Why this design: the data forces it

The provided record (`data/patient2/source.pdf`, 71 pages) is **scanned images with no
text layer**: a mix of typed printouts (discharge summary, lab reports) and a lot of
**handwriting** (ER charts, vitals, insulin/blood-sugar charts, nursing notes). Two
consequences drove the architecture:

1. `pdftotext`/OCR engines like tesseract fail on this; ingestion has to be a **vision
   model** transcribing each page. Handwriting is often only partly legible, so
   uncertainty is a first-class concept: unreadable text is marked `[illegible]` and the
   page is flagged, never guessed.
2. The notes genuinely contradict each other. The typed summary (page 1) gives the
   principal diagnosis as *acute gastroenteritis with dehydration + UTI*; the ER chart
   and the glucose charts point to *DKA / diabetes*. Some labs (urine culture, echo) are
   pending. These are exactly the cases the agent must surface rather than resolve.

## Architecture

```
PDF (scanned)
   |
   v
[ ingest.py ]  rasterise each page (pypdfium2) -> vision transcription (cached, retried)
   |            => PageStore: per-page text + doc_type + a compact index
   v
[ agent.py ]   from-scratch plan/act/observe loop over the index
   |              the model chooses tools; we execute and feed results back; it re-plans
   |              tools: read_page, search_notes, record_field, note_unavailable,
   |                     record_medication, reconcile_medications, drug_interaction_check,
   |                     flag_for_clinician_review, finalize_draft
   v
[ guardrails.py ] every recorded value must cite a page + a verbatim quote that is
   |               actually on that page, or the tool call is rejected
   v
[ verify.py ]   independent LLM re-reads every value against its source; unsupported
   |             values are removed and replaced with a flag
   v
[ render.py ]   draft.md / draft.json / flags.md / trace.md / trace.jsonl
```

Module map: `config.py` (all tunables/caps), `llm/` (provider abstraction with Gemini
and DeepSeek implementations plus a deterministic offline mock), `ingest.py` (PDF ->
transcripts, and `load_transcript_store` for a pre-extracted transcript),
`state.py` (the typed draft model), `tools.py` (the agent's actions), `guardrails.py`
and `verify.py` (the two safety layers), `prompts.py`, `agent.py` (the loop), `trace.py`
(observability), `render.py` (outputs), `cli.py` (entrypoint), `demo.py` (offline run),
`learning/` (Part 2: the edit-learning loop, isolated from the Part 1 agent).

### The agent loop (Hard requirement #1)

`agent.py` is a real loop, not a fixed pipeline. Each step: the model receives the
running conversation plus the tool schemas, emits a short reasoning note and one or more
tool calls; we execute them, append the results, and it decides what to do next. The
order of work (which pages to read, when to reconcile medications, when to check
interactions, when to flag, when to finalize) is the model's. The loop only enforces
safety and the control cap. `finalize_draft` is gated: it refuses until every required
section has been addressed and the medication checks have run, returning the list of
what is still outstanding so the model can continue.

### No fabrication: the core guardrail (Hard requirement #3)

Three layers, so the property does not depend on the model choosing to comply:

1. **Typed state.** In `state.py` a section is either a list of `SourcedValue` (each
   carrying `source_page` and a verbatim `quote`) or an explicit non-value status
   (MISSING / PENDING / CONFLICT / NOT_DOCUMENTED). There is no representation for a
   value without a citation.
2. **Citation check at write time.** `record_field` is rejected unless the cited quote
   is found verbatim on the cited page (`guardrails.quote_supported`). No citation, no
   value; the model is told to flag instead.
3. **Independent verification after the fact.** `verify.py` re-reads every recorded
   value against its source page with a separate, deliberately strict prompt. A value
   the source does not directly support is removed and replaced with a flag. If the
   verifier itself fails, the value is left marked *unverified* with a flag, never
   silently accepted.

The output is always labelled a draft for review and is never auto-finalized.

### Handling the messy cases

- **Pending / missing (Hard req #4):** a pending lab is recorded as PENDING and added to
  `pending_results`; an absent field is MISSING. Neither is ever filled with a value.
- **Medication reconciliation (Hard req #5):** the agent records admission and discharge
  medications (each with page + quote), then `reconcile_medications` diffs them into
  added/stopped/changed/continued. For each change it looks for a documented, quote-backed
  reason; any change without one is flagged automatically.
- **Conflicts (Hard req #6):** recording two different values for a single-value section
  marks it CONFLICT and keeps both with their sources; the conflict is flagged and never
  silently resolved.
- **Tools and when to use them (Hard req #7):** `drug_interaction_check` is a mock
  external lookup (a small local table, including additive QT-prolongation for
  e.g. ondansetron + a fluoroquinolone). The agent decides when to call it; any
  interaction it returns is flagged and escalated, and its disclaimer states that absence
  of a result is not a guarantee of safety.
- **Failure handling (Hard req #8):** all LLM/tool calls go through one retry/backoff
  helper (`retry.py`) so retries are visible in the trace. Transient errors back off and
  retry; fatal errors propagate. A page that cannot be transcribed becomes an explicit
  *unreadable* page that is flagged, never a blank-but-fine page. Nothing turns a failure
  into a fake success. (`--inject-read-failure-page N` forces a page's first read to fail
  to demonstrate the recovery path.)
- **Control (Hard req #9):** a hard step cap (`AGENT_MAX_STEPS`, default 80) and a
  consecutive-tool-error cap. If a cap is hit, the loop still emits a partial draft with
  every unfinished section forced to a flagged MISSING and a control flag explaining why.
- **Observability (Hard req #10):** `trace.jsonl` and `trace.md` record each step as
  reasoning -> tool -> inputs -> result, plus ingestion, retries, verification and the
  finalize decision.

## Outputs (per patient)

| File | Contents |
|---|---|
| `draft.md` | the discharge summary draft, each field cited or marked MISSING/PENDING/CONFLICT |
| `draft.json` | the same, machine-readable (the full draft state) |
| `flags.md` | every flag for the clinician, grouped by severity |
| `trace.md` / `trace.jsonl` | the step-by-step agent trace |

## Results on the provided patient

Full outputs are in `outputs/patient2/` (`draft.md`, `draft.json`, `flags.md`,
`trace.md`, `trace.jsonl`). On the 71-page record the agent ran 66 steps and raised
**41 flags**. What it surfaced rather than guessed:

- **Principal-diagnosis conflict:** the typed discharge summary (page 1) says *acute
  gastroenteritis with dehydration + UTI*, while the ER chart and consult notes say *DKA*
  (glucose 443 mg/dL). The agent recorded the conflict and flagged it instead of choosing.
- **A demographics conflict the source itself contains:** page 1 refers to the patient as
  "she", page 46 as "his". Demographics is left MISSING (the name is illegible) and the
  conflict is flagged.
- **Medication reconciliation (17 flags):** insulin (Actrapid + Lantus), IV meropenem, IV
  pantoprazole and others were on the inpatient charts but absent from the discharge list,
  each flagged as stopped with no documented reason. The dropped insulin in a DKA / HbA1c
  13.9% patient is exactly the kind of omission a reviewer needs to see.
- **Drug interaction, escalated:** the mock checker flagged additive QT-prolongation risk
  from loperamide + ofloxacin + ondansetron on the discharge list.
- **Pending results** (urine culture awaited) marked PENDING, not invented.

During the run the in-code guardrail **rejected 29 attempts** to record a value whose
quote was not verbatim on the cited page, and the post-hoc verifier independently checked
39 recorded values. (An earlier run shows the verifier doing its other job: dropping a
"bilateral pyelonephritis" value because the source only said "suggestive of", and
refusing a hospital-course narrative that cited a page which did not support it.)

The trace for all of this is in `outputs/patient2/trace.md`.

## Part 2: Learning from Doctor Edits

In production a clinician edits the draft before finalizing, and those edits are the signal
to learn from. The loop in `discharge_agent/learning/` turns that into a best-of-N selection
problem with a reward model, and is built so the real clinician drops into the same interface
the simulated reviewer uses now. Run it offline with `python -m discharge_agent learn`.

- **Reward signal.** Section-level normalized edit distance between a draft and its reviewed
  form (`1 - difflib ratio`, in `learning/metrics.py`). Less editing means a higher reward.
- **Edit source (`learning/edit_source.py`).** An `EditSource` interface with two
  implementations behind it. `SimulatedReviewer` applies a fixed, hidden, style-only house
  policy (expand abbreviations, reformat dates, normalize citation and list style) to
  manufacture (draft, edited) pairs; `DoctorEdits` ingests real clinician edits. The reward
  model and loop never know which is plugged in, so production swaps one for the other and
  changes nothing else. The policy is deliberately style-only: it never picks a diagnosis,
  fills a missing value, or removes a flag, so reducing edit distance means matching house
  style, never changing medicine.
- **Learning mechanism (`learning/reward.py`, `learning/select.py`).** Best-of-N candidate
  selection driven by a reward model that predicts edit burden. The model is an LLM judge that
  learns the editor's style from the accumulated (draft, edited) pairs given in its context,
  rather than from rules hand-coded to match our own simulator, so it would learn a real
  doctor's different style the same way. As pairs accumulate, its estimate sharpens and
  selection improves. Candidate 0 is always the agent's own draft, so best-of-N can never do
  worse than the baseline, and cold-start (no pairs) keeps that draft.
- **The anti-gaming safety gate.** Lowering edit distance by getting vaguer or by dropping the
  flags that make a draft safe is the known failure of edit-distance rewards. The gate
  (`learning/metrics.safety_retained`) disqualifies any candidate that hides a status marker
  (MISSING / PENDING / CONFLICT) or changes the set of documented numbers, so an unsafe
  candidate is unselectable no matter how low its predicted burden. Safety retention is
  reported next to the curve and stays at 100%.
- **Measured result, and what it does and does not show.** On a held-out split the reward model
  never trains on, mean edit burden drops from a **0.154 baseline to ~0.036** (about 80% lower) as
  training pairs accumulate, with safety retention at 100% throughout (`outputs/learning/report.md`,
  `curve.svg`, regenerated by `learn`). Read it precisely: the no-key `learn` run uses a
  deterministic stand-in judge that has oracle access to the reviewer (it computes the true edit
  burden directly) on a fixed confidence schedule, and the candidate it ends up selecting is the
  reviewer's own restyle. So the reproducible offline curve proves the mechanism (best-of-N
  selection, the safety gate, and the section-level reward wired together, with a sound
  measurement); it is not on its own evidence that an LLM infers the editor's style from a few
  pairs. That claim is the `learn --live` path, where a real LLM judge sees only the in-context
  (draft, edited) examples with no oracle access, and `learn --patients <dir>` runs the same loop
  across real patient folders. The `--live` curve needs an API key and is not reproduced in CI.

Limitations of the Part 2 loop are listed below with the rest.

## Testing

`pytest -q` runs 71 offline tests (no API key). Part 1 coverage: the citation guardrail,
conflict surfacing, pending/missing handling, medication reconciliation (including that a
quote-backed reason suppresses the flag), the QT drug-interaction detection, the verifier
downgrading unsupported values and not failing silently, the full loop happy path, the
step cap producing a partial-not-crashed draft, and graceful handling when the LLM is
unavailable. Part 2 coverage: the edit-burden metric and the safety gate (blocking fabricated
or dropped facts and hidden flags), the deterministic style-only reviewer, best-of-N with the
gate in front of it, cold-start falling back to the agent's draft, candidate generation
persisting and degrading to the original on failure, and an end-to-end learning run whose
held-out burden falls below baseline while safety retention holds at 100%.

## Limitations and what I would do with more time

- Transcription quality is bounded by the vision model on low-resolution handwriting;
  the system is built to flag uncertainty rather than paper over it, but a higher-DPI
  scan or a medical-OCR model would help. A confidence score per page could gate which
  fields need a second read.
- `search_notes` is keyword-based. Embeddings would improve recall on synonyms
  (for example "sugar" vs "glucose").
- The mock drug-interaction table is intentionally small; a real checker (or an API)
  would slot in behind the same tool interface.
- The post-hoc verifier can narrow a conflict: if it rejects one side of a two-value CONFLICT
  field as unsupported on its cited page, that field collapses to the surviving value. The dropped
  side is still raised as a HIGH flag, and the code-driven `detect_conflicts` scan that runs after
  the verifier re-surfaces a documented disagreement, so the signal is not lost; but a conflict
  that only the verifier-dropped page documented would survive as a flag rather than a CONFLICT
  field.
- The guaranteed conflict scan is scoped to single-valued fields (principal diagnosis, both dates,
  allergies, discharge condition). Secondary diagnoses (a list) and per-medication disagreements
  are not cross-scanned; those depend on the agent surfacing them during the loop.
- **Part 2 data scarcity (cold start).** The assignment ships one patient plus the synthetic
  demo record, so the held-out split is small and the reward model has few pairs early; with
  little context the judge is unreliable and best-of-N stays near the agent's own draft.
  Improvement appears as pairs accumulate. More patients (the `--patients` path) and real
  clinician edits would give a denser curve.
- **Part 2 gaming.** Optimizing to reduce edits can be gamed by becoming vaguer or by dropping
  flags. The safety gate plus reporting safety retention next to the curve is the guard, and it
  holds at 100% here, but the gate is a text-level check (status markers plus the documented-
  number multiset); a determined adversary editing only prose could still trade specificity for
  a lower score. A stronger version would re-run the Part 1 verifier on each selected candidate.
- **Part 2 offline judge overstates learning.** As the Measured-result note above explains, the
  no-key `learn` judge has oracle access and a fixed confidence schedule, so the reproducible curve
  demonstrates the selection / gate / measurement plumbing rather than an LLM learning the editor's
  style from few pairs. The genuine in-context-learning evidence is `learn --live` (needs a key),
  and the real signal is real clinician edits, which drop into the same `EditSource` interface with
  no code change.
