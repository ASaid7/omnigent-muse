"""Run ``muse serve`` inside the Omnigent OS-environment sandbox.

The policy is resolved once from the agent's :class:`OSEnvSpec` and reused
for every spawn, so a respawned transport runs under exactly the sandbox the
first one did. Each spawn writes a fresh exec launcher
(:func:`omnigent.sandbox.create_exec_launcher`) that re-execs itself under the
platform backend before starting Muse, which puts the whole Muse process tree
-- its shell tool and anything that launches -- inside the sandbox.

Anything that would weaken the requested sandbox fails here instead of
silently running Muse unconfined.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from omnigent.inner.datamodel import OSEnvSpec
from omnigent.sandbox import (
    SandboxPolicy,
    create_exec_launcher,
    resolve_sandbox,
    with_additional_read_roots,
    with_additional_write_files,
    with_additional_write_roots,
    with_spawn_env_allowlist,
)

# Backends whose launcher confines the target's descendants. Windows Job
# Objects are applied from the parent after spawn, which this launch path
# does not do, and Muse does not ship for Windows.
SUPPORTED_BACKENDS = frozenset({"linux_bwrap", "darwin_seatbelt"})
# The only provider that works without network access.
OFFLINE_PROVIDERS = frozenset({"echo"})
# Muse's own shell sandbox needs user namespaces, which the outer sandbox's
# seccomp profile denies; the outer sandbox is the enforcement boundary.
DISABLE_NATIVE_SANDBOX = "--disable-sandbox"
# The installer wrapper self-updates over the network and reads a dotfile the
# sandbox masks, so the sandbox runs the versioned binary it points at.
_VERSION_FILE = ".muse-version"
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+-R[0-9]+(\.[0-9]+)?$")


class MuseSandboxError(RuntimeError):
    """The requested sandbox cannot be applied to ``muse serve``."""


def resolve_muse_binary(executable: str) -> str:
    """Return the absolute path of the Muse binary behind ``executable``.

    :param executable: A command name or path, e.g. ``"muse"``.
    :raises MuseSandboxError: The executable is missing, or it is the
        installer wrapper and the binary it selects is not installed.
    """
    found = shutil.which(executable)
    if found is None:
        raise MuseSandboxError(f"Muse executable {executable!r} not found")
    path = Path(found).resolve()
    version_file = path.parent / _VERSION_FILE
    if path.name.startswith("muse-bin-") or not version_file.is_file():
        return str(path)
    version = version_file.read_text(encoding="utf-8").strip()
    binary = path.parent / f"muse-bin-{version}"
    if not _VERSION.fullmatch(version) or not os.access(binary, os.X_OK):
        raise MuseSandboxError(
            f"Muse launcher {path} selects {binary.name}, which is not an "
            "installed executable; rerun the Muse installer"
        )
    return str(binary)


def _muse_dirs(env: Mapping[str, str]) -> tuple[Path, Path]:
    """Return Muse's (config, data) directories as Muse will compute them."""
    home = Path(env.get("HOME") or Path.home())
    config_home = env.get("XDG_CONFIG_HOME") or home / ".config"
    data_home = env.get("XDG_DATA_HOME") or home / ".local" / "share"
    return Path(config_home) / "muse", Path(data_home) / "muse"


@dataclass(frozen=True)
class MuseLaunch:
    """One sandboxed ``muse serve`` invocation; ``cleanup`` after spawning."""

    argv: tuple[str, ...]
    env: dict[str, str]
    cwd: str
    policy: SandboxPolicy

    def cleanup(self) -> None:
        """Remove the launcher script. Safe once the process has started."""
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.argv[0])


class MuseSandbox:
    """A resolved sandbox that every ``muse serve`` spawn is launched through."""

    def __init__(self, policy: SandboxPolicy, workspace: Path) -> None:
        self._policy = policy
        self._workspace = workspace

    @classmethod
    def resolve(
        cls,
        os_env: OSEnvSpec | None,
        *,
        cwd: str | Path | None,
        provider: str | None,
    ) -> MuseSandbox | None:
        """Resolve ``os_env`` into a sandbox, or ``None`` when none is requested.

        :param os_env: The agent's OS environment; ``None`` or
            ``sandbox.type: none`` leaves Muse unsandboxed.
        :param cwd: Workspace Muse serves; falls back to ``os_env.cwd`` and
            then the process working directory.
        :param provider: Muse provider, checked against network isolation.
        :raises MuseSandboxError: The sandbox cannot be applied as requested.
        """
        if os_env is None:
            return None
        workspace = Path(cwd or os_env.cwd or os.getcwd()).resolve()
        try:
            policy = resolve_sandbox(os_env, workspace)
        except (OSError, ValueError, NotImplementedError) as exc:
            raise MuseSandboxError(f"cannot sandbox Muse: {exc}") from exc
        if not policy.active:
            return None
        if policy.backend_type not in SUPPORTED_BACKENDS:
            supported = ", ".join(sorted(SUPPORTED_BACKENDS))
            raise MuseSandboxError(
                f"sandbox type {policy.backend_type!r} cannot confine the Muse "
                f"process tree; use one of {supported}, or 'none'"
            )
        if not policy.allow_network and provider not in OFFLINE_PROVIDERS:
            raise MuseSandboxError(
                "os_env.sandbox.allow_network is false, but Muse provider "
                f"{provider or 'default'!r} needs network access; allow network "
                "or use the 'echo' provider"
            )
        return cls(policy, workspace)

    def launch(
        self, executable: str, args: Sequence[str], env: Mapping[str, str]
    ) -> MuseLaunch:
        """Prepare one sandboxed spawn of ``executable args``.

        :param executable: Muse command name or path, e.g. ``"muse"``.
        :param args: Arguments after the executable, e.g. ``["serve"]``.
        :param env: The already-filtered environment for the Muse process.
        :raises MuseSandboxError: The Muse binary cannot be resolved.
        :raises OSError: The data directory or launcher cannot be written.
        """
        binary = resolve_muse_binary(executable)
        spawn_env = {**env, "MUSE_NO_AUTO_UPDATE": "1"}
        config_dir, data_dir = _muse_dirs(spawn_env)
        data_dir.mkdir(parents=True, exist_ok=True)
        # Configuration (settings, approval policy) stays read-only so a
        # sandboxed shell cannot rewrite Muse's own permissions; only the
        # credential file is writable, for token refresh.
        policy = with_additional_read_roots(self._policy, [config_dir])
        policy = with_additional_write_roots(policy, [data_dir])
        auth_file = config_dir / "auth.json"
        if auth_file.is_file():
            policy = with_additional_write_files(policy, [auth_file])
        policy = with_spawn_env_allowlist(policy, list(spawn_env))
        launcher = create_exec_launcher(binary, policy, cwd=str(self._workspace))
        return MuseLaunch(
            argv=(launcher, *args, DISABLE_NATIVE_SANDBOX),
            env=spawn_env,
            cwd=str(self._workspace),
            policy=policy,
        )
