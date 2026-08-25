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

"""PowerContext Memory Provider for Hermes Agent.

This directory can be copied to the Hermes provider plugin directory. It intentionally
uses only the Python standard library for HTTP, so the provider does not add a
runtime dependency to Hermes.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import commands
from .client import PowerContextClient, PowerContextError
from .provider import PowerContextMemoryProvider

logger = logging.getLogger(__name__)


def _load_plugin_config() -> dict[str, Any]:
    """Load optional Hermes plugin config without making import-time calls."""
    try:
        from hermes_cli.config import load_config_readonly  # ty: ignore[unresolved-import]

        config = load_config_readonly()
        if isinstance(config, dict):
            plugins = config.get("plugins", {})
            if isinstance(plugins, dict) and isinstance(plugins.get("powercontext"), dict):
                return dict(plugins["powercontext"])
    except Exception:
        logger.debug("Could not load Hermes plugin config", exc_info=True)
    return {}


def register(ctx) -> None:
    """Register PowerContext with Hermes' memory provider registry and slash commands."""
    provider = PowerContextMemoryProvider(_load_plugin_config())
    ctx.register_memory_provider(provider)
    register_skill = getattr(ctx, "register_skill", None)
    skill_path = Path(__file__).parent / "skills" / "powercontext" / "SKILL.md"
    if callable(register_skill) and skill_path.is_file():
        register_skill(
            "powercontext",
            skill_path,
            "Use PowerContext memory, continuity, and review operations safely.",
        )
    register_command = getattr(ctx, "register_command", None)
    if callable(register_command):
        for name in ("pc", "powercontext"):
            register_command(
                name,
                provider.handle_slash_command,
                description="Inspect and manage PowerContext memory, handoffs, artifacts, and traces.",
                args_hint="status|search|list|changes|get|remember|revise|retire|flush|stats|handoff|experience|skill|external-skills|review|workstream|trace|call ...",
            )
    commands.register_subcommands()


__all__ = ["PowerContextClient", "PowerContextError", "PowerContextMemoryProvider", "register"]
