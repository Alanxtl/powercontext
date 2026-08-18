from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

HERMES_ROOT = Path(__file__).parents[2] / "integrations" / "hermes"
if str(HERMES_ROOT) not in sys.path:
    sys.path.insert(0, str(HERMES_ROOT))

from plugins.powercontext import PowerContextMemoryProvider  # noqa: E402  # ty: ignore[unresolved-import]
from plugins.powercontext.cli import register_cli  # noqa: E402  # ty: ignore[unresolved-import]


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def prepare_context(self, scope_id, query, *, max_bytes):
        self.calls.append(("prepare_context", (scope_id, query), {"max_bytes": max_bytes}))
        return {"status": "ready", "content": "remembered project context"}

    def capture_content(self, scope_id, *, source_id, content, metadata):
        self.calls.append(("capture_content", (scope_id, source_id, content), {"metadata": metadata}))
        return {}

    def flush_memory(self, scope_id):
        self.calls.append(("flush_memory", (scope_id,), {}))
        return {}

    def search_memory(self, scope_id, query, *, limit, mode):
        self.calls.append(("search_memory", (scope_id, query), {"limit": limit, "mode": mode}))
        return {"hits": [{"text": "a memory"}]}

    def get_memory_entry(self, scope_id, citation):
        self.calls.append(("get_memory_entry", (scope_id, citation), {}))
        return {"text": "a memory"}

    def remember_memory(self, scope_id, *, kind, text, reason=None):
        self.calls.append(("remember_memory", (scope_id, kind, text), {"reason": reason}))
        return {"status": "remembered"}

    def retire_memory_entry(self, scope_id, citation, *, reason=None):
        self.calls.append(("retire_memory_entry", (scope_id, citation), {"reason": reason}))
        return {"status": "retired"}


@pytest.fixture
def provider_and_client():
    client = FakeClient()
    provider = PowerContextMemoryProvider(
        {"scope_id": "hermes:{profile}:{user_id}"},
        client_factory=lambda _config: client,
    )
    provider.initialize("session-1", hermes_home="C:/profiles/work", agent_identity="coder", user_id="user-7")
    yield provider, client
    provider.shutdown()


def test_prefetch_uses_profile_and_user_scoped_context(provider_and_client):
    provider, client = provider_and_client

    recalled = provider.prefetch("What did we decide about the deployment?")

    assert "remembered project context" in recalled
    assert client.calls[0] == (
        "prepare_context",
        ("hermes:coder:user-7", "What did we decide about the deployment?"),
        {"max_bytes": 8000},
    )


def test_sync_turn_is_flushed_before_session_end(provider_and_client):
    provider, client = provider_and_client

    provider.sync_turn("Use uv for the integration.", "I will add a uv check.", session_id="session-1")
    provider.on_session_end([])

    names = [call[0] for call in client.calls]
    assert names == ["capture_content", "flush_memory"]
    assert client.calls[0][1][0] == "hermes:coder:user-7"
    assert client.calls[0][2]["metadata"]["kind"] == "hermes-turn"


def test_pre_compress_persists_context_before_compression(provider_and_client):
    provider, client = provider_and_client

    result = provider.on_pre_compress([
        {"role": "user", "content": "The service must stay backward compatible."},
        {"role": "assistant", "content": "I will preserve the public API."},
    ])

    assert result == ""
    assert [call[0] for call in client.calls] == ["capture_content", "flush_memory"]
    assert "backward compatible" in client.calls[0][1][2]
    assert client.calls[0][2]["metadata"]["kind"] == "hermes-context-compression"


def test_memory_tools_map_to_powercontext_operations(provider_and_client):
    provider, client = provider_and_client
    citation_args = {
        "family": "memory",
        "artifact_id": "memory-1",
        "revision": 1,
        "entry_id": "entry-1",
        "entry_version_id": "entry-version-1",
    }

    search = json.loads(provider.handle_tool_call("powercontext_search_memory", {"query": "deployment"}))
    saved = json.loads(
        provider.handle_tool_call(
            "powercontext_remember",
            {"kind": "decision", "text": "Use the Hermes standard Provider interface."},
        )
    )
    read = json.loads(provider.handle_tool_call("powercontext_get_memory", citation_args))
    retired = json.loads(provider.handle_tool_call("powercontext_retire_memory", citation_args))

    assert search["hits"]
    assert saved["status"] == "remembered"
    assert read["text"] == "a memory"
    assert retired["status"] == "retired"
    assert [call[0] for call in client.calls] == [
        "search_memory",
        "remember_memory",
        "get_memory_entry",
        "retire_memory_entry",
    ]


def test_backend_failure_fails_open(provider_and_client):
    provider, client = provider_and_client

    def failed_prepare(*args, **kwargs):
        from plugins.powercontext.client import PowerContextTransportError  # ty: ignore[unresolved-import]

        raise PowerContextTransportError("offline")

    client.prepare_context = failed_prepare

    assert provider.prefetch("query") == ""


def test_cli_registers_provider_commands():
    parser = argparse.ArgumentParser()
    root = parser.add_subparsers(dest="provider")
    provider = root.add_parser("powercontext")
    register_cli(provider)

    args = parser.parse_args(["powercontext", "search", "deployment", "--limit", "3"])

    assert args.powercontext_command == "search"
    assert args.query == "deployment"
    assert args.limit == 3
    assert callable(args.func)
