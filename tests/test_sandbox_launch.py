"""OS-environment sandboxing for the ``muse serve`` process tree."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from omnigent.inner.datamodel import CredentialProxySpec, OSEnvSandboxSpec, OSEnvSpec
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
    "sandbox_spec",
    [
        OSEnvSandboxSpec(type="linux_bwrap", egress_rules=["GET example.com/**"]),
        OSEnvSandboxSpec(
            type="linux_bwrap",
            credential_proxy=CredentialProxySpec(entries=[]),
            egress_rules=[],
        ),
    ],
)
def test_egress_and_credential_proxy_are_rejected(
    tmp_path: Path,
    resolved: list[tuple[OSEnvSpec, Path]],
    sandbox_spec: OSEnvSandboxSpec,
) -> None:
    spec = OSEnvSpec(sandbox=sandbox_spec)

    with pytest.raises(MuseSandboxError, match="egress proxy"):
        MuseSandbox.resolve(spec, cwd=tmp_path, provider="meta")


def test_seatbelt_is_rejected_until_core_supports_muse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sandbox_launch,
        "resolve_sandbox",
        lambda spec, cwd: _policy(backend_type="darwin_seatbelt"),
    )

    with pytest.raises(MuseSandboxError, match="darwin_seatbelt"):
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


@pytest.fixture
def omnigent_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "omnigent"
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(data))
    return data


def _user_config(home: Path) -> Path:
    config = home / ".config" / "muse"
    config.mkdir(parents=True)
    (config / "auth.json").write_text('{"providers": {}}')
    (config / ".auth.json.lock").write_text("")
    (config / "settings.json").write_text('{"model": "user"}')
    (config / "trust.json").write_text("{}")
    return config


def test_launch_wraps_real_binary_and_delegates_shell_sandbox(
    tmp_path: Path, resolved: list[tuple[OSEnvSpec, Path]], omnigent_data: Path
) -> None:
    binary = _executable(tmp_path / "muse")
    home = tmp_path / "home"
    sandbox = MuseSandbox.resolve(_bwrap_spec(), cwd=tmp_path, provider="meta")
    assert sandbox is not None

    launch = sandbox.launch(
        str(binary),
        ["serve", "--provider", "meta"],
        {"HOME": str(home), "PATH": "/bin", "META_API_KEY": "sk-test"},
    )
    try:
        launcher = Path(launch.argv[0])
        assert launcher.is_file() and os.access(launcher, os.X_OK)
        assert launch.argv[1:] == ("serve", "--provider", "meta", "--disable-sandbox")
        assert launch.cwd == str(tmp_path.resolve())
        private = sandbox_launch.private_home(tmp_path.resolve())
        assert private.is_relative_to(omnigent_data)
        assert launch.env == {
            "HOME": str(home),
            "PATH": "/bin",
            "MUSE_NO_AUTO_UPDATE": "1",
            "XDG_CONFIG_HOME": str(private / "config"),
            "XDG_DATA_HOME": str(private / "data"),
        }

        policy = launch.policy
        assert policy.read_roots == [sandbox_launch._OMNIGENT_IMPORT_ROOT]
        assert private.resolve() in policy.write_roots
        assert (private / "data" / "muse").is_dir()
        assert policy.write_files == []
        assert policy.spawn_env_allowlist == sorted(launch.env)
        assert str(binary.resolve()) in launcher.read_text()
    finally:
        launch.cleanup()
    assert not Path(launch.argv[0]).exists()
    launch.cleanup()  # idempotent


def test_launch_links_login_and_copies_settings(
    tmp_path: Path, resolved: list[tuple[OSEnvSpec, Path]], omnigent_data: Path
) -> None:
    binary = _executable(tmp_path / "muse")
    home = tmp_path / "home"
    user = _user_config(home)
    sandbox = MuseSandbox.resolve(_bwrap_spec(), cwd=tmp_path, provider="meta")
    assert sandbox is not None

    sandbox.launch(str(binary), ["serve"], {"HOME": str(home)}).cleanup()

    private = sandbox_launch.private_home(tmp_path.resolve()) / "config" / "muse"
    # A refresh written in either place is the same file.
    assert os.path.samefile(private / "auth.json", user / "auth.json")
    assert os.path.samefile(private / ".auth.json.lock", user / ".auth.json.lock")
    # Settings are a private copy; trust decisions are not inherited.
    assert (private / "settings.json").read_text() == '{"model": "user"}'
    assert not os.path.samefile(private / "settings.json", user / "settings.json")
    assert not (private / "trust.json").exists()


def test_launch_resyncs_user_config_on_every_spawn(
    tmp_path: Path, resolved: list[tuple[OSEnvSpec, Path]], omnigent_data: Path
) -> None:
    binary = _executable(tmp_path / "muse")
    home = tmp_path / "home"
    user = _user_config(home)
    sandbox = MuseSandbox.resolve(_bwrap_spec(), cwd=tmp_path, provider="meta")
    assert sandbox is not None
    env = {"HOME": str(home)}
    sandbox.launch(str(binary), ["serve"], env).cleanup()
    private = sandbox_launch.private_home(tmp_path.resolve()) / "config" / "muse"

    # Logging in again replaces the file rather than rewriting it.
    (user / "auth.json").unlink()
    (user / "auth.json").write_text('{"providers": {"meta": {}}}')
    (user / "settings.json").write_text('{"model": "changed"}')
    sandbox.launch(str(binary), ["serve"], env).cleanup()
    assert os.path.samefile(private / "auth.json", user / "auth.json")
    assert (private / "settings.json").read_text() == '{"model": "changed"}'

    # The user logs out: the sandbox loses the login too.
    (user / "auth.json").unlink()
    sandbox.launch(str(binary), ["serve"], env).cleanup()
    assert not (private / "auth.json").exists()


def test_launch_honours_user_xdg_config_home(
    tmp_path: Path, resolved: list[tuple[OSEnvSpec, Path]], omnigent_data: Path
) -> None:
    binary = _executable(tmp_path / "muse")
    user = tmp_path / "cfg" / "muse"
    user.mkdir(parents=True)
    (user / "auth.json").write_text("{}")
    sandbox = MuseSandbox.resolve(_bwrap_spec(), cwd=tmp_path, provider="echo")
    assert sandbox is not None
    env = {"HOME": str(tmp_path / "home"), "XDG_CONFIG_HOME": str(tmp_path / "cfg")}

    launch = sandbox.launch(str(binary), ["serve"], env)
    launch.cleanup()

    private = sandbox_launch.private_home(tmp_path.resolve())
    assert launch.env["XDG_CONFIG_HOME"] == str(private / "config")
    assert os.path.samefile(
        private / "config" / "muse" / "auth.json", user / "auth.json"
    )


def test_workspaces_get_separate_private_homes(
    tmp_path: Path, omnigent_data: Path
) -> None:
    first = sandbox_launch.private_home(tmp_path / "a")
    second = sandbox_launch.private_home(tmp_path / "b")

    assert first != second
    assert first == sandbox_launch.private_home(tmp_path / "a")


def test_login_link_failure_is_explicit(
    tmp_path: Path,
    resolved: list[tuple[OSEnvSpec, Path]],
    omnigent_data: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = _executable(tmp_path / "muse")
    home = tmp_path / "home"
    _user_config(home)
    sandbox = MuseSandbox.resolve(_bwrap_spec(), cwd=tmp_path, provider="meta")
    assert sandbox is not None

    def cross_device(src: object, dst: object) -> None:
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(sandbox_launch.os, "link", cross_device)

    with pytest.raises(MuseSandboxError, match="same filesystem"):
        sandbox.launch(str(binary), ["serve"], {"HOME": str(home)})


def test_each_launch_reuses_the_resolved_policy(
    tmp_path: Path, resolved: list[tuple[OSEnvSpec, Path]], omnigent_data: Path
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


def _installed(directory: Path, version: str, info: object) -> Path:
    directory.mkdir(exist_ok=True)
    _executable(directory / "muse")
    (directory / ".muse-version").write_text(f"{version}\n")
    (directory / ".muse-release-info.json").write_text(json.dumps(info))
    return _executable(directory / f"muse-bin-{version}")


def test_launch_exports_release_info_like_the_wrapper(
    tmp_path: Path, resolved: list[tuple[OSEnvSpec, Path]], omnigent_data: Path
) -> None:
    info = {"channel": "muse-stable", "version": "1.4.3-R5018.1"}
    _installed(tmp_path / "bin", "1.4.3-R5018.1", info)
    sandbox = MuseSandbox.resolve(_bwrap_spec(), cwd=tmp_path, provider="echo")
    assert sandbox is not None

    launch = sandbox.launch(
        str(tmp_path / "bin" / "muse"),
        ["serve"],
        {"HOME": str(tmp_path / "home"), "MUSE_RELEASE_INFO": "stale"},
    )
    launch.cleanup()

    assert json.loads(launch.env["MUSE_RELEASE_INFO"]) == info


@pytest.mark.parametrize("info", [{"version": "1.4.2-R4684.1"}, ["not", "a", "dict"]])
def test_release_info_for_another_version_is_dropped(
    tmp_path: Path, info: object
) -> None:
    binary = _installed(tmp_path, "1.4.3-R5018.1", info)

    assert sandbox_launch.release_info(str(binary)) is None


def test_launch_unsets_release_info_when_missing(
    tmp_path: Path, resolved: list[tuple[OSEnvSpec, Path]], omnigent_data: Path
) -> None:
    binary = _executable(tmp_path / "muse")
    sandbox = MuseSandbox.resolve(_bwrap_spec(), cwd=tmp_path, provider="echo")
    assert sandbox is not None

    launch = sandbox.launch(
        str(binary), ["serve"], {"HOME": str(tmp_path), "MUSE_RELEASE_INFO": "stale"}
    )
    launch.cleanup()

    assert "MUSE_RELEASE_INFO" not in launch.env


def test_resolve_binary_rejects_missing_executable(tmp_path: Path) -> None:
    with pytest.raises(MuseSandboxError, match="not found"):
        resolve_muse_binary(str(tmp_path / "missing-muse"))
