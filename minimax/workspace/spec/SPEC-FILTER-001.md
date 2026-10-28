# SPEC-FILTER-001 按负责人过滤

- 标识：SPEC-FILTER-001
- 状态：待实现
- 适用范围：`TaskBoardService.list_tasks`

## 需求

`list_tasks` 必须支持 `assignee` 关键字参数，用于按任务负责人过滤：

- 只返回 `assignee` 与给定值相同的任务；
- 比较时忽略大小写；
- 比较前去除给定值两端空白；
- 未指派（`assignee is None`）的任务永远不出现在任何具体负责人的过滤结果中。

## 不变量

- 未传 `assignee` 时行为不变，返回全部任务（SPEC-CORE-001 / SPEC-CORE-002 不受影响）。
- 过滤结果仍按 SPEC-CORE-001 的默认排序返回。

## 验收要点

- `assignee="alice"` 只返回负责人为 Alice 的任务。
- `assignee="ALICE"` 与 `assignee="alice"` 返回结果一致。
- 未指派的任务不出现在任何具体负责人过滤结果中。
