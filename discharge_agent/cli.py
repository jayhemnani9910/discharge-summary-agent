"""Command-line entrypoint.

    python -m discharge_agent run --pdf data/patient2/source.pdf --patient patient2

Runs ingestion + the agent loop on one patient's PDF and writes draft.md, draft.json,
flags.md, trace.md and trace.jsonl into the output directory.
"""

from __future__ import annotations

import argparse
import os
import sys

from .agent import run_agent
from .config import Config
from .llm import build_provider
from .render import render_all
from .trace import Tracer


def _build_config(args) -> Config:
    cfg = Config()
    if args.chat_provider:
        cfg.chat_provider = args.chat_provider
    if args.vision_provider:
        cfg.vision_provider = args.vision_provider
    if args.max_steps:
        cfg.max_steps = args.max_steps
    if getattr(args, "inject_read_failure_page", None):
        cfg.inject_read_failure_page = args.inject_read_failure_page
    return cfg


def cmd_run(args) -> int:
    from .ingest import load_transcript_store

    cfg = _build_config(args)
    using_transcript = bool(args.transcript)
    if not args.pdf and not using_transcript:
        raise SystemExit("Provide --pdf (for live vision OCR) or --transcript (pre-extracted text).")
    cfg.require_keys(need_vision=not using_transcript)

    if args.patient:
        patient = args.patient
    elif args.pdf:
        patient = os.path.basename(os.path.dirname(os.path.abspath(args.pdf))) or "patient"
    else:
        patient = "patient"
    out_dir = args.out or os.path.join("outputs", patient)
    os.makedirs(out_dir, exist_ok=True)

    chat_provider = build_provider(cfg.chat_provider, cfg)
    vision_provider = None if using_transcript else build_provider(cfg.vision_provider, cfg)
    store = load_transcript_store(args.transcript) if using_transcript else None

    tracer = Tracer(os.path.join(out_dir, "trace.jsonl"))
    src = f"transcript={args.transcript}" if using_transcript else f"pdf={args.pdf} (vision={cfg.vision_provider})"
    print(f"Running discharge agent [chat={cfg.chat_provider}] on {src}", file=sys.stderr)
    if using_transcript:
        tracer.emit("ingest", message=f"Loaded pre-extracted transcript from {args.transcript} "
                    f"({store.num_pages} pages); live vision OCR skipped.")
    try:
        result = run_agent(args.pdf, patient, chat_provider, cfg, tracer,
                           cache_dir=os.path.join(args.cache, patient),
                           store=store, vision_provider=vision_provider)
    finally:
        tracer.close()

    render_all(result, out_dir)

    n_flags = len(result.state.flags)
    print(f"\nDone. {'COMPLETE' if result.finalized else 'PARTIAL'} draft in {result.steps} steps "
          f"({result.stop_reason}).")
    print(f"  {n_flags} flag(s) raised. Outputs written to {out_dir}/")
    print(f"  draft.md, draft.json, flags.md, trace.md, trace.jsonl")
    return 0


def cmd_demo(args) -> int:
    """Run the full pipeline offline on a synthetic record (no API key needed)."""
    from .demo import build_demo_provider, build_demo_store

    cfg = Config()
    cfg.chat_provider = "mock"
    cfg.vision_provider = "mock"
    out_dir = args.out or os.path.join("outputs", "demo")
    os.makedirs(out_dir, exist_ok=True)

    store = build_demo_store()
    provider = build_demo_provider(cfg)
    tracer = Tracer(os.path.join(out_dir, "trace.jsonl"))
    try:
        result = run_agent(None, "demo-patient", provider, cfg, tracer, store=store)
    finally:
        tracer.close()
    render_all(result, out_dir)
    print(f"Demo complete ({'COMPLETE' if result.finalized else 'PARTIAL'}, {result.steps} steps, "
          f"{len(result.state.flags)} flags). Outputs in {out_dir}/")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="discharge_agent",
                                     description="Agentic discharge-summary drafter (draft for review).")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="Run end-to-end on a synthetic record with no API key.")
    demo.add_argument("--out", help="Output directory (default: outputs/demo).")
    demo.set_defaults(func=cmd_demo)

    run = sub.add_parser("run", help="Run the agent on one patient's source PDF.")
    run.add_argument("--pdf", help="Path to the patient's source-note PDF (for live vision OCR).")
    run.add_argument("--transcript", help="Pre-extracted transcript JSON to use instead of live "
                     "vision OCR (e.g. reference/ground_truth_transcript.json).")
    run.add_argument("--patient", help="Patient id/label (default: parent folder name).")
    run.add_argument("--out", help="Output directory (default: outputs/<patient>).")
    run.add_argument("--cache", default="cache", help="Transcription cache directory.")
    run.add_argument("--chat-provider", choices=["gemini", "deepseek", "mock"],
                     help="Override the reasoning provider.")
    run.add_argument("--vision-provider", choices=["gemini", "mock"],
                     help="Override the vision/OCR provider.")
    run.add_argument("--max-steps", type=int, help="Override the hard step cap.")
    run.add_argument("--inject-read-failure-page", type=int,
                     help="Force the first read of this page to fail (demonstrates retry/flag).")
    run.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
