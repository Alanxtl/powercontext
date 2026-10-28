"""Durable task state stored with PowerContext.

The state is written into the same seekdb database that holds the engineering
corpus, so a restarted module reads the task progress back and continues.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from sdd_context.config import Settings
from sdd_context.workspace import Workspace

_STATE_KIND = "sdd-task-state"


def _jsonable(value: Any) -> Any:
    """Convert tuples into lists and models into plain data for strict JSON."""

    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    return value


@dataclass(frozen=True)
class StateSnapshot:
    """One saved task state."""

    task_id: str
    source_id: str
    position: int
    payload: dict[str, Any]


class StateStore:
    """Persist and reload task progress through the PowerContext runtime."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._runtime: Any = None
        self._scope_id: str | None = None

    async def __aenter__(self) -> StateStore:
        from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime

        self._settings.state_dir.mkdir(parents=True, exist_ok=True)
        if self._settings.database_kind == "seekdb":
            from powercontext.builtin.persistence.seekdb import SeekDBConfig

            self._settings.seekdb_path.mkdir(parents=True, exist_ok=True)
            database = SeekDBConfig(path=self._settings.seekdb_path)
        else:
            from powercontext.builtin.persistence.sqlite import SQLiteConfig

            database = SQLiteConfig(url=f"sqlite+aiosqlite:///{self._settings.sqlite_path.as_posix()}")
        self._stack = open_builtin_runtime(BuiltinConfig(database=database))
        self._runtime = await self._stack.__aenter__()
        self._scope_id = await self._resolve_scope()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._stack.__aexit__(*exc_info)  # type: ignore[misc]
        self._runtime = None

    @property
    def runtime(self) -> Any:
        """Return the running PowerContext runtime."""

        if self._runtime is None:
            raise RuntimeError("the state store is not open")
        return self._runtime

    @property
    def engine(self) -> AsyncEngine:
        """Return the SQLAlchemy engine of the active database."""

        return self.runtime.primary_database.engine

    @property
    def scope_id(self) -> str:
        """Return the scope that stores the module state."""

        if self._scope_id is None:
            raise RuntimeError("the state store is not open")
        return self._scope_id

    async def _resolve_scope(self) -> str:
        """Reuse one scope across runs so state survives a restart."""

        from powercontext.builtin.scope import ScopeDraft

        scopes = await self.runtime.scopes.list()
        for scope in scopes:
            if getattr(scope, "title", "") == self._settings.scope_title:
                return scope.scope_id
        draft = ScopeDraft(
            title=self._settings.scope_title,
            summary=self._settings.scope_summary,
            idempotency_key=self._settings.scope_key,
        )
        created = await self.runtime.scopes.create(draft)
        return created.scope_id

    async def capture_materials(self, workspace: Workspace) -> int:
        """Capture the engineering materials into PowerContext as Sources."""

        from powercontext.builtin.runtime import CaptureSource

        captured = 0
        scoped = self.runtime.sources.for_scope(self.scope_id)
        for spec in workspace.specs:
            try:
                await scoped.capture(
                    CaptureSource(
                        source_id=f"spec:{spec.spec_id}",
                        content=spec.content,
                        metadata={"kind": "spec", "path": spec.path, "spec_id": spec.spec_id},
                    )
                )
                captured += 1
            except Exception:  # noqa: BLE001 - a conflicting capture keeps the older revision
                continue
        for symbol in workspace.symbols:
            try:
                await scoped.capture(
                    CaptureSource(
                        source_id=f"code:{symbol.symbol_id}",
                        content=symbol.content,
                        metadata={"kind": "code", "path": symbol.path, "symbol": symbol.qualname},
                    )
                )
                captured += 1
            except Exception:  # noqa: BLE001
                continue
        for case in workspace.tests:
            try:
                await scoped.capture(
                    CaptureSource(
                        source_id=f"test:{case.test_id}",
                        content=case.content,
                        metadata={"kind": "test", "path": case.path, "test": case.name},
                    )
                )
                captured += 1
            except Exception:  # noqa: BLE001
                continue
        return captured

    async def save_state(self, task_id: str, payload: dict[str, Any]) -> str:
        """Persist one task state document and return its Source id."""

        document = {"kind": _STATE_KIND, "task_id": task_id, "payload": _jsonable(payload)}
        record = await self.runtime.records.for_scope(self.scope_id).create_source("content", document)
        return record.source_id

    async def load_state(self, task_id: str) -> StateSnapshot | None:
        """Return the newest saved state for one task."""

        scoped = self.runtime.records.for_scope(self.scope_id)
        cursor: str | None = None
        best: StateSnapshot | None = None
        while True:
            page = await scoped.list_sources(limit=100, cursor=cursor)
            for item in page.items:
                content = item.content
                if not isinstance(content, dict) or content.get("kind") != _STATE_KIND:
                    continue
                if content.get("task_id") != task_id:
                    continue
                payload = content.get("payload")
                if not isinstance(payload, dict):
                    continue
                if best is None or item.position > best.position:
                    best = StateSnapshot(
                        task_id=task_id,
                        source_id=item.source_id,
                        position=item.position,
                        payload=payload,
                    )
            cursor = page.next_cursor
            if not cursor:
                break
        return best

    async def record_contract(
        self,
        *,
        source_id: str,
        objective: str,
        in_scope: tuple[str, ...],
        completion_criteria: tuple[str, ...],
        facts: tuple[str, ...] = (),
    ) -> None:
        """Record the work contract of the current task."""

        from powercontext.builtin.work.models import CreateWorkContract, WorkClaim, WorkContract

        contract = WorkContract(
            objective=objective,
            facts=tuple(WorkClaim(text=fact) for fact in facts),
            in_scope=in_scope,
            completion_criteria=completion_criteria,
        )
        await self.runtime.work.for_scope(self.scope_id).create_contract(
            CreateWorkContract(source_id=source_id, contract=contract)
        )

    async def record_outcome(
        self,
        *,
        source_id: str,
        objective: str,
        status: str,
        summary: str,
        observations: tuple[str, ...],
        checks: tuple[tuple[str, str, str], ...] = (),
    ) -> None:
        """Record the outcome of one work attempt."""

        from powercontext.builtin.work.models import RecordTaskOutcome, TaskCheck, TaskOutcome, WorkClaim

        outcome = TaskOutcome(
            objective=objective,
            status=status,  # type: ignore[arg-type]
            summary=summary,
            observations=tuple(WorkClaim(text=item) for item in observations),
            checks=tuple(
                TaskCheck(name=name, status=check_status, details=details)  # type: ignore[arg-type]
                for name, check_status, details in checks
            ),
        )
        await self.runtime.work.for_scope(self.scope_id).record_outcome(
            RecordTaskOutcome(source_id=source_id, outcome=outcome)
        )

    async def continuity(self) -> dict[str, Any]:
        """Return the projected work-continuity view of the scope."""

        projection = await self.runtime.work.for_scope(self.scope_id).continuity()
        return {
            "scope_id": projection.scope_id,
            "total_event_count": projection.total_event_count,
            "events": [
                {
                    "kind": event.kind,
                    "status": event.status,
                    "summary": event.summary,
                    "source_ref": _jsonable(event.source_ref),
                }
                for event in projection.events
            ],
            "coverage": {
                "contract_records": projection.coverage.contract_records,
                "outcome_records": projection.coverage.outcome_records,
                "transfer_state": projection.coverage.transfer_state,
                "outcome_state": projection.coverage.outcome_state,
            },
        }


@asynccontextmanager
async def open_state_store(settings: Settings):
    """Open the state store as an async context manager."""

    store = StateStore(settings)
    async with store as opened:
        yield opened


__all__ = ["StateSnapshot", "StateStore", "open_state_store"]
