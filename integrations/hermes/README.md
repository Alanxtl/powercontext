# PowerContext integration for Hermes Agent

This directory contains a standard Hermes `MemoryProvider` backed by a running
PowerContext server. It keeps Hermes responsible for memory lifecycle and Agent
orchestration while PowerContext provides external storage, retrieval, context
preparation, and memory lifecycle operations.

## Install as a directory provider

Copy `plugins/powercontext` into one of the Hermes provider locations:

```bash
cp -R integrations/hermes/plugins/powercontext \
  "$HERMES_HOME/plugins/powercontext"
```

For project-local installation, copy it to `.hermes/plugins/powercontext` and
enable project plugins with `HERMES_ENABLE_PROJECT_PLUGINS=1`.

Activate it in Hermes configuration:

```yaml
memory:
  provider: powercontext
```

Start PowerContext separately:

```bash
powercontext server run
```

## Configuration

The provider uses `http://127.0.0.1:8000` by default. Configuration can be
stored in `$HERMES_HOME/powercontext.json`:

```json
{
  "base_url": "http://127.0.0.1:8000",
  "scope_id": "hermes:{profile}:{user_id}",
  "max_bytes": 8000,
  "timeout": 5,
  "capture_turns": true,
  "flush_on_session_end": true
}
```

Environment variables override file values:

| Variable | Purpose |
| --- | --- |
| `POWERCONTEXT_HERMES_BASE_URL` | PowerContext server URL |
| `POWERCONTEXT_HERMES_AUTHORIZATION` | Complete authorization header, e.g. `Bearer <token>` |
| `POWERCONTEXT_HERMES_TOKEN` | Token shorthand; used when `AUTHORIZATION` is absent |
| `POWERCONTEXT_HERMES_SCOPE_ID` | Explicit scope or scope template |
| `POWERCONTEXT_HERMES_MAX_BYTES` | Maximum prepared context size, 512–32768 |
| `POWERCONTEXT_HERMES_TIMEOUT` | HTTP request timeout in seconds |
| `POWERCONTEXT_HERMES_CAPTURE_TURNS` | Capture completed turns as PowerContext Sources |
| `POWERCONTEXT_HERMES_FLUSH_ON_SESSION_END` | Run memory extraction at session end |

The default scope template is `hermes:{profile}:{user_id}`. The provider uses
the active Hermes profile and gateway user identifier when available. For local
CLI sessions without a user identifier, it derives a stable identifier from
the active `HERMES_HOME` path. This prevents cross-profile memory leakage while
keeping memories available across sessions.

## Runtime behavior

- `prefetch()` calls `/v1/context/prepare` and injects bounded context as
  untrusted historical evidence.
- `queue_prefetch()` performs the same preparation in the background and caches
  the exact query for the next turn.
- `sync_turn()` captures the completed turn through `/v1/sources/content` in a
  non-blocking single-worker queue.
- `on_session_end()` waits for queued writes and calls `/v1/memory/flush`.
- `on_pre_compress()` persists the bounded pre-compression conversation and
  flushes it before Hermes discards old messages.
- `on_memory_write()` mirrors built-in Hermes memory additions/replacements to
  PowerContext as explicit memory entries.
- Agent tools expose search, exact citation reads, explicit writes, and memory
  retirement.

All backend failures fail open: they are logged without request content and do
not interrupt the Hermes conversation.

## CLI commands

After restarting Hermes so it discovers the new command tree:

```bash
hermes powercontext --help
hermes powercontext status
hermes powercontext search "Python project management"
hermes powercontext remember preference "The user prefers uv"
hermes powercontext flush
```

Use `--scope-id` when inspecting a scope explicitly:

```bash
hermes powercontext search "deployment decision" --scope-id hermes-smoke-test
```

The `get` and `retire` commands accept the five fields returned in a search
citation: `family`, `artifact_id`, `revision`, `entry_id`, and
`entry_version_id`.

## Package provider option

Hermes also supports `hermes_agent.memory_providers` entry points. If this
integration is distributed as a package, point the entry point at the provider
package's `register` function:

```toml
[project.entry-points."hermes_agent.memory_providers"]
powercontext = "powercontext_hermes:register"
```

The directory layout is the reference implementation for direct installation.
