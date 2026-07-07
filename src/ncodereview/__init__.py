"""Sandboxed DeepAgent code review.

Webhook entry → e2b sandbox → DeepAgent orchestrator + specialized subagents
→ host-side GitHub tools → kill sandbox.
"""

from ncodereview.config import config
from ncodereview.pipeline import run_review_pipeline

__all__ = ["config", "run_review_pipeline"]
