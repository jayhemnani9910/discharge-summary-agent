"""The agent loop (Hard requirements #1, #8, #9).

A from-scratch plan/act/observe loop (no agent framework). On each step the model is
given the running conversation and the tool schemas; it chooses tools, we execute them,
feed back the results, and it re-plans. The order of work is the model's; the loop only
enforces safety and the control cap.

Termination, in priority order:
* the model calls ``finalize_draft`` and the structural guardrail says it is ready;
* the hard step cap is reached;
* the model stalls (several steps with no tool call);
* the LLM becomes unavailable (retries exhausted / fatal error).

In every non-ideal case the loop still produces a partial draft with the gaps marked and
an explicit control flag. It never crashes and never reports an unfinished draft as done.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .ingest import transcribe_pdf
from .llm.base import (
    FatalLLMError,
    LLMError,
    msg_model_turn,
    msg_tool_results,
    msg_user_text,
)
from .prompts import bootstrap_message, system_prompt
from .retry import call_with_retries
from .schema import SECTION_KEYS
from .state import DraftState, FieldStatus, Severity
from .tools import TOOL_SPECS, ToolDispatcher
from .verify import detect_conflicts, verify_draft


@dataclass
class AgentResult:
    patient_id: str
    state: DraftState
    store: object
    tracer: object
    finalized: bool
    steps: int
    stop_reason: str


def run_agent(pdf_path, patient_id, provider, config, tracer, cache_dir="cache",
              store=None, vision_provider=None) -> AgentResult:
    # 1) Deterministic ingestion (vision OCR, cached). A pre-built ``store`` may be
    #    supplied (a loaded transcript, or in tests, to exercise the loop without a PDF).
    #    ``provider`` runs the reasoning loop; ``vision_provider`` (if different) does OCR.
    if store is None:
        store = transcribe_pdf(pdf_path, vision_provider or provider, config, tracer, cache_dir)

    # 2) Fresh draft + surface unreadable pages immediately so they can never be lost.
    state = DraftState(patient_id, SECTION_KEYS)
    for page in store.unreadable_pages():
        state.add_flag("source_document", "unreadable",
                       f"Page {page} could not be transcribed; its content is unknown.",
                       Severity.HIGH, [page])
    for page in store.partially_legible_pages():
        state.add_flag("source_document", "unreadable",
                       f"Page {page} is only partially legible; some content could not be read.",
                       Severity.MEDIUM, [page])

    dispatcher = ToolDispatcher(store, state, config, tracer)
    system = system_prompt()
    history = [msg_user_text(bootstrap_message(store, patient_id))]

    steps = 0
    stalls = 0
    consecutive_errors = 0
    stop_reason = ""
    finalized = False

    while steps < config.max_steps:
        steps += 1
        try:
            response = call_with_retries(
                lambda: provider.chat(system, history, TOOL_SPECS),
                config=config, tracer=tracer, what="chat")
        except FatalLLMError as exc:
            stop_reason = f"fatal LLM error: {exc}"
            tracer.emit("control", message=stop_reason)
            break
        except LLMError as exc:
            stop_reason = f"LLM unavailable after retries: {exc}"
            tracer.emit("control", message=stop_reason)
            break

        if not response.tool_calls:
            # The model emitted text but took no action. Nudge it; give up after a few.
            stalls += 1
            tracer.step(steps, reasoning=response.text, tool="(none)", tool_input={},
                        result="no tool call", severity="low")
            if stalls >= 3:
                stop_reason = "model stalled (no tool calls)"
                tracer.emit("control", message=stop_reason)
                break
            history.append(msg_model_turn(response.text or "(no text)", []))
            history.append(msg_user_text(
                "You did not call a tool. Use the tools to read pages and record sourced values, "
                "or call finalize_draft. Do not write the summary as prose."))
            continue
        stalls = 0

        # Record the model's turn (reasoning text + the tool calls it made) as one message.
        history.append(msg_model_turn(response.text, response.tool_calls))

        results = []
        finalize_ready = False
        step_productive = False
        step_unproductive = False
        for call in response.tool_calls:
            result = dispatcher.dispatch(call.name, call.args)
            unproductive = isinstance(result, dict) and (result.get("error") or result.get("rejected"))
            severity = "warn" if unproductive else "info"
            tracer.step(steps, reasoning=response.text, tool=call.name,
                        tool_input=call.args, result=result, severity=severity)
            results.append({"name": call.name, "id": call.id, "result": result})
            if unproductive:
                step_unproductive = True
            else:
                step_productive = True
            if call.name == "finalize_draft" and isinstance(result, dict) and result.get("ready"):
                finalize_ready = True

        # The breaker counts consecutive fully-failed STEPS, not individual calls: a single bad
        # batch (e.g. admission-med quotes that don't match the OCR text) is one stuck step, and a
        # model that does anything productive next step resets it. Only a sustained stall trips it.
        if step_unproductive and not step_productive:
            consecutive_errors += 1
        else:
            consecutive_errors = 0

        history.append(msg_tool_results(results))

        if finalize_ready:
            finalized = True
            stop_reason = "finalized by agent"
            break
        if consecutive_errors >= config.max_consecutive_tool_errors:
            stop_reason = "too many consecutive tool errors"
            tracer.emit("control", message=stop_reason)
            break

    if steps >= config.max_steps and not finalized:
        stop_reason = stop_reason or "hit hard step cap"
        tracer.emit("control", message=f"Step cap reached ({config.max_steps}); finalizing partial draft")

    # Force every still-empty required section into an explicit, flagged MISSING state so
    # the output is never silently incomplete.
    if not finalized:
        for key in state.unhandled_sections():
            state.set_status(key, FieldStatus.MISSING,
                             "not reached before the agent stopped")
            state.add_flag(key, "missing",
                           f"Section '{key}' was not completed ({stop_reason}); needs manual entry.",
                           Severity.HIGH, [])
        state.incomplete_reason = stop_reason

    # 3) Independent verification of every recorded value before we emit anything.
    verify_summary = verify_draft(provider, store, state, config, tracer)
    # 4) A guaranteed conflict scan for single-valued fields, independent of the agent's prompt,
    #    so a disagreement the loop missed still surfaces as a CONFLICT / review flag.
    detect_conflicts(provider, store, state, config, tracer)

    state.finalized = finalized
    tracer.emit("finalize",
                message=f"{'COMPLETE' if finalized else 'PARTIAL'} draft after {steps} steps "
                f"({stop_reason}); verifier {verify_summary}")
    return AgentResult(patient_id=patient_id, state=state, store=store, tracer=tracer,
                       finalized=finalized, steps=steps, stop_reason=stop_reason)
