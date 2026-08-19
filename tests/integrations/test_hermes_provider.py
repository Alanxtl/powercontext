from __future__ import annotations

import argparse
import importlib
import json
import logging
import sys
import threading
from pathlib import Path

import pytest

HERMES_ROOT = Path(__file__).parents[2] / "integrations" / "hermes"
_HERMES_MODULE_NAMES = (
    "plugins.powercontext",
    "plugins.powercontext.client",
    "plugins.powercontext.cli",
)


@pytest.fixture
def hermes_modules(monkeypatch):
    previous_modules = {name: sys.modules.get(name) for name in _HERMES_MODULE_NAMES}
    for name in _HERMES_MODULE_NAMES:
        sys.modules.pop(name, None)
    monkeypatch.syspath_prepend(str(HERMES_ROOT))
    try:
        yield importlib.import_module("plugins.powercontext"), importlib.import_module("plugins.powercontext.cli")
    finally:
        for name in _HERMES_MODULE_NAMES:
            sys.modules.pop(name, None)
        for name, module in previous_modules.items():
            if module is not None:
                sys.modules[name] = module


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.base_url = "http://powercontext.test:8000"
        self._remember_count = 0
        self.memory_extraction = True

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
        self._remember_count += 1
        self.calls.append(("remember_memory", (scope_id, kind, text), {"reason": reason}))
        return {
            "status": "remembered",
            "entry": {
                "citation": {
                    "memory_ref": {"family": "memory", "artifact_id": f"memory-{self._remember_count}", "revision": 1},
                    "entry_id": f"entry-{self._remember_count}",
                    "entry_version_id": f"entry-version-{self._remember_count}",
                }
            },
        }

    def retire_memory_entry(self, scope_id, citation, *, reason=None):
        self.calls.append(("retire_memory_entry", (scope_id, citation), {"reason": reason}))
        return {"status": "retired"}

    def get_liveness(self):
        self.calls.append(("get_liveness", (), {}))
        return {"status": "ok"}

    def get_readiness(self):
        self.calls.append(("get_readiness", (), {}))
        return {"status": "ready"}

    def get_capabilities(self):
        self.calls.append(("get_capabilities", (), {}))
        return {"memory_extraction": self.memory_extraction}


@pytest.fixture
def provider_and_client(tmp_path, hermes_modules):
    provider_module, _cli_module = hermes_modules
    client = FakeClient()
    provider = provider_module.PowerContextMemoryProvider(
        {"scope_id": "hermes:{profile}:{user_id}"},
        client_factory=lambda _config: client,
    )
    provider.initialize("session-1", hermes_home=str(tmp_path), agent_identity="coder", user_id="user-7")
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


def test_queue_prefetch_honors_max_bytes_environment_override(provider_and_client, monkeypatch):
    provider, client = provider_and_client
    monkeypatch.setenv("POWERCONTEXT_HERMES_MAX_BYTES", "16000")

    provider.queue_prefetch("What did we decide about the deployment?")
    provider._wait_for_background()

    assert client.calls[0] == (
        "prepare_context",
        ("hermes:coder:user-7", "What did we decide about the deployment?"),
        {"max_bytes": 16000},
    )


def test_yaml_config_is_loaded_from_hermes_home(tmp_path, hermes_modules):
    provider_module, _cli_module = hermes_modules
    client = FakeClient()
    provider = provider_module.PowerContextMemoryProvider(client_factory=lambda config: client)
    config_path = tmp_path / "powercontext.yaml"
    config_path.write_text(
        "base_url: http://powercontext.test:9000\n"
        "scope_id: hermes:{profile}:{user_id}\n"
        "max_bytes: 1200\n"
        "capture_turns: false\n",
        encoding="utf-8",
    )

    provider.initialize("session-1", hermes_home=str(tmp_path), agent_identity="coder", user_id="user-7")

    assert provider._client is client
    assert provider._config["base_url"] == "http://powercontext.test:9000"
    assert provider._config["max_bytes"] == 1200
    assert provider._config["capture_turns"] is False
    assert provider._scope_id == "hermes:coder:user-7"
    provider.shutdown()


