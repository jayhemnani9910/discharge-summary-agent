"""Observability (Hard requirement #10).

Every meaningful event the agent takes is appended here as a structured record and
written to a JSONL file as it happens (so a crash still leaves a partial trace). The
same records render to a human-readable markdown trace. Each agent step captures the
model's reasoning, the tool it chose, the inputs, and the result.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field


@dataclass
class TraceEvent:
    seq: int
    kind: str                # ingest | step | tool | retry | control | verify | finalize | note
    data: dict
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"seq": self.seq, "kind": self.kind, "ts": round(self.ts, 3), **self.data}


class Tracer:
    def __init__(self, jsonl_path: str | None = None):
        self.events: list[TraceEvent] = []
        self._seq = 0
        self._fh = open(jsonl_path, "w", encoding="utf-8") if jsonl_path else None

    def emit(self, kind: str, **data) -> TraceEvent:
        self._seq += 1
        ev = TraceEvent(seq=self._seq, kind=kind, data=data)
        self.events.append(ev)
        if self._fh:
            self._fh.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
            self._fh.flush()
        return ev

    # Convenience wrappers for the common event kinds -----------------------
    def step(self, step: int, reasoning: str, tool: str, tool_input: dict, result, severity="info"):
        return self.emit(
            "step",
            step=step,
            reasoning=reasoning,
            tool=tool,
            tool_input=tool_input,
            result=_summarise(result),
            severity=severity,
        )

    def note(self, message: str, **data):
        return self.emit("note", message=message, **data)

    def close(self):
        if self._fh:
            self._fh.close()
            self._fh = None

    # Rendering -------------------------------------------------------------
    def to_markdown(self) -> str:
        lines = ["# Agent step trace", ""]
        for ev in self.events:
            d = ev.data
            if ev.kind == "step":
                lines.append(f"## Step {d.get('step')} — `{d.get('tool')}`")
                if d.get("reasoning"):
                    lines.append(f"- **Reasoning:** {d['reasoning']}")
                lines.append(f"- **Tool:** `{d.get('tool')}`")
                if d.get("tool_input"):
                    lines.append(f"- **Inputs:** `{json.dumps(d['tool_input'], ensure_ascii=False)}`")
                lines.append(f"- **Result:** {d.get('result')}")
                lines.append("")
            elif ev.kind == "ingest":
                lines.append(f"- _[ingest]_ {d.get('message','')}")
            elif ev.kind == "retry":
                lines.append(
                    f"- _[retry]_ {d.get('what','')} attempt {d.get('attempt')} after error: {d.get('error')}"
                )
            elif ev.kind == "control":
                lines.append(f"- **[control]** {d.get('message','')}")
            elif ev.kind == "verify":
                lines.append(f"- _[verify]_ {d.get('message','')}")
            elif ev.kind == "finalize":
                lines.append("")
                lines.append(f"## Finalize — {d.get('message','')}")
            else:
                lines.append(f"- _[{ev.kind}]_ {d.get('message', json.dumps(d, ensure_ascii=False))}")
        return "\n".join(lines) + "\n"


def _summarise(result, limit: int = 600) -> str:
    """Compact a tool result for the trace without dumping huge page transcripts."""
    if isinstance(result, dict):
        text = json.dumps(result, ensure_ascii=False)
    else:
        text = str(result)
    if len(text) > limit:
        text = text[:limit] + f"... [+{len(text) - limit} chars]"
    return text
