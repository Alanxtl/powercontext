# MiniMax 赛题：面向软件持续演进的 SDD 上下文管理系统

本目录是「规有所依——面向软件持续演进的 SDD 上下文管理系统」题目的完整提交物。

## 一次说清它做了什么

围绕一次真实的局部修改（为任务列表增加按负责人过滤），模块完成了题面要求的完整闭环：

1. **导入**：`sdd_context import` 把业务工程的规格、代码符号与测试写入 **seekdb** 语料库，
   同时作为 Source 存入 **PowerContext**。
2. **选上下文**：`sdd_context context --task ...` 用 seekdb 全文检索挑出适用规格、需要修改的
   代码符号与验证测试，组装成带来源清单的开发上下文。
3. **改代码**：上下文交给 **MiniMax**，由模型产出文件级修改；测试未通过时把失败信息回灌给
   MiniMax 继续修复（最多 `SDD_MAX_ROUNDS` 轮）。
4. **验证**：修改前目标测试因行为未实现而失败，修改后目标测试与回归测试全部通过。
5. **存状态**：任务进展与验证结果写入 **PowerContext**（同一个 seekdb 实例）。
6. **重启复用**：新进程 `sdd_context resume --task ...` 读回状态并继续使用。

## 目录结构

| 路径 | 说明 |
| --- | --- |
| `sdd_context/` | 上下文模块源码（CLI 入口 `python -m sdd_context`） |
| `workspace/` | 被修改的业务工程（taskboard）：`spec/` 规格、`src/` 代码、`tests/` 测试 |
| `docs/01-运行说明.md` | 依赖版本、配置、命令、验证步骤、已知限制 |
| `docs/02-验证样例.md` | 任务描述、有效规格、初始数据、操作步骤、预期结果、重置方法 |
| `docs/03-技术使用记录.md` | MiniMax 开发记录、seekdb 查询记录、PowerContext 状态写入与复用记录 |
| `docs/04-一页技术说明.md` | 模块结构、上下文选择方式、三项技术的分工 |
| `evidence/` | 运行产物：MiniMax 往来记录、seekdb 查询日志、测试日志、代码差异 |
| `scripts/run_demo.sh` / `run_demo.ps1` | 一键跑完整流程 |

## 快速开始

```bash
cd minimax
export MINIMAX_API_KEY=<你的 MiniMax API Key>
export MINIMAX_BASE_URL=https://api.minimax.io/v1
export MINIMAX_MODEL=MiniMax-M3.1-Flash
bash scripts/run_demo.sh
```

Windows：

```powershell
cd minimax
$env:MINIMAX_API_KEY = '<你的 MiniMax API Key>'
powershell -ExecutionPolicy Bypass -File scripts\run_demo.ps1
```

完整说明见 [`docs/01-运行说明.md`](docs/01-运行说明.md)。

## 技术接入方式

| 技术 | 接入方式 | 版本 |
| --- | --- | --- |
| MiniMax | HTTP API（Anthropic Messages 兼容协议 / OpenAI Chat Completions 兼容协议），由 `sdd_context/minimax_client.py` 调用 | 模型 `MiniMax-M3.1-Flash`（可由 `MINIMAX_MODEL` 覆盖） |
| seekdb | 嵌入式实例（`pylibseekdb`），语料表与 PowerContext 状态表位于同一个数据库 | `pylibseekdb 1.4.0.post2` |
| PowerContext | Python SDK（`powercontext` 包，进程内 `open_builtin_runtime`） | `powercontext 1.4.1` |
