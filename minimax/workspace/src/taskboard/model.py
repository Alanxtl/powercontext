"""Task board domain model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

TASK_STATUSES: Final[tuple[str, ...]] = ("todo", "done")


@dataclass(frozen=True)
class Task:
    """One task card on the board."""

    id: str
    title: str
    assignee: str | None
    status: str
    priority: int
    created_at: str

    def __post_init__(self) -> None:
        if self.status not in TASK_STATUSES:
            raise ValueError(f"unsupported task status: {self.status!r}")
        if not self.id.strip():
            raise ValueError("task id must not be empty")
