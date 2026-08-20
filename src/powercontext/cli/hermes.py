# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Install and diagnose the Hermes PowerContext memory provider."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from shutil import which

from powercontext.cli.system import Diagnostic, DiagnosticStatus, SetupError
from powercontext.paths import powercontext_data_dir

HERMES_HOME_ENV = "HERMES_HOME"
HERMES_PLUGIN_RELATIVE = Path("integrations") / "hermes" / "plugins" / "powercontext"
HERMES_PLUGIN_NAME = "powercontext"


@dataclass(frozen=True, slots=True)
class HermesSetupResult:
    plugin: str
    plugin_path: str
    hermes_home: str
    data_dir: str


def install_hermes_plugin(*, source: str, ref: str) -> HermesSetupResult:
    """Install the PowerContext provider into Hermes' user plugin directory."""

    hermes_executable()
    data_dir = powercontext_data_dir()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise SetupError.data_directory(data_dir, error) from error

    plugin_dir = resolve_hermes_plugin_dir(source=source, ref=ref)
    home = hermes_home()
    target = home / "plugins" / HERMES_PLUGIN_NAME
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(plugin_dir, target, dirs_exist_ok=True)
    except OSError as error:
        raise SetupError.hermes_plugin_write(target, error) from error

    return HermesSetupResult(
        plugin=HERMES_PLUGIN_NAME,
        plugin_path=str(target),
        hermes_home=str(home),
        data_dir=str(data_dir),
    )


def resolve_hermes_plugin_dir(*, source: str, ref: str) -> Path:
    """Return the Hermes provider directory from a local or remote checkout."""

    if _is_local_source(source):
        return plugin_dir_from_checkout(Path(source).expanduser().resolve())
    return plugin_dir_from_checkout(_materialize_remote_checkout(source, ref))


def plugin_dir_from_checkout(root: Path) -> Path:
    """Accept either the provider directory or a PowerContext repository root."""

    if _is_hermes_plugin(root):
        return root
    plugin = root / HERMES_PLUGIN_RELATIVE
    if _is_hermes_plugin(plugin):
        return plugin
    raise SetupError.missing_hermes_plugin(root)


def run_hermes_diagnostics() -> dict[str, Diagnostic]:
    """Collect diagnostics for the optional Hermes integration."""

    try:
        executable = hermes_executable()
    except SetupError:
        return {
            "hermes": Diagnostic(
                status=DiagnosticStatus.FAILED,
                detail="Hermes CLI is not installed or is not on PATH",
            ),
            "plugin": Diagnostic(
                status=DiagnosticStatus.SKIPPED,
                detail="not checked because Hermes CLI is unavailable",
            ),
        }

    plugin = hermes_home() / "plugins" / HERMES_PLUGIN_NAME
    installed = _is_hermes_plugin(plugin)
    return {
        "hermes": Diagnostic(status=DiagnosticStatus.OK, detail=executable),
        "plugin": Diagnostic(
            status=DiagnosticStatus.OK if installed else DiagnosticStatus.FAILED,
            detail=(
                f"{HERMES_PLUGIN_NAME} is installed" if installed else "PowerContext Hermes plugin is not installed"
            ),
        ),
    }


def hermes_home() -> Path:
    """Return Hermes' user home, honoring the host's environment override."""

    configured = os.environ.get(HERMES_HOME_ENV, "").strip()
    return Path(configured).expanduser().resolve() if configured else (Path.home() / ".hermes").resolve()


def hermes_executable() -> str:
    """Return a subprocess-launchable Hermes CLI path."""

    executable = which("hermes")
    if executable is None:
        raise SetupError.hermes_unavailable()
    return executable


def checkout_target(ref: str) -> Path:
    """Resolve a Git ref to a directory under PowerContext's Hermes cache."""

    root = (powercontext_data_dir() / "checkouts" / "hermes").resolve()
    if not ref or ref in {".", ".."} or "\x00" in ref:
        raise SetupError.invalid_hermes_ref(ref)
    target = (root / ref).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise SetupError.invalid_hermes_ref(ref) from error
    if target == root:
        raise SetupError.invalid_hermes_ref(ref)
    return target


def github_clone_url(source: str) -> str:
    """Accept a GitHub slug or repository URL and return a clone URL."""

    text = source.strip()
    if text.startswith(("https://github.com/", "http://github.com/", "git@github.com:")):
        return text if text.endswith(".git") else f"{text}.git"
    if "://" in text or text.startswith("git@"):
        raise SetupError.invalid_hermes_source(source)
    if "/" in text and not text.startswith("."):
        return f"https://github.com/{text}.git"
    raise SetupError.invalid_hermes_source(source)


def _is_local_source(source: str) -> bool:
    candidate = Path(source).expanduser()
    return source.startswith((".", "/", "~")) or (len(source) >= 2 and source[1] == ":") or candidate.exists()


def _is_hermes_plugin(path: Path) -> bool:
    return (path / "__init__.py").is_file() and (path / "plugin.yaml").is_file()


def _usable_checkout(target: Path) -> bool:
    return _is_hermes_plugin(target) or _is_hermes_plugin(target / HERMES_PLUGIN_RELATIVE)


def _materialize_remote_checkout(source: str, ref: str) -> Path:
    target = checkout_target(ref)
    if _usable_checkout(target):
        return target
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    _clone_github_source(source, ref, target)
    return target


def _clone_github_source(source: str, ref: str, target: Path) -> None:
    command = ["git", "clone", "--depth", "1", "--branch", ref, github_clone_url(source), str(target)]
    try:
        completed = subprocess.run(  # noqa: S603 - arguments are passed directly to git.
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SetupError.command_unavailable(command, error) from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        raise SetupError.command_failed(command, detail)


__all__ = [
    "HERMES_PLUGIN_NAME",
    "HermesSetupResult",
    "checkout_target",
    "github_clone_url",
    "hermes_executable",
    "hermes_home",
    "install_hermes_plugin",
    "plugin_dir_from_checkout",
    "resolve_hermes_plugin_dir",
    "run_hermes_diagnostics",
]
