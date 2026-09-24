"""Registration tests for the omnigent-muse community harness.

``test_contribution_shape`` needs only the omnigent registry types importable.
``test_registered_in_omnigent`` additionally needs the plugin's entry point
discoverable in the running environment (an editable/installed omnigent + this
package). It is skipped automatically if omnigent isn't importable.
"""

from __future__ import annotations

import importlib.util

import pytest


def test_contribution_shape():
    from omnigent.community.harness.muse.plugin import get_contribution

    c = get_contribution()
    assert "muse" in c.valid_harnesses
    assert c.harness_modules["muse"].startswith("omnigent.community.harness.")
    assert c.aliases["muse-code"] == "muse"
    assert c.harness_labels["muse"] == "Muse"
    assert c.capabilities["muse"].integration_mode.value == "cli-subprocess"


@pytest.mark.skipif(
    importlib.util.find_spec("omnigent.harness_plugins") is None,
    reason="omnigent not installed in this environment",
)
def test_registered_in_omnigent():
    import omnigent.harness_plugins as hp

    hp.reset_plugin_state_for_tests()
    assert "muse" in hp.valid_harnesses()
    assert hp.harness_aliases()["muse-code"] == "muse"
    assert (
        hp.harness_modules()["muse"]
        == "omnigent.community.harness.muse.inner.muse_harness"
    )
    assert any(r["id"] == "muse" and r["label"] == "Muse" for r in hp.harness_catalog())
    assert hp.plugin_state().load_errors == {}
