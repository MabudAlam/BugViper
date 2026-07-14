"""Coverage tracking and completion gating middleware.

`CoverageMiddleware` and `ForceFinalizeMiddleware` are both disabled:
- `CoverageMiddleware` removed (no callers).
- `ForceFinalizeMiddleware` retained as a no-op stub so the existing
  `from ncodereview.coverage import ForceFinalizeMiddleware` in
  `subagents.py` still imports cleanly. It does nothing.

The active gating logic now lives in `ncodereview.model_call_limit`
via `ModelCallLimitMiddleware(exit_behavior="report")`.
"""
from __future__ import annotations

import logging

from langchain.agents.middleware import AgentMiddleware

logger = logging.getLogger(__name__)


# ─── ForceFinalizeMiddleware (DISABLED) ───────────────────────────────────────
# Commented out — superseded by ModelCallLimitMiddleware(exit_behavior="report")
# in ncodereview.model_call_limit. Kept as a no-op stub so the symbol still
# imports for the legacy subagent path in ncodereview.subagents.


class ForceFinalizeMiddleware(AgentMiddleware):  # pragma: no cover - disabled
    """DISABLED no-op. Use ModelCallLimitMiddleware(exit_behavior='report')."""

    def __init__(self, max_steps: int = 20):
        super().__init__()
        self._max_steps = max_steps

    async def aafter_model(self, state, runtime):
        return None

    async def abefore_model(self, state, runtime):
        return None


__all__ = ["ForceFinalizeMiddleware"]
