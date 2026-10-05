"""OS-environment sandboxing for the ``muse serve`` process tree."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from omnigent.inner.datamodel import OSEnvSandboxSpec, OSEnvSpec
from omnigent.inner.sandbox import SandboxPolicy

from omnigent.community.harness.muse.inner import sandbox_launch
from omnigent.community.harness.muse.inner.sandbox_launch import (
    MuseSandbox,
    MuseSandboxError,
    resolve_muse_binary,
)


def _policy(
    *,
    backend_type: str = "linux_bwrap",
    active: bool = True,
    allow_network: bool = True,
) -> SandboxPolicy:
    return SandboxPolicy(
        backend_type=backend_type,
        active=active,
        read_roots=None,
        write_roots=[],
        write_files=[],
        allow_network=allow_network,
    )


def _executable(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def resolved(monkeypatch: pytest.MonkeyPatch) -> list[tuple[OSEnvSpec, Path]]:
    """Stub policy resolution so tests do not depend on the host's bwrap."""
    calls: list[tuple[OSEnvSpec, Path]] = []
    policy = _policy()

    def fake_resolve(spec: OSEnvSpec, cwd: Path) -> SandboxPolicy:
        calls.append((spec, cwd))
        return policy

    monkeypatch.setattr(sandbox_launch, "resolve_sandbox", fake_resolve)
    return calls


def _bwrap_spec() -> OSEnvSpec:
    return OSEnvSpec(sandbox=OSEnvSandboxSpec(type="linux_bwrap"))


def test_no_os_env_means_no_sandbox(tmp_path: Path) -> None:
    assert MuseSandbox.resolve(None, cwd=tmp_path, provider="meta") is None


def test_explicit_none_sandbox_means_no_sandbox(tmp_path: Path) -> None:
    spec = OSEnvSpec(sandbox=OSEnvSandboxSpec(type="none"))

    assert MuseSandbox.resolve(spec, cwd=tmp_path, provider="meta") is None


def test_policy_is_resolved_once_against_the_workspace(
    tmp_path: Path, resolved: list[tuple[OSEnvSpec, Path]]
) -> None:
    spec = _bwrap_spec()

    sandbox = MuseSandbox.resolve(spec, cwd=tmp_path, provider="meta")

    assert sandbox is not None
    assert resolved == [(spec, tmp_path.resolve())]


def test_workspace_falls_back_to_os_env_cwd(
    tmp_path: Path, resolved: list[tuple[OSEnvSpec, Path]]
) -> None:
    spec = OSEnvSpec(cwd=str(tmp_path), sandbox=OSEnvSandboxSpec(type="linux_bwrap"))

    MuseSandbox.resolve(spec, cwd=None, provider="meta")

    assert resolved[0][1] == tmp_path.resolve()


@pytest.mark.parametrize("backend_type", ["windows_jobobject", "custom"])
def test_unsupported_backends_fail_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend_type: str
) -> None:
    monkeypatch.setattr(
        sandbox_launch,
        "resolve_sandbox",
        lambda spec, cwd: _policy(backend_type=backend_type),
    )

    with pytest.raises(MuseSandboxError, match=backend_type):
        MuseSandbox.resolve(_bwrap_spec(), cwd=tmp_path, provider="meta")


@pytest.mark.parametrize(
    "error", [OSError("bwrap not found"), NotImplementedError("no backend")]
)
def test_backend_resolution_failures_fail_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def fail(spec: OSEnvSpec, cwd: Path) -> SandboxPolicy:
        raise error

    monkeypatch.setattr(sandbox_launch, "resolve_sandbox", fail)

    with pytest.raises(MuseSandboxError, match=str(error)):
        MuseSandbox.resolve(_bwrap_spec(), cwd=tmp_path, provider="meta")


@pytest.mark.parametrize("provider", [None, "meta", "local"])
def test_network_isolation_rejects_providers_that_need_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str | None
) -> None:
    monkeypatch.setattr(
        sandbox_launch,
        "resolve_sandbox",
        lambda spec, cwd: _policy(allow_network=False),
    )

    with pytest.raises(MuseSandboxError, match="allow_network"):
        MuseSandbox.resolve(_bwrap_spec(), cwd=tmp_path, provider=provider)


def test_network_isolation_allows_offline_echo_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sandbox_launch,
        "resolve_sandbox",
        lambda spec, cwd: _policy(allow_network=False),
    )

    assert MuseSandbox.resolve(_bwrap_spec(), cwd=tmp_path, provider="echo")


