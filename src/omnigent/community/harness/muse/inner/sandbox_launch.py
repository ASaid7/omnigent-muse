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
import hashlib
import json
import os
import re
import shutil
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from omnigent.inner.datamodel import OSEnvSpec
from omnigent.inner.sandbox import _project_root
from omnigent.process_logging import data_dir
from omnigent.sandbox import (
    SandboxPolicy,
    create_exec_launcher,
    resolve_sandbox,
    with_additional_read_roots,
    with_additional_write_roots,
    with_spawn_env_allowlist,
)

# Backends whose launcher confines the target's descendants. Windows Job
# Objects are applied from the parent after spawn, which this launch path
# does not do, and Muse does not ship for Windows.
#
# darwin_seatbelt confines the tree too, but Muse cannot run under Omnigent's
# Seatbelt profile yet: the profile grants only stat on the ancestors of
# granted paths, while Muse opens each ancestor of its data dir (session/start
# fails with UnsafePath), and it denies ~/Library outright, which hides the
# Keychain that holds the Muse login on macOS. Both need core changes; see
# https://github.com/R7L208/omnigent-muse/issues/23.
SUPPORTED_BACKENDS = frozenset({"linux_bwrap"})
# The only provider that works without network access.
OFFLINE_PROVIDERS = frozenset({"echo"})
# Muse's own shell sandbox needs user namespaces, which the outer sandbox's
# seccomp profile denies; the outer sandbox is the enforcement boundary.
DISABLE_NATIVE_SANDBOX = "--disable-sandbox"
# The installer wrapper (~/.local/bin/muse) cannot run inside the sandbox: it
# self-updates over the network and reads its state from dotfiles next to
# itself, which the sandbox masks. So the sandbox runs the versioned binary
# the wrapper would exec and reproduces the two things the wrapper hands it:
# MUSE_NO_AUTO_UPDATE (set below) and MUSE_RELEASE_INFO.
_VERSION_FILE = ".muse-version"
_RELEASE_INFO_FILE = ".muse-release-info.json"
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+-R[0-9]+(\.[0-9]+)?$")
# Sandboxed Muse runs from a private home so nothing it writes (plugins,
# skills, memory, settings) is loaded later by the user's unsandboxed Muse.
# The login is hard-linked in, the way core bridges Codex's credential store:
# a hard link shares the inode, so a token refresh inside the sandbox reaches
# the user's real login and vice versa. The lock is shared for the same
# reason, so two refreshes cannot race each other.
_LOGIN_FILES = ("auth.json", ".auth.json.lock")
# Every XDG base directory Muse may read or write points into the private
# home, so nothing it caches or records lands in the user's own directories.
_XDG_KINDS = ("config", "data", "state", "cache")
# Copied, so a settings change made inside the sandbox stays there.
_COPIED_FILES = ("settings.json",)
# Stripped so Muse always authenticates with the user's login.
_API_KEY_ENV = "META_API_KEY"
_RELEASE_INFO_ENV = "MUSE_RELEASE_INFO"
# The launcher imports omnigent inside the sandbox from the directory core
# puts on its sys.path. For an editable install that is a checkout outside
# the venv, which the sandbox would otherwise hide. Core's own (private)
# helper keeps the grant and the launcher's sys.path in step.
_OMNIGENT_IMPORT_ROOT = _project_root()


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


def release_info(binary: str) -> str | None:
    """Return the wrapper's release info for ``binary``, as the wrapper would.

    The wrapper exports ``.muse-release-info.json`` as ``MUSE_RELEASE_INFO``
    only when it describes the active version; otherwise it unsets it.

    :param binary: Path of the resolved ``muse-bin-<version>`` binary.
    :returns: The file's JSON text, or ``None`` when it is missing, malformed,
        or describes another version.
    """
    directory = Path(binary).parent
    try:
        version = (directory / _VERSION_FILE).read_text(encoding="utf-8").strip()
        text = (directory / _RELEASE_INFO_FILE).read_text(encoding="utf-8")
        info = json.loads(text)
    except (OSError, ValueError):
        return None
    if not isinstance(info, dict) or info.get("version") != version:
        return None
    if Path(binary).name != f"muse-bin-{version}":
        return None
    return text


def _user_config_dir(env: Mapping[str, str]) -> Path:
    """Return the user's Muse config directory as Muse computes it."""
    home = Path(env.get("HOME") or Path.home())
    return Path(env.get("XDG_CONFIG_HOME") or home / ".config") / "muse"


