# SPEC-CORE-003 结果条数限制

- 标识：SPEC-CORE-003
- 状态：生效
- 适用范围：`TaskBoardService.list_tasks`

## 需求

`limit` 用于限制返回条数：

- `limit=None` 表示不限制；
- `limit` 必须是不小于 1 的整数，否则抛出 `ValueError`。

## 不变量

- `limit` 在过滤与排序之后生效（见 SPEC-FILTER-003）。

## 验收要点

- `limit=0` 与 `limit=-1` 抛出 `ValueError`，不返回空列表。
- `limit` 大于实际条数时返回全部结果，不报错。