def test_memory_setup_schema_exposes_powercontext_configuration(hermes_modules):
    provider_module, _cli_module = hermes_modules
    provider = provider_module.PowerContextMemoryProvider()

    schema = provider.get_config_schema()
    fields = {field["key"]: field for field in schema}

    assert fields["base_url"]["default"] == "http://127.0.0.1:8000"
    assert fields["authorization"]["secret"] is True
    assert fields["authorization"]["env_var"] == "POWERCONTEXT_HERMES_AUTHORIZATION"
    assert fields["capture_pre_compress"]["choices"] == ["true", "false"]
    assert "capture_turns" in fields
    assert "flush_on_session_end" in fields


def test_memory_setup_saves_powercontext_yaml_and_preserves_existing_values(tmp_path, hermes_modules):
    provider_module, _cli_module = hermes_modules
    provider = provider_module.PowerContextMemoryProvider()
    config_path = tmp_path / "powercontext.yaml"
    config_path.write_text(
        "base_url: http://powercontext.test:8000\ncustom_setting: keep-me\n",
        encoding="utf-8",
    )

    provider.save_config(
        {"base_url": "http://powercontext.test:9000", "max_bytes": "16000"},
        str(tmp_path),
    )

    import yaml

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved == {
        "base_url": "http://powercontext.test:9000",
        "custom_setting": "keep-me",
        "max_bytes": "16000",
    }


def test_sync_turn_is_flushed_before_session_end(provider_and_client):
    provider, client = provider_and_client

    provider.sync_turn("Use uv for the integration.", "I will add a uv check.", session_id="session-1")
    provider.on_session_end([])

    names = [call[0] for call in client.calls]
    assert names == ["capture_content", "get_capabilities", "flush_memory"]
    assert client.calls[0][1][0] == "hermes:coder:user-7"
    assert client.calls[0][2]["metadata"]["kind"] == "hermes-turn"


def test_session_end_skips_flush_when_memory_extraction_is_disabled(provider_and_client):
    provider, client = provider_and_client
    client.memory_extraction = False

    provider.sync_turn("Captured as a Source.", "No extraction is available.", session_id="session-1")
    provider.on_session_end([])

    assert [call[0] for call in client.calls] == ["capture_content", "get_capabilities"]


def test_pre_compress_persists_context_before_compression(provider_and_client):
    provider, client = provider_and_client
    provider._config["capture_pre_compress"] = True

    result = provider.on_pre_compress([
        {"role": "user", "content": "The service must stay backward compatible."},
        {"role": "assistant", "content": "I will preserve the public API."},
    ])

    assert result == ""
    assert [call[0] for call in client.calls] == [
        "capture_content",
        "get_capabilities",
        "flush_memory",
    ]
    assert "backward compatible" in client.calls[0][1][2]
    assert client.calls[0][2]["metadata"]["kind"] == "hermes-context-compression"


def test_pre_compress_is_disabled_by_default(provider_and_client):
    provider, client = provider_and_client

    provider.on_pre_compress([{"role": "user", "content": "Do not capture this by default."}])

    assert client.calls == []


def test_pre_compress_filters_roles_and_redacts_secrets(provider_and_client):
    provider, client = provider_and_client
    provider._config["capture_pre_compress"] = True

    provider.on_pre_compress([
        {"role": "system", "content": "system-secret=system-value"},
        {"role": "user", "content": "Use api_key=super-secret-value for deployment."},
        {"role": "tool", "content": "tool-secret=tool-value"},
        {"role": "assistant", "content": "password=hunter2 is not persisted."},
    ])

    content = client.calls[0][1][2]
    assert "system-secret" not in content
    assert "tool-secret" not in content
    assert "super-secret-value" not in content
    assert "hunter2" not in content
    assert "[REDACTED]" in content
    assert "deployment" in content