def private_home(workspace: Path) -> Path:
    """Return the private Muse home used for sandboxed runs in ``workspace``.

    :param workspace: The resolved sandbox workspace, e.g. ``/repo``.
    :returns: A path such as ``~/.omnigent/muse-sandbox/3f2a9c0d1e4b5a6f``.
    """
    digest = hashlib.sha256(str(workspace).encode()).hexdigest()[:16]
    return data_dir() / "muse-sandbox" / digest


def _bridge_user_config(source: Path, target: Path) -> None:
    """Link the user's login into ``target`` and copy their settings.

    Every file is staged under a unique name and renamed into place, so
    launches racing in one workspace, and a Muse already running there, never
    see a missing or half-written file.
    """
    for name in (*_LOGIN_FILES, *_COPIED_FILES):
        src, dst = source / name, target / name
        linked = name in _LOGIN_FILES and src.is_file() and dst.is_file()
        if linked and os.path.samefile(src, dst):
            continue
        if not src.is_file():
            with contextlib.suppress(FileNotFoundError):
                dst.unlink()
            continue
        staged = target / f".{name}.omnigent-{uuid.uuid4().hex}"
        try:
            if name in _COPIED_FILES:
                shutil.copyfile(src, staged)
            else:
                try:
                    os.link(src, staged)
                except OSError as exc:
                    raise MuseSandboxError(
                        f"cannot link the Muse login {src} into {target}: {exc}; "
                        "the Omnigent data directory must be on the same filesystem"
                    ) from exc
            os.replace(staged, dst)
        finally:
            with contextlib.suppress(FileNotFoundError):
                staged.unlink()


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

    @property
    def workspace(self) -> Path:
        """The resolved directory every sandboxed ``muse serve`` runs in."""
        return self._workspace

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
        spec = os_env.sandbox
        if spec is not None and (spec.egress_rules or spec.credential_proxy):
            # Nothing on the exec-launcher path starts Omnigent's egress
            # proxy, so these would leave the network unrestricted.
            raise MuseSandboxError(
                "os_env.sandbox.egress_rules and credential_proxy are not "
                "supported for Muse: nothing starts the egress proxy, so the "
                "network would be unrestricted; remove them, or set "
                "allow_network: false with the 'echo' provider"
            )
        if policy.backend_type == "darwin_seatbelt":
            raise MuseSandboxError(
                "Muse cannot run under the darwin_seatbelt sandbox yet; use "
                "sandbox type 'none' on macOS"
            )
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
        :raises MuseSandboxError: The Muse binary cannot be resolved, or the
            login cannot be linked into the private home.
        :raises OSError: The private home or launcher cannot be written.
        """
        binary = resolve_muse_binary(executable)
        home = private_home(self._workspace)
        xdg = {f"XDG_{kind.upper()}_HOME": home / kind for kind in _XDG_KINDS}
        for directory in xdg.values():
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        for kind in ("XDG_CONFIG_HOME", "XDG_DATA_HOME"):
            (xdg[kind] / "muse").mkdir(mode=0o700, exist_ok=True)
        _bridge_user_config(_user_config_dir(env), xdg["XDG_CONFIG_HOME"] / "muse")
        spawn_env = {
            **env,
            "MUSE_NO_AUTO_UPDATE": "1",
            **{name: str(path) for name, path in xdg.items()},
        }
        spawn_env.pop(_API_KEY_ENV, None)
        info = release_info(binary)
        if info is None:
            spawn_env.pop(_RELEASE_INFO_ENV, None)
        else:
            spawn_env[_RELEASE_INFO_ENV] = info
        policy = with_additional_read_roots(self._policy, [_OMNIGENT_IMPORT_ROOT])
        # Grant the home itself: the backends mask dotfiles only at the top
        # level of a granted root, so Muse's lock files below stay visible.
        policy = with_additional_write_roots(policy, [home])
        policy = with_spawn_env_allowlist(policy, list(spawn_env))
        launcher = create_exec_launcher(binary, policy, cwd=str(self._workspace))
        return MuseLaunch(
            argv=(launcher, *args, DISABLE_NATIVE_SANDBOX),
            env=spawn_env,
            cwd=str(self._workspace),
            policy=policy,
        )
