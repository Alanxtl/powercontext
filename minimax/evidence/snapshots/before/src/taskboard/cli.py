"""Command line demo for the task board service."""

from __future__ import annotations

import argparse
import json

from taskboard.model import Task
from taskboard.service import TaskBoardService


def build_demo_service() -> TaskBoardService:
    """Return a small board used by the demo command."""

    service = TaskBoardService()
    service.add_task(Task(id="T-1", title="write spec", assignee="Alice", status="todo", priority=3, created_at="2026-03-01"))
    service.add_task(Task(id="T-2", title="fix parser", assignee="bob", status="done", priority=5, created_at="2026-03-02"))
    service.add_task(Task(id="T-3", title="review docs", assignee=None, status="todo", priority=3, created_at="2026-03-03"))
    return service


def main() -> int:
    parser = argparse.ArgumentParser(description="task board demo")
    parser.add_argument("--status", default=None)
    parser.add_argument("--assignee", default=None)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    service = build_demo_service()
    filters: dict[str, object] = {}
    if args.status is not None:
        filters["status"] = args.status
    if args.assignee is not None:
        filters["assignee"] = args.assignee
    if args.limit is not None:
        filters["limit"] = args.limit
    tasks = service.list_tasks(**filters)
    print(json.dumps([task.__dict__ for task in tasks], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
