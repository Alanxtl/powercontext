# SPEC-CORE-001 任务列表默认排序

- 标识：SPEC-CORE-001
- 状态：生效
- 适用范围：`TaskBoardService.list_tasks`

## 需求

`list_tasks` 返回的任务必须按 `priority` 降序排列；`priority` 相同时按 `created_at` 升序排列（先创建者在前）。

## 不变量

- 返回结果始终是新的 `list`，调用方修改返回值不得影响服务内部状态。
- 返回顺序在相同输入下必须稳定可复现。

## 验收要点

- 同一批任务以不同顺序加入服务，`list_tasks()` 的输出顺序完全一致。
