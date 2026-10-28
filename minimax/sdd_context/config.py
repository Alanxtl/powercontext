"""Runtime configuration for the SDD context module.

Every setting can be overridden with an environment variable so the module can
run from a clean environment without editing files.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parent
MINIMAX_ROOT = MODULE_ROOT.parent


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else default


@dataclass(frozen=True)
class Settings:
    """Resolved settings for one module run."""

    workspace: Path
    state_dir: Path
    evidence_dir: Path
    database_kind: str
    scope_title: str
    scope_summary: str
    scope_key: str
    minimax_api_key: str
    minimax_base_url: str
    minimax_model: str
    minimax_protocol: str
    minimax_timeout_seconds: float
    minimax_max_tokens: int
    context_max_bytes: int
    spec_limit: int
    code_limit: int
    test_limit: int
    max_rounds: int

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from the process environment."""

        workspace = _env_path("SDD_WORKSPACE", MINIMAX_ROOT / "workspace")
        state_dir = _env_path("SDD_STATE_DIR", MINIMAX_ROOT / ".state")
        evidence_dir = _env_path("SDD_EVIDENCE_DIR", MINIMAX_ROOT / "evidence")
        database_kind = os.environ.get("SDD_DATABASE", "seekdb").strip().lower()
        if database_kind not in {"seekdb", "sqlite"}:
            raise ValueError(f"SDD_DATABASE must be 'seekdb' or 'sqlite', got {database_kind!r}")
        base_url = os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.io/v1").strip().rstrip("/")
        protocol = os.environ.get("MINIMAX_PROTOCOL", "auto").strip().lower()
        if protocol not in {"auto", "anthropic", "chat-completions"}:
            raise ValueError(f"MINIMAX_PROTOCOL must be auto, anthropic or chat-completions, got {protocol!r}")
        return cls(
            workspace=workspace,
            state_dir=state_dir,
            evidence_dir=evidence_dir,
            database_kind=database_kind,
            scope_title=os.environ.get("SDD_SCOPE_TITLE", "SDD task board"),
            scope_summary=os.environ.get("SDD_SCOPE_SUMMARY", "Specification-driven development context"),
            scope_key=os.environ.get("SDD_SCOPE_KEY", "sdd-context-taskboard"),
            minimax_api_key=os.environ.get("MINIMAX_API_KEY", "").strip(),
            minimax_base_url=base_url,
            minimax_model=os.environ.get("MINIMAX_MODEL", "MiniMax-M3.1-Flash").strip(),
            minimax_protocol=protocol,
            minimax_timeout_seconds=float(os.environ.get("MINIMAX_TIMEOUT_SECONDS", "180")),
            minimax_max_tokens=int(os.environ.get("MINIMAX_MAX_TOKENS", "8192")),
            context_max_bytes=int(os.environ.get("SDD_CONTEXT_MAX_BYTES", "24000")),
            spec_limit=int(os.environ.get("SDD_SPEC_LIMIT", "8")),
            code_limit=int(os.environ.get("SDD_CODE_LIMIT", "8")),
            test_limit=int(os.environ.get("SDD_TEST_LIMIT", "8")),
            max_rounds=int(os.environ.get("SDD_MAX_ROUNDS", "3")),
        )

    @property
    def seekdb_path(self) -> Path:
        """Directory of the embedded seekdb instance."""

        return self.state_dir / "seekdb"

    @property
    def sqlite_path(self) -> Path:
        """Path of the SQLite fallback database."""

        return self.state_dir / "powercontext.db"

    def resolved_protocol(self) -> str:
        """Return the wire protocol used for MiniMax requests."""

        if self.minimax_protocol != "auto":
            return self.minimax_protocol
        host = self.minimax_base_url.lower()
        if "api.minimax.io" in host or "api.minimaxi.com" in host:
            return "chat-completions"
        if "agent.minimax.cn" in host:
            return "anthropic"
        return "anthropic" if "/messages" in self.minimax_base_url else "chat-completions"


__all__ = ["MINIMAX_ROOT", "MODULE_ROOT", "Settings"]
