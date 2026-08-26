---
title: Host-visible diagnostics contract
description: Contract for the host-visible integration diagnostics RFC.
---

# Host-visible diagnostics contract

This page records the contract introduced by the host-visible integration diagnostics RFC. It covers the diagnostic
slice only; shared service state, service lifecycle, installation, and platform adapter contracts belong to their
respective RFCs and implementation slices.

The contract currently applies to Codex, Claude Code, DeepSeek Harness (DSH), OpenClaw, Pi, and Hermes. Bub is out of
scope until it has a host channel, implementation, tests, and support qualification.

The words **MUST**, **SHOULD**, and **MAY** are normative requirements for plugin implementation and review.

This contract is derived from [RFC 1299: Local Server availability and service installation](../rfcs/1299_local_server_availability_and_service_installation.md).
The original [RFC PR #1299](https://github.com/oceanbase/powercontext/pull/1299) is tracked by [issue #1298](https://github.com/oceanbase/powercontext/issues/1298).

## What a plugin must report

A plugin MUST report a PowerContext failure when a host-visible operation cannot complete because of one of the
classified backend failures. The operation may be context preparation, recall, capture, flush, a direct tool or slash
command, or a health/status check.

The plugin MUST use typed client errors to make the classification. It MUST NOT classify failures by matching text in
an exception message.

| Outcome | Classification |
| --- | --- |
| `authentication_failed` | HTTP 401. |
| `version_mismatch` | HTTP 404, normally an incompatible or missing endpoint. |
| `server_unavailable` | Connection failure, timeout, aborted request, or HTTP 503. |
| `invalid_response` | Other HTTP failures, malformed JSON, invalid response shape, or decoding/schema failures. |

An empty but valid result is not a failure diagnostic. In particular, an empty memory result MUST NOT be reported as
`server_unavailable`.

## Diagnostic event format

Each diagnostic MUST be one JSON object written as one line through the host's supported channel:

```json
{
  "component": "powercontext.openclaw",
  "event": "context_prepare",
  "outcome": "server_unavailable",
  "recovery": "powercontext doctor"
}
```

### Fields

| Field | Requirement |
| --- | --- |
| `component` | Stable host-qualified name, such as `powercontext.dsh` or `powercontext.claude_code.recall`. |
| `event` | Short lower-snake-case event, such as `context_prepare`, `capture_source`, `tool_call`, or `status`. It MUST NOT contain a prompt, query, URL, or identifier. |
| `outcome` | One of the four outcomes defined above. |
| `http_status` | Optional integer for an HTTP response. It MUST NOT be fabricated for a transport failure. |
| `recovery` | MUST equal `powercontext doctor` for `server_unavailable`; normally omitted for other outcomes. |

Additional fields MAY be included only when they are bounded, non-sensitive, and useful to interpret the lifecycle
event. For example, a numeric `content_bytes` or a bounded `context_status` is acceptable.

### Examples

```json
{"component":"powercontext.codex.recall","event":"context_prepare","outcome":"authentication_failed","http_status":401}
{"component":"powercontext.pi","event":"context_prepare","outcome":"version_mismatch","http_status":404}
{"component":"powercontext.hermes","event":"tool_call","outcome":"server_unavailable","recovery":"powercontext doctor"}
{"component":"powercontext.dsh","event":"capture_source","outcome":"invalid_response","http_status":500}
```

## Host presentation contract

Each plugin MUST use the native host channel. Diagnostics MUST NOT be inserted into model content, recalled context,
or a successful tool result.

| Host | Channel | Component prefix |
| --- | --- | --- |
| Codex | Hook `stderr` | `powercontext.codex.recall` |
| Claude Code | Hook `stderr` | `powercontext.claude_code.recall` |
| DSH | Host logger warning | `powercontext.dsh` |
| OpenClaw | Plugin API logger warning | `powercontext.openclaw` |
| Pi | Host terminal warning (`console.warn`) | `powercontext.pi` |
| Hermes | Plugin logger warning | `powercontext.hermes` |

The host-facing operation result MAY remain a generic error such as `PowerContext operation failed`. The structured
diagnostic is the recovery signal; the generic result is only for host/model control flow.

## Fail-open, privacy, and presentation bounds

When a PowerContext operation fails:

- recall/context preparation MUST return no recalled context rather than partial or fabricated context;
- capture and flush MUST not terminate or block the host session indefinitely;
- direct tools and commands MUST return a generic failure result without exposing request details;
- diagnostic emission itself MUST be best effort and MUST NOT turn a backend failure into a host failure.

Diagnostics MUST NOT contain endpoint URLs, authorization headers, tokens, cookies, filesystem paths, prompts, queries,
captured text, recalled text, response bodies, or stack traces.

Repeated failures MUST have bounded presentation. Long-lived plugins SHOULD deduplicate by `outcome` for 60 seconds.
Short-lived hooks MAY deduplicate within one invocation, but MUST NOT emit an unbounded stream for one failure. The
deduplication key is the outcome, not user input or the request payload.

## Plugin implementation conventions for this RFC

Every plugin implementation in this RFC MUST:

1. Reuse the shared client error types and the outcome mapping above.
2. Attach diagnostics to every relevant failure exit, including lifecycle callbacks and direct tool/command paths.
3. Use a stable component and event name; never put user or request data in either field.
4. Keep the diagnostic formatter independent from model-facing content formatting.
5. Preserve the host's normal behavior when PowerContext is unavailable.
6. Add or update documentation and tests in the same implementation slice.

The plugin MAY choose its language and internal helper shape. It MUST preserve the observable JSON contract and the
host channel listed above.

## Required test matrix

Each plugin PR that implements this RFC MUST test the following observable behavior:

1. Transport failure or timeout produces `server_unavailable` and `powercontext doctor`.
2. HTTP 503 produces `server_unavailable` with `http_status: 503`.
3. HTTP 401 produces `authentication_failed`.
4. HTTP 404 produces `version_mismatch`.
5. Other HTTP failures and malformed responses produce `invalid_response`.
6. Repeated failures are deduplicated within the documented bound.
7. Recall, capture, flush, direct tool, slash command, and status paths remain fail-open where the host exposes them.
8. The diagnostic contains no URL, token, prompt, query, response body, or stack trace.
9. The matching host runner, type checker, or smoke test passes.

Tests SHOULD assert the parsed event and the host-visible channel. They SHOULD NOT freeze private call order or
internal helper names.

## Out of scope

This contract does not define:

- shared service state or native service lifecycle;
- service installation, ownership, restart policy, or platform support qualification;
- a common UI for every host;
- the Bub integration.

Those decisions require their own implementation evidence and review boundary.
