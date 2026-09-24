# omnigent-muse

A community-plugin harness that adds **Muse Code** to [Omnigent](https://github.com/omnigent-ai/omnigent).

Muse is an interactive terminal coding agent that serves the **Muse Session Protocol
(MSP)** over stdio via `muse serve`. This package registers a headless `muse` harness
through Omnigent's `omnigent.community.harness` entry point and drives Muse via its
official Python SDK ([`muse-code-sdk`](https://github.com/meta-models/muse-code-sdk)).

> **Status: Step 1 scaffold.** The harness is *discoverable* (id, alias `muse-code`,
> label, install spec, capabilities, catalog row, importable `create_app()`), but the
> executor's `run_turn` is a stub — the functional MSP round-trip lands in Step 2.

## Install (local dev)

```sh
# from this directory, with sibling ../omnigent and ../muse-code-sdk checkouts
uv pip install -e ../omnigent -e .
```

`[tool.uv.sources]` in `pyproject.toml` points the `muse-code-sdk` / `muse-code-msp`
dependencies at the local checkouts under `../muse-code-sdk/python/clients/`. Remove
those source overrides before publishing.

## Verify discovery

```sh
python -c "from omnigent.harness_plugins import valid_harnesses; assert 'muse' in valid_harnesses(); print('ok')"
python -c "from omnigent.harness_plugins import plugin_state; print(plugin_state().load_errors)"  # {}
pytest -q
```

## Requirements

- `muse` CLI on PATH (or set `OMNIGENT_MUSE_PATH`). Install: `curl -fsSL https://dev.meta.ai/install.sh | bash`.
- The `muse-code-sdk` version must serve a schema fingerprint compatible with the
  installed `muse` host, or the SDK raises `MuseHostMismatchError` at handshake.

## License

MIT.
