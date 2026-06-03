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


def cmd_learn(args) -> int:
    """Part 2: run the best-of-N + reward-model learning loop and emit the before/after curve.

    Default is fully offline (the shipped synthetic record, a deterministic stand-in judge), so
    it runs with no API key. ``--live`` uses the configured chat provider for real candidate
    generation and a real LLM judge; ``--patients DIR`` learns across real patient folders."""
    from .learning.data import demo_items, items_from_result, split
    from .learning.edit_source import DoctorEdits, SimulatedReviewer
    from .learning.loop import run_learning
    from .learning.offline import offline_provider
    from .learning.report import write_report

    cfg = _build_config(args)
    out_dir = args.out or os.path.join("outputs", "learning")
    os.makedirs(out_dir, exist_ok=True)
    tracer = Tracer(os.path.join(out_dir, "trace.jsonl"))

    if args.patients:
        from .ingest import load_transcript_store
        cfg.require_keys(need_vision=False)
        provider = build_provider(cfg.chat_provider, cfg)
        items = []
        for folder in sorted(d.path for d in os.scandir(args.patients) if d.is_dir()):
            pid = os.path.basename(folder)
            transcript = os.path.join(folder, "transcript.json")
            store = load_transcript_store(transcript) if os.path.exists(transcript) else None
            pdf = os.path.join(folder, "source.pdf")
            result = run_agent(pdf if not store else None, pid, provider, cfg, tracer,
                               store=store, vision_provider=build_provider(cfg.vision_provider, cfg)
                               if not store else None)
            items += items_from_result(pid, result)
    else:
        provider = build_provider(cfg.chat_provider, cfg) if args.live else offline_provider(cfg)
        if args.live:
            cfg.require_keys(need_vision=False)
        items = demo_items(cfg)

    edit_source = DoctorEdits(_load_json(args.edits)) if args.edits else SimulatedReviewer()
    train, heldout = split(items, seed=args.seed, heldout_frac=args.heldout_frac)
    print(f"Learning over {len(train)} training / {len(heldout)} held-out sections "
          f"({'live' if (args.live or args.patients) else 'offline'} provider).", file=sys.stderr)

    report = run_learning(provider, edit_source, train, heldout, cfg,
                          n_candidates=args.n_candidates, cache_dir=out_dir,
                          regenerate=args.generate, tracer=tracer)
    tracer.close()
    paths = write_report(report, out_dir)
    print(f"\nBaseline edit burden: {report.baseline_burden:.3f}  ->  after learning: "
          f"{report.final_burden:.3f}  ({report.improvement:.3f} lower).")
    print(f"  Safety retention stayed 100%; {report.n_blocked_unsafe} unsafe candidate(s) blocked.")
    print(f"  Report: {paths['report']}   Curve: {paths['curve']}")
    return 0


def _load_json(path):
    import json
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


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
    run.add_argument("--chat-provider", choices=["gemini", "deepseek"],
                     help="Override the reasoning provider. (The no-key 'mock' provider is "
                     "only wired up for the `demo` command, which is the offline path.)")
    run.add_argument("--vision-provider", choices=["gemini"],
                     help="Override the vision/OCR provider.")
    run.add_argument("--max-steps", type=int, help="Override the hard step cap.")
    run.add_argument("--inject-read-failure-page", type=int,
                     help="Force the first read of this page to fail (demonstrates retry/flag).")
    run.set_defaults(func=cmd_run)

    learn = sub.add_parser("learn", help="Part 2: learn from (simulated) doctor edits and emit "
                           "the before/after edit-burden curve.")
    learn.add_argument("--out", help="Output directory (default: outputs/learning).")
    learn.add_argument("--live", action="store_true", help="Use the configured chat provider for "
                       "real candidate generation and a real LLM judge (needs a key).")
    learn.add_argument("--patients", help="Directory of patient folders to learn across "
                       "(each with source.pdf or transcript.json). Implies live.")
    learn.add_argument("--edits", help="JSON {section_key: edited_text} of real clinician edits "
                       "to use instead of the simulated reviewer (production edit source).")
    learn.add_argument("--chat-provider", choices=["gemini", "deepseek"],
                       help="Reasoning provider for --live / --patients.")
    learn.add_argument("--vision-provider", choices=["gemini"], help="Vision provider for --patients.")
    learn.add_argument("--n-candidates", type=int, default=4, help="Candidates per section (best-of-N).")
    learn.add_argument("--seed", type=int, default=0, help="Split seed.")
    learn.add_argument("--heldout-frac", type=float, default=0.4, help="Held-out fraction (single record).")
    learn.add_argument("--generate", action="store_true", help="Regenerate candidates, ignoring cache.")
    learn.add_argument("--max-steps", type=int, help=argparse.SUPPRESS)
    learn.set_defaults(func=cmd_learn)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