def test_launch_wraps_real_binary_and_delegates_shell_sandbox(
    tmp_path: Path, resolved: list[tuple[OSEnvSpec, Path]]
) -> None:
    binary = _executable(tmp_path / "muse")
    home = tmp_path / "home"
    (home / ".config" / "muse").mkdir(parents=True)
    (home / ".config" / "muse" / "auth.json").write_text("{}")
    sandbox = MuseSandbox.resolve(_bwrap_spec(), cwd=tmp_path, provider="meta")
    assert sandbox is not None

    launch = sandbox.launch(
        str(binary),
        ["serve", "--provider", "meta"],
        {"HOME": str(home), "PATH": "/bin"},
    )
    try:
        launcher = Path(launch.argv[0])
        assert launcher.is_file() and os.access(launcher, os.X_OK)
        assert launch.argv[1:] == ("serve", "--provider", "meta", "--disable-sandbox")
        assert launch.cwd == str(tmp_path.resolve())
        assert launch.env == {
            "HOME": str(home),
            "PATH": "/bin",
            "MUSE_NO_AUTO_UPDATE": "1",
        }

        policy = launch.policy
        config_dir = (home / ".config" / "muse").resolve()
        data_dir = (home / ".local" / "share" / "muse").resolve()
        assert policy.read_roots == [config_dir]
        assert config_dir not in policy.write_roots
        assert data_dir in policy.write_roots and data_dir.is_dir()
        assert policy.write_files == [config_dir / "auth.json"]
        assert policy.spawn_env_allowlist == sorted(launch.env)
        assert str(binary.resolve()) in launcher.read_text()
    finally:
        launch.cleanup()
    assert not Path(launch.argv[0]).exists()
    launch.cleanup()  # idempotent


def test_launch_honours_xdg_directories_from_spawn_env(
    tmp_path: Path, resolved: list[tuple[OSEnvSpec, Path]]
) -> None:
    binary = _executable(tmp_path / "muse")
    sandbox = MuseSandbox.resolve(_bwrap_spec(), cwd=tmp_path, provider="echo")
    assert sandbox is not None
    env = {
        "HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(tmp_path / "cfg"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
    }

    launch = sandbox.launch(str(binary), ["serve"], env)
    launch.cleanup()

    assert launch.policy.read_roots == [(tmp_path / "cfg" / "muse").resolve()]
    assert (tmp_path / "data" / "muse").resolve() in launch.policy.write_roots
    # No credential file yet (e.g. META_API_KEY auth): nothing to grant.
    assert launch.policy.write_files == []


def test_each_launch_reuses_the_resolved_policy(
    tmp_path: Path, resolved: list[tuple[OSEnvSpec, Path]]
) -> None:
    binary = _executable(tmp_path / "muse")
    sandbox = MuseSandbox.resolve(_bwrap_spec(), cwd=tmp_path, provider="echo")
    assert sandbox is not None
    env = {"HOME": str(tmp_path / "home")}

    first = sandbox.launch(str(binary), ["serve"], env)
    second = sandbox.launch(str(binary), ["serve"], env)
    first.cleanup()
    second.cleanup()

    assert len(resolved) == 1
    assert first.argv[0] != second.argv[0]
    assert first.policy == second.policy


def test_resolve_binary_follows_installer_wrapper(tmp_path: Path) -> None:
    wrapper = _executable(tmp_path / "muse", "#!/usr/bin/env bash\nexec true\n")
    (tmp_path / ".muse-version").write_text("1.4.2-R4684.1\n")
    real = _executable(tmp_path / "muse-bin-1.4.2-R4684.1")

    assert resolve_muse_binary(str(wrapper)) == str(real.resolve())


def test_resolve_binary_through_path_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = _executable(tmp_path / "muse")
    monkeypatch.setenv("PATH", str(tmp_path))

    assert resolve_muse_binary("muse") == str(real.resolve())


def test_resolve_binary_rejects_wrapper_without_installed_binary(
    tmp_path: Path,
) -> None:
    wrapper = _executable(tmp_path / "muse")
    (tmp_path / ".muse-version").write_text("1.4.2-R4684.1\n")

    with pytest.raises(MuseSandboxError, match="muse-bin-1.4.2-R4684.1"):
        resolve_muse_binary(str(wrapper))


def test_resolve_binary_rejects_missing_executable(tmp_path: Path) -> None:
    with pytest.raises(MuseSandboxError, match="not found"):
        resolve_muse_binary(str(tmp_path / "missing-muse"))
