"""Community harness contribution for Muse Code.

``get_contribution()`` is the entry point core loads during plugin discovery. It
must stay import-light: only registry / install-spec / capability types, no SDK and
no runtime modules (those import lazily inside ``create_app`` / ``build_spawn_env``).
"""

from __future__ import annotations

_HARNESS = "muse"
_MODULE = "omnigent.community.harness.muse.inner.muse_harness"


def get_contribution():
    # Import-light: registry/spec/capability types only (no SDK, no runtime).
    from omnigent.harness_capabilities import (
        AuthModel,
        EffortFamily,
        Elicitation,
        HarnessCapabilities,
        IntegrationMode,
        ModelFamily,
        Resume,
    )
    from omnigent.harness_install_spec import HarnessInstallSpec
    from omnigent.harness_plugins import HarnessContribution

    return HarnessContribution(
        name="omnigent-muse",
        valid_harnesses=frozenset({_HARNESS}),
        harness_modules={_HARNESS: _MODULE},
        aliases={"muse-code": _HARNESS},
        harness_labels={_HARNESS: "Muse"},
        model_env_keys={_HARNESS: "HARNESS_MUSE_MODEL"},
        install_specs={
            _HARNESS: HarnessInstallSpec(
                display="Muse",
                binary="muse",
                package=None,
                login_args=("login",),
                logout_args=("logout",),
                install_hint="curl -fsSL https://dev.meta.ai/install.sh | bash",
                min_version="1.3.0",
            )
        },
        harness_install_keys={_HARNESS: _HARNESS, "muse-code": _HARNESS},
        spawn_env_builders={
            _HARNESS: "omnigent.community.harness.muse.plugin:build_spawn_env"
        },
        capabilities={
            _HARNESS: HarnessCapabilities(
                IntegrationMode.CLI_SUBPROCESS,  # spawns `muse serve`
                Elicitation.JSONRPC,  # MSP structured approval requests
                Resume.NONE,  # cross-process session-id persistence is not wired yet
                EffortFamily.NONE,  # no matching effort family enum yet
                ModelFamily.MULTI,  # --provider meta / --model
                AuthModel.OWN_AUTH,  # `muse auth` / `muse login`
                subagents=False,
                interrupt=True,
                streaming=True,
            )
        },
    )


def build_spawn_env(spec, *, cwd=None) -> dict[str, str]:
    """Build the env-var dict the muse harness wrap reads at startup.

    Heavy imports (runtime/provider helpers) belong inside this function, not at
    module top. TODO(step 2): resolve the model via omnigent's ``_resolve_spec_model``
    and thread provider / reasoning-effort through. The skeleton passes model + cwd only.
    """
    env: dict[str, str] = {}
    model = getattr(getattr(spec, "executor", None), "model", None) or getattr(
        spec, "model", None
    )
    if model:
        env["HARNESS_MUSE_MODEL"] = str(model)
    if cwd is not None:
        env["HARNESS_MUSE_CWD"] = str(cwd)
    return env
