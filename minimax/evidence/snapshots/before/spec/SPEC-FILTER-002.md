# SPEC-FILTER-002 空白负责人输入的边界行为

- 标识：SPEC-FILTER-002
- 状态：待实现
- 适用范围：`TaskBoardService.list_tasks`

## 需求

`assignee` 传入空白字符串或纯空白字符（去除首尾空白后为空）时，视为「未指定负责人过滤」，返回全部任务，与不传 `assignee` 的结果一致。

## 不变量

- 不得因为空白输入抛出异常，也不得返回空列表。
- 不得把空白输入当作「只匹配未指派任务」。

## 验收要点

- `assignee=""` 与 `assignee="   "` 的返回结果与不传 `assignee` 完全一致。
- `assignee="\t\n"` 同样按未指定处理。
