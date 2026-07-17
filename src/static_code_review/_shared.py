from __future__ import annotations

import logging

from e2b import Sandbox
from e2b.sandbox.commands.command_handle import CommandExitException

logger = logging.getLogger(__name__)


def run_in_sandbox(sbx: Sandbox, cmd: str, timeout: int = 120) -> tuple[int, str, str]:
    try:
        result = sbx.commands.run(cmd, timeout=timeout)
        return (result.exit_code, result.stdout or "", result.stderr or "")
    except CommandExitException as exc:
        return (exc.exit_code, exc.stdout or "", exc.stderr or "")
    except Exception as exc:
        logger.error("sandbox command failed: %s — cmd=%s", exc, cmd[:200])
        return (-1, "", str(exc))


def filter_changed_files(changed_files: list[str], extensions: tuple[str, ...]) -> list[str]:
    return [f for f in changed_files if f.endswith(extensions)]
