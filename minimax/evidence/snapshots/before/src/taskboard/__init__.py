"""Task board: a small business service used as the SDD work target."""

from taskboard.model import TASK_STATUSES, Task
from taskboard.service import TaskBoardService

__all__ = ["TASK_STATUSES", "Task", "TaskBoardService"]
