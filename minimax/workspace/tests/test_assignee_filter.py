"""Target feature tests for the assignee filter (SPEC-FILTER-001..003).

These tests describe behaviour that is not implemented yet, so they fail on the
pre-change code base and pass after the change is applied.
"""

from __future__ import annotations

import pytest

from taskboard.model import Task
from taskboard.service import TaskBoardService


@pytest.fixture()
def service() -> TaskBoardService:
    board = TaskBoardService()
    board.add_task(Task(id="T-1", title="a", assignee="Alice", status="todo", priority=1, created_at="2026-01-01"))
    board.add_task(Task(id="T-2", title="b", assignee="bob", status="done", priority=3, created_at="2026-01-02"))
    board.add_task(Task(id="T-3", title="c", assignee=None, status="todo", priority=3, created_at="2026-01-03"))
    board.add_task(Task(id="T-4", title="d", assignee="alice", status="todo", priority=2, created_at="2026-01-04"))
    return board


def test_filter_by_assignee_returns_only_matching_tasks(service: TaskBoardService) -> None:
    tasks = service.list_tasks(assignee="bob")
    assert [task.id for task in tasks] == ["T-2"]


def test_filter_by_assignee_is_case_insensitive(service: TaskBoardService) -> None:
    # SPEC-FILTER-001 compares case-insensitively, so "Alice" (T-1) and "alice" (T-4) both match.
    assert [task.id for task in service.list_tasks(assignee="ALICE")] == ["T-4", "T-1"]
    assert [task.id for task in service.list_tasks(assignee="alice")] == ["T-4", "T-1"]


def test_filter_by_assignee_trims_surrounding_whitespace(service: TaskBoardService) -> None:
    assert [task.id for task in service.list_tasks(assignee="  bob  ")] == ["T-2"]


def test_unassigned_task_is_not_returned_for_a_specific_assignee(service: TaskBoardService) -> None:
    assert "T-3" not in [task.id for task in service.list_tasks(assignee="alice")]


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_blank_assignee_is_treated_as_no_filter(service: TaskBoardService, blank: str) -> None:
    assert [task.id for task in service.list_tasks(assignee=blank)] == [
        task.id for task in service.list_tasks()
    ]


def test_assignee_combines_with_status_filter(service: TaskBoardService) -> None:
    tasks = service.list_tasks(status="todo", assignee="alice")
    assert [task.id for task in tasks] == ["T-4", "T-1"]


def test_assignee_combines_with_limit(service: TaskBoardService) -> None:
    tasks = service.list_tasks(assignee="alice", limit=1)
    assert len(tasks) == 1
