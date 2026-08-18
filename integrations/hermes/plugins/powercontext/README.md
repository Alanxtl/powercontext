# PowerContext Hermes Memory Provider

This plugin implements Hermes' `MemoryProvider` interface using the PowerContext
HTTP API. See [`integrations/hermes/README.md`](../../README.md) for setup,
configuration, scope isolation, and runtime behavior.

The plugin deliberately uses only the Python standard library for HTTP, so it
can be copied into Hermes without adding a dependency to the Hermes runtime.

## CLI

After enabling the provider and restarting Hermes so it discovers the command
tree, use:

```bash
hermes powercontext --help
hermes powercontext status
hermes powercontext search "Python project management"
hermes powercontext remember preference "The user prefers uv"
hermes powercontext flush
```

Use `--scope-id` to inspect a specific scope:

```bash
hermes powercontext search "deployment decision" --scope-id hermes-smoke-test
```
