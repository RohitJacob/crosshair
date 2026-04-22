"""Safepoint detection and handoff summary generation."""

from crosshair.safepoint.detector import SafepointDecision, evaluate
from crosshair.safepoint.handoff import build_handoff_summary

__all__ = ["SafepointDecision", "evaluate", "build_handoff_summary"]
