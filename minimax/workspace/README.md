# taskboard — 被修改的业务工程

一个内存任务看板服务，用作本次 SDD 题目的被修改对象。

| 路径 | 内容 |
| --- | --- |
| `spec/` | 6 条规格条目（`SPEC-CORE-001..003` 生效中，`SPEC-FILTER-001..003` 待实现） |
| `src/taskboard/model.py` | `Task` 数据类与合法状态集合 |
| `src/taskboard/service.py` | `TaskBoardService`：增删查、完成、过滤与排序 |
| `src/taskboard/cli.py` | 演示命令（`python -m taskboard.cli`） |
| `tests/test_taskboard.py` | 9 个回归测试（既有行为，修改前后都应通过） |
| `tests/test_assignee_filter.py` | 9 个目标功能测试（按负责人过滤，修改前应失败、修改后应通过） |

运行测试：

```bash
python -m pytest -q tests
```

规格是本工程的行为依据：本次修改实现 `SPEC-FILTER-001`（按负责人过滤）、`SPEC-FILTER-002`
（空白输入按未指定处理）、`SPEC-FILTER-003`（与状态过滤、条数限制组合），并保持
`SPEC-CORE-001..003` 的既有行为不变。

修改前的文件快照见 `../evidence/snapshots/before/`，可用
`python -m sdd_context reset --label before` 还原。