def test_pre_compress_captures_only_new_overlapping_windows(provider_and_client):
    provider, client = provider_and_client
    provider._config["capture_pre_compress"] = True

    first_window = [
        {"role": "user", "content": "First user turn."},
        {"role": "assistant", "content": "First assistant turn."},
    ]
    second_window = [
        *first_window,
        {"role": "user", "content": "Second user turn."},
        {"role": "assistant", "content": "Second assistant turn."},
    ]
    third_window = [
        {"role": "user", "content": "Second user turn."},
        {"role": "assistant", "content": "Second assistant turn."},
        {"role": "user", "content": "Third user turn."},
        {"role": "assistant", "content": "Third assistant turn."},
    ]

    provider.on_pre_compress(first_window)
    provider.on_pre_compress(second_window)
    provider.on_pre_compress(third_window)
    provider.on_pre_compress(third_window)

    capture_calls = [call for call in client.calls if call[0] == "capture_content"]
    assert len(capture_calls) == 3
    assert "First user turn" in capture_calls[0][1][2]
    assert "Second user turn" in capture_calls[1][1][2]
    assert "First user turn" not in capture_calls[1][1][2]
    assert "Third user turn" in capture_calls[2][1][2]
    assert "Second user turn" not in capture_calls[2][1][2]
    assert len({call[1][1] for call in capture_calls}) == 3


def test_memory_write_retires_mapped_entries_for_replace_and_remove(provider_and_client):
    provider, client = provider_and_client

    provider.on_memory_write("add", "user", "The user prefers uv.")
    provider._wait_for_background()
    provider.on_memory_write(
        "replace",
        "user",
        "The user prefers rye.",
        {"old_text": "The user prefers uv."},
    )
    provider._wait_for_background()
    provider.on_memory_write(
        "remove",
        "user",
        "",
        {"old_text": "The user prefers rye."},
    )
    provider._wait_for_background()

    assert [call[0] for call in client.calls] == [
        "remember_memory",
        "retire_memory_entry",
        "remember_memory",
        "retire_memory_entry",
    ]
    assert client.calls[1][1][1]["entry_id"] == "entry-1"
    assert client.calls[3][1][1]["entry_id"] == "entry-2"
    assert provider._memory_map == {}


def test_memory_write_skips_replace_and_remove_without_old_text(provider_and_client):
    provider, client = provider_and_client

    provider.on_memory_write("replace", "memory", "new value")
    provider.on_memory_write("remove", "memory", "")
    provider._wait_for_background()

    assert client.calls == []


def test_memory_write_worker_is_daemon_bounded_and_reports_overflow(provider_and_client, caplog):
    provider, _client = provider_and_client
    memory_queue = provider._memory_write_queue
    memory_thread = provider._memory_write_thread
    assert memory_queue is not None
    assert memory_thread is not None and memory_thread.daemon

    started = threading.Event()
    release = threading.Event()
    assert provider._enqueue_memory_write(lambda: (started.set(), release.wait(timeout=1)))
    assert started.wait(timeout=1)
    for _ in range(memory_queue.maxsize + 1):
        provider._enqueue_memory_write(lambda: None)
    assert provider._dropped_memory_writes >= 1

    release.set()
    with caplog.at_level(logging.WARNING):
        provider.shutdown()
    assert "memory-write shutdown dropped" in caplog.text


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


def test_cli_registers_provider_commands(hermes_modules):
    _provider_module, cli_module = hermes_modules
    parser = argparse.ArgumentParser()
    root = parser.add_subparsers(dest="provider")
    provider = root.add_parser("powercontext")
    cli_module.register_cli(provider)

    args = parser.parse_args(["powercontext", "search", "deployment", "--limit", "3"])

    assert args.powercontext_command == "search"
    assert args.query == "deployment"
    assert args.limit == 3
    assert callable(args.func)
