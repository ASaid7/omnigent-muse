# MSP transport review results

All seven comments on [PR #1](https://github.com/R7L208/omnigent-muse/pull/1) are implemented locally on `feat/msp-transport`, starting from the reviewed head `6afec8ed16687aed5d3b262b783ef876e892818f`. The fixes are committed individually. No commits or review replies have been pushed or posted.

## Comment responses ready for review

| Reviewer comment | Implemented response | Passing regression / evidence | Commit |
| --- | --- | --- | --- |
| [Environment-copy scan](https://github.com/R7L208/omnigent-muse/pull/1#issuecomment-5894529243) | Changed the test environment copy to `os.environ.copy()`. | The exfiltration scanner passes on the full PR diff. | `ecb8a6e` |
| [Pending request leak](https://github.com/R7L208/omnigent-muse/pull/1#discussion_r4136516534) | `request()` releases its pending entry in `finally`; initialization uses the same path. `_finish()` now clears the map before failing remaining futures. Late responses are ignored. | `test_request_timeout_clears_pending`, `test_request_cancellation_clears_pending`, `test_serialization_failure_clears_pending`, `test_late_response_after_timeout_is_ignored`, `test_eof_clears_pending`. | `7314e3f` |
| [Writer cancellation](https://github.com/R7L208/omnigent-muse/pull/1#issuecomment-5898461255) | Writer teardown delivers `MspConnectionClosed` to request callers. Queue accounting is balanced and public closure marks the transport closed before waiting for the child. Caller cancellation still propagates normally. | `test_pending_request_reports_closed_when_writer_cancelled` covers direct writer cancellation and public closure, including another queued request and `flush()`. | `4913632` |
| [Host request handler ownership](https://github.com/R7L208/omnigent-muse/pull/1#discussion_r4136567914) | Track handlers, remove completed tasks, cancel them on closure, and join them during teardown. Prevent new handlers after closure. Exclude the handler calling `close()` from cancellation and joining; avoid cancelling an already cancelling handler again. | `test_close_joins_server_request_handlers`, `test_server_request_handler_response_and_self_eviction`, `test_server_request_handler_can_close_client`, `test_close_awaits_async_handler_cleanup`; existing default-response coverage retained. | `8bbbc04`, `7b4c9d5` |
| [Stream helper task cleanup](https://github.com/R7L208/omnigent-muse/pull/1#discussion_r4136592452) | A `finally` boundary cancels and joins both race helpers before translating or yielding an event. | `test_follow_joins_helpers_on_every_exit` covers consumer cancellation, iterator closure after yielding, normal completion, EOF, and simultaneous readiness. | `67689b6` |
| [Retry flag precedence](https://github.com/R7L208/omnigent-muse/pull/1#discussion_r4136583404) | Explicit `retryable=False` returns immediately. Retryable error kinds are used only when a boolean flag is absent. Retries retain the command ID and attempt limit. | `test_command_retry_flag_is_authoritative`, `test_command_retry_exhaustion_preserves_command_id`; existing real-pipe backpressure test retained. | `51f84cb` |
| [Turn event scoping](https://github.com/R7L208/omnigent-muse/pull/1#discussion_r4136537748) | Require matching session and turn IDs for every translated event. Correlate deltas without wire `turnId` through `item/started`; evict completed items and drop unknown-item deltas. Copy internally annotated payloads so other raw subscribers see unchanged frames. Document attaching before submission. | `test_translation_checks_event_scope`, `test_follow_correlates_items_and_rejects_other_turns`, `test_follow_drops_unassociated_item_deltas`, plus the protocol-shaped happy-path fake host. | `71d23c6` |

For the scoping thread: the [pinned official schema](https://github.com/meta-models/muse-code-sdk/blob/bb44be3d36de46d2411bd9eaa4aee99006092546/schema/msp/stable/msp.schema.json) and [official text transcript](https://github.com/meta-models/muse-code-sdk/blob/bb44be3d36de46d2411bd9eaa4aee99006092546/schema/msp/transcripts/text-run-single-turn/transcript.ndjson) put the owning turn on the started item, while deltas identify that item. Requiring a new top-level wire field would discard legitimate deltas; correlation supplies the internal turn ID before applying the common scope gate.

## Verification

Validated on September 30, 2026, against production/test head `7b4c9d5`:

| Check | Result |
| --- | --- |
| `PYTHONASYNCIODEBUG=1 uv run --no-sync pytest -q` (Python 3.13.9) | 85 passed; no unhandled-task or pending-task warnings. |
| `PYTHONASYNCIODEBUG=1 .scratch/venv-py312/bin/python -m pytest -q` (Python 3.12.12) | 85 passed; no unhandled-task or pending-task warnings. |
| Ruff check / format check | All checks passed; 14 files already formatted. |
| Pyrefly | Zero errors. |
| Exfiltration / secret scanners | Both passed against the full diff from PR base `58dcae5d3c55a8e43ae0aa1a36de6ad0ea02a710` to local HEAD. Exfiltration scan emits only the existing informational note for `pyproject.toml`. |
| Workflow action pinning / shell syntax | Passed. |
| Wheel / source distribution build | Both built successfully. |
| Twine distribution checks | Both passed. |
| Regular wheel installation on Python 3.12 and 3.13 | `muse` registers with zero plugin load errors; transport imports from the installed wheel. All eight registration tests pass on each version. Development editable installations restored afterward. |
| Independent code review | Two additional handler shutdown regressions found, reproduced, fixed, and tested. Re-review reports no remaining important findings; 21 focused tests pass with asyncio debug enabled. |
| `git diff --check` | Passed. |

The reviewed head's baseline had 34 passing tests, eight Ruff findings, and 77 Pyrefly errors. Commit `75c5385` declares factory-created instance attributes at class scope for Pyrefly and makes the focused formatting/logging corrections needed by the existing CI checks. It does not change protocol behavior.

## Live host validation and its limit

An isolated probe used Muse Code **1.4.1 (1.4.1-R4503.1)** with write and shell tools disabled, private temporary XDG directories, and an `echo` session with `allowAll` approval mode. It verified initialization, session creation, turn acceptance, delivery of the matching terminal event, and child reaping during `close()`.

Served schema fingerprint: `sha256:e0e163db6ccf00dbe68402ce55d6319b3edc33c421f31e9583b587b2de8a118f`. This was read from the live initialize response by the probe; the existing client's `schemaInfo` field lookup was not changed.

The host returned `turn/completed` with `error.kind=authRequired` and `retryable=False`, despite accepting the session as provider `echo`. It emitted no agent text deltas or token-usage notification. Successful live text/usage streaming therefore remains unverified. The fake host and the pinned official protocol transcript verify the intended item lifecycle and turn correlation. No user credentials or settings were changed, and no paid provider calls were initiated.

## Follow-up PR integration

When these changes are integrated into [PR #3](https://github.com/R7L208/omnigent-muse/pull/3), remove its non-strict xfail markers on `test_request_timeout_clears_pending` and `test_pending_request_reports_closed_when_writer_cancelled`. These are now ordinary passing regressions in this branch. Keep the follow-up executor work separate from this transport review.

GitHub CI and reviewer replies remain external handoff steps after a user-authorized push. The successful live text/usage probe is the only outstanding local validation item from the original plan.
