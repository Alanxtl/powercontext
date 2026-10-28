# SPEC-CORE-002 任务状态过滤

- 标识：SPEC-CORE-002
- 状态：生效
- 适用范围：`TaskBoardService.list_tasks`

## 需求

`list_tasks(status=...)` 按任务状态过滤。合法取值为 `todo` 与 `done`；其它取值必须抛出 `ValueError`。

## 不变量

- 未传 `status` 时不过滤状态，返回全部任务。
- 状态过滤与默认排序（SPEC-CORE-001）同时生效。

## 验收要点

- `status="todo"` 只返回未完成任务。
- `status="archived"` 抛出 `ValueError`，而不是静默返回空列表。
