# SPEC-FILTER-003 负责人过滤与其它过滤条件组合

- 标识：SPEC-FILTER-003
- 状态：待实现
- 适用范围：`TaskBoardService.list_tasks`

## 需求

`assignee` 过滤必须与 `status` 过滤、`limit` 限制同时可用，多个条件之间是「与」关系。

## 不变量

- 同时给出 `status` 与 `assignee` 时，返回同时满足两个条件的任务。
- `limit` 在过滤之后生效：先过滤，再截断。

## 验收要点

- `list_tasks(status="todo", assignee="alice")` 返回既未完成又属于 Alice 的任务。
- `list_tasks(assignee="alice", limit=1)` 在过滤后只保留第一条。
