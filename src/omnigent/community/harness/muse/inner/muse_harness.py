"""Muse harness app + executor.

``create_app()`` is the entry point core's runner invokes after resolving the
``muse`` harness id to this module. It returns the FastAPI app built by
``ExecutorAdapter``, which installs the elicitation/policy bridges.

Step 1 (this file) is a registration skeleton: ``MuseExecutor.run_turn`` is a
documented stub. Step 2 wires the ``muse_code`` SDK over MSP (see the plan).
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from omnigent.harness_startup_config import resolve_harness_path
from omnigent.inner.executor import Executor
from omnigent.runtime.harnesses._executor_adapter import ExecutorAdapter

_ENV_MODEL = "HARNESS_MUSE_MODEL"
_ENV_CWD = "HARNESS_MUSE_CWD"


class MuseExecutor(Executor):
    """Skeleton executor for the Muse (MSP) harness.

    Step 2 will drive ``muse serve`` via the ``muse_code`` SDK
    (``MuseClient.spawn`` -> ``start_session`` -> ``send_user_turn`` ->
    ``turn.items()`` / ``await turn.completed``) and translate MSP items/deltas into
    omnigent ``ExecutorEvent``s, bridging tool approval to ASK elicitation.
    """

    def __init__(self, *, muse_bin: str, model: str | None, cwd: str | None) -> None:
        self._muse_bin = muse_bin
        self._model = model
        self._cwd = cwd

    def supports_streaming(self) -> bool:
        return True

    async def run_turn(self, messages, tools, system_prompt, config=None):
        raise NotImplementedError(
            "omnigent-muse scaffold: MuseExecutor.run_turn is not implemented yet "
            "(Step 2 wires the muse_code SDK over MSP)."
        )
        yield  # unreachable — keeps this coroutine an async generator


def _build_muse_executor() -> Executor:
    return MuseExecutor(
        muse_bin=resolve_harness_path("muse") or "muse",
        model=os.environ.get(_ENV_MODEL) or None,
        cwd=os.environ.get(_ENV_CWD)
        or os.environ.get("OMNIGENT_RUNNER_WORKSPACE")
        or None,
    )


def create_app() -> FastAPI:
    return ExecutorAdapter(
        executor_factory=_build_muse_executor, harness_label="Muse"
    ).build()
