"""Discharge Summary Agent.

An agentic system that reads a patient's messy, scanned source notes and drafts a
structured discharge summary *for clinician review*. Its defining property is that
it never invents a clinical fact: anything it cannot source from the documents is
marked MISSING / PENDING / CONFLICT and flagged, never filled with a plausible value.

See README.md for the design and the per-module docstrings for details.
"""

__version__ = "1.0.0"
