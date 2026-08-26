---
title: 插件集成约定
description: 插件可见集成诊断 RFC 的约定。
---

# 插件可见诊断约定

本文记录 Host-visible integration diagnostics RFC 引入的约定，只覆盖本次诊断切片。共享 service state、service lifecycle、安装方式和平台 adapter
约定属于各自的 RFC 与实现切片，不在本文定义。

当前约定适用于 Codex、Claude Code、DeepSeek Harness（DSH）、OpenClaw、Pi 和 Hermes。Bub 尚未具备插件通道、实现、测试和支持资格，暂不在范围内。

文中的“必须（MUST）”“应该（SHOULD）”和“可以（MAY）”是插件实现与评审的规范性要求。

本约定来源于 [RFC 1299：Local Server availability and service installation](../rfcs/1299_local_server_availability_and_service_installation.md)。
原始 [RFC PR #1299](https://github.com/oceanbase/powercontext/pull/1299) 对应的跟踪 issue 是 [#1298](https://github.com/oceanbase/powercontext/issues/1298)。

## 插件必须报告什么

当插件可见操作因为下列已分类的 backend failure 无法完成时，插件必须报告 PowerContext failure。适用操作包括 context preparation、recall、capture、flush、
direct tool、slash command 以及 health/status check。

插件必须使用 typed client error 进行分类，不能通过匹配 exception message 文本来分类。

| Outcome | 分类规则 |
| --- | --- |
| `authentication_failed` | HTTP 401。 |
| `version_mismatch` | HTTP 404，通常表示 endpoint 不兼容或不存在。 |
| `server_unavailable` | 连接失败、超时、请求中止或 HTTP 503。 |
| `invalid_response` | 其他 HTTP failure、JSON 损坏、响应结构错误或解码/schema failure。 |

合法但为空的结果不是失败诊断。尤其是空的 memory 结果不能报告为 `server_unavailable`。

## 诊断事件格式

每条诊断必须通过插件支持的通道写出一个单行 JSON object：

```json
{
  "component": "powercontext.openclaw",
  "event": "context_prepare",
  "outcome": "server_unavailable",
  "recovery": "powercontext doctor"
}
```

### 字段

| 字段 | 要求 |
| --- | --- |
| `component` | 稳定且包含插件名称，例如 `powercontext.dsh` 或 `powercontext.claude_code.recall`。 |
| `event` | 简短的 lower-snake-case 事件，例如 `context_prepare`、`capture_source`、`tool_call` 或 `status`。不能包含 prompt、query、URL 或 identifier。 |
| `outcome` | 必须是上面定义的四种 outcome 之一。 |
| `http_status` | HTTP 响应可以携带的整数。传输失败不能伪造该字段。 |
| `recovery` | `server_unavailable` 时必须为 `powercontext doctor`；其他 outcome 通常省略。 |

可以增加 bounded、非敏感且有助于解释生命周期事件的字段，例如数字类型的 `content_bytes` 或受限的 `context_status`。

### 示例

```json
{"component":"powercontext.codex.recall","event":"context_prepare","outcome":"authentication_failed","http_status":401}
{"component":"powercontext.pi","event":"context_prepare","outcome":"version_mismatch","http_status":404}
{"component":"powercontext.hermes","event":"tool_call","outcome":"server_unavailable","recovery":"powercontext doctor"}
{"component":"powercontext.dsh","event":"capture_source","outcome":"invalid_response","http_status":500}
```

## 插件展示约定

每个插件必须使用插件原生通道。诊断不能插入 model content、recalled context 或成功的 tool result。

| 插件 | 通道 | Component 前缀 |
| --- | --- | --- |
| Codex | Hook `stderr` | `powercontext.codex.recall` |
| Claude Code | Hook `stderr` | `powercontext.claude_code.recall` |
| DSH | 插件 logger warning | `powercontext.dsh` |
| OpenClaw | Plugin API logger warning | `powercontext.openclaw` |
| Pi | 插件终端 warning（`console.warn`） | `powercontext.pi` |
| Hermes | Plugin logger warning | `powercontext.hermes` |

插件侧 operation result 可以继续返回 `PowerContext operation failed` 这类通用错误。结构化诊断是恢复信号，通用错误只服务于 host/model 控制流。

## Fail-open、隐私和展示边界

PowerContext operation 失败时：

- recall/context preparation 必须返回空的 recalled context，不能返回 partial 或伪造的 context；
- capture 和 flush 不能终止插件 session，也不能无限期阻塞插件；
- direct tool 和 command 必须返回不包含请求细节的通用 failure result；
- 诊断发送本身必须是 best effort，日志失败不能把 backend failure 变成插件 failure。

诊断不能包含 endpoint URL、authorization header、token、cookie、filesystem path、prompt、query、capture text、recall text、response body 或 stack trace。

重复失败必须有展示边界。长生命周期插件应该按 `outcome` 去重 60 秒。短生命周期 hook 可以在一次 invocation 内去重，但不能为一个失败无限输出诊断。
去重 key 是 outcome，不能使用 user input 或 request payload。

## 本 RFC 的插件实现约定

本 RFC 中的每个插件实现都必须：

1. 复用 shared client 的 typed error 和上面的 outcome mapping。
2. 在所有相关 failure exit 接入诊断，包括 lifecycle callback 和 direct tool/command path。
3. 使用稳定的 component 和 event name；不能把用户数据或请求数据放进字段。
4. 让 diagnostic formatter 与 model-facing content formatter 解耦。
5. PowerContext 不可用时保持插件的正常行为。
6. 在同一个 implementation slice 中同步更新文档和测试。

插件可以选择语言和内部 helper 结构，但必须保持可观察的 JSON 约定和上表中的插件通道。

## 必须覆盖的测试矩阵

实现本 RFC 的每个插件 PR 都必须测试以下可观察行为：

1. 传输失败或超时产生 `server_unavailable` 和 `powercontext doctor`。
2. HTTP 503 产生带有 `http_status: 503` 的 `server_unavailable`。
3. HTTP 401 产生 `authentication_failed`。
4. HTTP 404 产生 `version_mismatch`。
5. 其他 HTTP failure 和 malformed response 产生 `invalid_response`。
6. 重复失败在约定边界内去重。
7. 在插件提供这些入口时，recall、capture、flush、direct tool、slash command 和 status path 都保持 fail-open。
8. 诊断不包含 URL、token、prompt、query、response body 或 stack trace。
9. 对应的插件 runner、type checker 或 smoke test 通过。

测试应该断言解析后的 event 和插件可见通道，不应该冻结 private call order 或内部 helper name。

## 不在范围内

本文不定义：

- shared service state 或 native service lifecycle；
- service 安装、ownership、restart policy 或平台支持资格；
- 所有插件共用的 UI；
- Bub 集成。

这些决定需要独立的实现证据和评审边界。
