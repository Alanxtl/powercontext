"""Task board service.

The behaviour of this module is driven by the specification entries under
``spec/``:

- ``SPEC-CORE-001`` default ordering of :meth:`TaskBoardService.list_tasks`
- ``SPEC-CORE-002`` status filtering
- ``SPEC-CORE-003`` result count limit
"""

from __future__ import annotations

from collections.abc import Iterable

from taskboard.model import TASK_STATUSES, Task


class TaskBoardService:
    """In-memory task board with filtering and ordering rules."""

    def __init__(self, tasks: Iterable[Task] = ()) -> None:
        self._tasks: list[Task] = list(tasks)

    def add_task(self, task: Task) -> Task:
        """Register a task and return it."""

        if any(existing.id == task.id for existing in self._tasks):
            raise ValueError(f"duplicate task id: {task.id!r}")
        self._tasks.append(task)
        return task

    def get_task(self, task_id: str) -> Task:
        """Return one task by id."""

        for task in self._tasks:
            if task.id == task_id:
                return task
        raise KeyError(task_id)

    def complete_task(self, task_id: str) -> Task:
        """Mark a task as done and return the updated task."""

        task = self.get_task(task_id)
        updated = Task(
            id=task.id,
            title=task.title,
            assignee=task.assignee,
            status="done",
            priority=task.priority,
            created_at=task.created_at,
        )
        self._tasks[self._tasks.index(task)] = updated
        return updated

    def list_tasks(self, *, status: str | None = None, limit: int | None = None) -> list[Task]:
        """Return tasks ordered by priority (desc) then creation time (asc).

        ``status`` restricts the result to one task status (SPEC-CORE-002) and
        ``limit`` caps the number of returned tasks (SPEC-CORE-003).
        """

        if status is not None and status not in TASK_STATUSES:
            raise ValueError(f"unsupported task status: {status!r}")
        if limit is not None and limit < 1:
            raise ValueError(f"limit must be a positive integer, got {limit!r}")

        selected = [task for task in self._tasks if status is None or task.status == status]
        selected.sort(key=lambda task: (-task.priority, task.created_at))
        if limit is not None:
            selected = selected[:limit]
        return selected

    def __len__(self) -> int:
        return len(self._tasks)
