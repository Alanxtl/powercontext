"""Regression tests for the behaviour that already exists.

These tests must pass before and after the assignee filter change.
"""

from __future__ import annotations

import pytest

from taskboard.model import Task
from taskboard.service import TaskBoardService


def make_service() -> TaskBoardService:
    service = TaskBoardService()
    service.add_task(Task(id="T-1", title="a", assignee="Alice", status="todo", priority=1, created_at="2026-01-01"))
    service.add_task(Task(id="T-2", title="b", assignee="bob", status="done", priority=3, created_at="2026-01-02"))
    service.add_task(Task(id="T-3", title="c", assignee=None, status="todo", priority=3, created_at="2026-01-03"))
    return service


def test_default_order_is_priority_desc_then_created_at_asc() -> None:
    tasks = make_service().list_tasks()
    assert [task.id for task in tasks] == ["T-2", "T-3", "T-1"]


def test_status_filter_returns_only_matching_status() -> None:
    tasks = make_service().list_tasks(status="todo")
    assert [task.id for task in tasks] == ["T-3", "T-1"]


def test_unknown_status_raises_value_error() -> None:
    with pytest.raises(ValueError):
        make_service().list_tasks(status="archived")


@pytest.mark.parametrize("limit", [0, -1])
def test_non_positive_limit_raises_value_error(limit: int) -> None:
    with pytest.raises(ValueError):
        make_service().list_tasks(limit=limit)


def test_limit_larger_than_result_returns_everything() -> None:
    assert len(make_service().list_tasks(limit=99)) == 3


def test_duplicate_task_id_is_rejected() -> None:
    service = make_service()
    with pytest.raises(ValueError):
        service.add_task(Task(id="T-1", title="dup", assignee="x", status="todo", priority=1, created_at="2026-02-01"))


def test_complete_task_marks_task_done() -> None:
    service = make_service()
    updated = service.complete_task("T-1")
    assert updated.status == "done"
    assert service.get_task("T-1").status == "done"


def test_returned_list_is_a_copy() -> None:
    service = make_service()
    tasks = service.list_tasks()
    tasks.clear()
    assert len(service.list_tasks()) == 3
