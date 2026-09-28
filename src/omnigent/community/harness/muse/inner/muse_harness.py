"""Muse harness app + executor.

``create_app()`` is the entry point core's runner invokes after resolving the
``muse`` harness id to this module. It returns the FastAPI app built by
``ExecutorAdapter``, which installs the elicitation/policy bridges.

``MuseExecutor.run_turn`` is still a documented stub: wiring it to the
vendored :mod:`msp_client` transport (turn submit, stream translation,
approval bridging) is the next step.
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

    The next step drives ``muse serve`` via the vendored ``msp_client``
    (``MspClient.spawn`` -> ``start_session`` -> ``send_turn`` ->
    ``TurnStream.follow``) and translates MSP events into omnigent
    ``ExecutorEvent``s, bridging tool approval to ASK elicitation.
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
            "(wiring it to the vendored MSP client is the next step)."
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
