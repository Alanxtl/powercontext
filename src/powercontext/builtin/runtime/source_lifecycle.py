# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Persist exact evidence bindings and propagate scope-local availability."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import insert, select, update

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryCitation
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_NODES_TABLE
from powercontext.builtin.runtime.models import GetMemoryEntryRequest
from powercontext.builtin.sources import validate_scope_id
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime


class SourceLifecycleError(ValueError):
    """An invalid binding, lifecycle event, or use of unavailable evidence."""

    def __init__(self, code: str) -> None:
        messages = {
            "blank": "lifecycle identifiers and reasons must not be blank",
            "source-reference": "Source requires a reference and a positive lifecycle revision",
            "memory-reference": "Memory requires an exact citation",
            "topic-reference": "Topic requires an exact Topic Memory reference",
            "node-id": "Node id must be nonblank and at most 255 characters",
            "binding-conflict": "Node id is already bound to different evidence",
            "parents": "Parents must be distinct, previously registered nodes",
            "unreadable": "Registered evidence must exist and be readable",
            "event-revision": "Event must match a registered Source and lifecycle revision",
            "event-sequence": "Lifecycle events must increase sequence or replay identically",
            "stored-reference": "Invalid persisted Memory reference",
            "body-version": "Registered Memory body version has changed",
            "purpose": "Unknown evidence purpose",
            "unavailable": "Evidence is unknown or unavailable for generation",
            "policy": "Dependency policy must be all or any",
        }
        super().__init__(messages[code])


class SourceLifecycleEvent(BaseModel):
    """A strictly typed, replayable Source availability event."""

    model_config = ConfigDict(extra="forbid", strict=True)

    node_id: str = Field(min_length=1, max_length=255)
    revision: int = Field(gt=0)
    sequence: int = Field(gt=0)
    available: bool
    reason: str = Field(min_length=1)

    @field_validator("node_id", "reason")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise SourceLifecycleError("blank")
        return value


class _Binding(BaseModel):
    node_id: str
    kind: Literal["source", "memory", "topic"]
    reference: SourceRef | MemoryCitation | ArtifactRef
    parents: tuple[str, ...] = ()
    policy: Literal["all", "any"] = "all"
    revision: int | None = None
    event: SourceLifecycleEvent | None = None


class SourceLifecycleApplication:
    """Select the evidence lifecycle service for one Runtime scope."""

    def __init__(self, runtime: BuiltinRuntime, database: AsyncDatabase) -> None:
        self._runtime = runtime
        self._database = database

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycle:
        return ScopedSourceLifecycle(self._runtime, self._database, validate_scope_id(scope_id))


class ScopedSourceLifecycle:
    """Own an immutable evidence DAG and resumable native state transitions.

    Graph reads are bounded by the scope's registered evidence graph. Native
    writes commit before their availability projection: a failed propagation
    can be replayed against actual Memory state without creating extra revisions.
    """

    def __init__(self, runtime: BuiltinRuntime, database: AsyncDatabase, scope_id: str) -> None:
        self._runtime = runtime
        self._database = database
        self.scope_id = scope_id

    async def register_source(self, node_id: str, source_ref: SourceRef, *, revision: int) -> None:
        if type(revision) is not int or revision <= 0 or not isinstance(source_ref, SourceRef):
            raise SourceLifecycleError("source-reference")
        await self._register(_Binding(node_id=node_id, kind="source", reference=source_ref, revision=revision))

    async def register_memory(
        self, node_id: str, citation: MemoryCitation, *, parents: tuple[str, ...], policy: Literal["all", "any"]
    ) -> None:
        if policy not in {"all", "any"}:
            raise SourceLifecycleError("policy")
        if not isinstance(citation, MemoryCitation):
            raise SourceLifecycleError("memory-reference")
        await self._register(
            _Binding(node_id=node_id, kind="memory", reference=citation, parents=parents, policy=policy)
        )

    async def register_topic(
        self, node_id: str, artifact_ref: ArtifactRef, *, parents: tuple[str, ...], policy: Literal["all", "any"]
    ) -> None:
        if policy not in {"all", "any"}:
            raise SourceLifecycleError("policy")
        if not isinstance(artifact_ref, ArtifactRef) or artifact_ref.family != "topic-memory":
            raise SourceLifecycleError("topic-reference")
        await self._register(
            _Binding(node_id=node_id, kind="topic", reference=artifact_ref, parents=parents, policy=policy)
        )

    async def _load(self) -> tuple[dict[str, _Binding], dict[str, bool]]:
        table = SOURCE_LIFECYCLE_NODES_TABLE
        async with self._database.transaction() as connection:
            rows = (await connection.execute(select(table).where(table.c.scope_id == self.scope_id))).mappings().all()
        return (
            {str(row["node_id"]): _Binding.model_validate_json(row["payload"]) for row in rows},
            {str(row["node_id"]): bool(row["available"]) for row in rows},
        )

    async def _register(self, binding: _Binding) -> None:
        if not binding.node_id.strip() or len(binding.node_id) > 255:
            raise SourceLifecycleError("node-id")
        async with self._runtime._scope_operation(self.scope_id), self._runtime._locked(self.scope_id):
            graph, _ = await self._load()
            previous = graph.get(binding.node_id)
            if previous is not None:
                if previous.model_copy(update={"event": None}) != binding:
                    raise SourceLifecycleError("binding-conflict")
                await self._propagate(graph)
                return
            if binding.kind != "source" and (
                not binding.parents
                or len(set(binding.parents)) != len(binding.parents)
                or any(parent not in graph for parent in binding.parents)
            ):
                raise SourceLifecycleError("parents")
            try:
                records = self._runtime.records.for_scope(self.scope_id)
                if isinstance(binding.reference, SourceRef):
                    await records.get_source(binding.reference.source_type, binding.reference.source_id)
                elif isinstance(binding.reference, MemoryCitation):
                    await self._runtime.memory.for_scope(self.scope_id).get(
                        GetMemoryEntryRequest(citation=binding.reference)
                    )
                else:
                    ref = binding.reference
                    await records.get_artifact_revision(ref.family, ref.artifact_id, ref.revision)
            except Exception as error:
                raise SourceLifecycleError("unreadable") from error
            table = SOURCE_LIFECYCLE_NODES_TABLE
            ref = binding.reference
            artifact_id = (
                ref.memory_ref.artifact_id
                if isinstance(ref, MemoryCitation)
                else (ref.artifact_id if isinstance(ref, ArtifactRef) else None)
            )
            # Fail closed until the native state has been synchronized below.
            async with self._database.transaction() as connection:
                await connection.execute(
                    insert(table).values(
                        scope_id=self.scope_id,
                        node_id=binding.node_id,
                        kind=binding.kind,
                        artifact_id=artifact_id,
                        available=binding.kind == "source",
                        payload=binding.model_dump_json(),
                    )
                )
            graph[binding.node_id] = binding
            await self._propagate(graph)

    async def apply(self, event: SourceLifecycleEvent) -> list[str]:
        async with self._runtime._scope_operation(self.scope_id), self._runtime._locked(self.scope_id):
            graph, _ = await self._load()
            binding = graph.get(event.node_id)
            if binding is None or binding.kind != "source" or binding.revision != event.revision:
                raise SourceLifecycleError("event-revision")
            previous = binding.event
            if previous is not None and (
                event.sequence < previous.sequence or (event.sequence == previous.sequence and event != previous)
            ):
                raise SourceLifecycleError("event-sequence")
            binding = binding.model_copy(update={"event": event})
            graph[event.node_id] = binding
            table = SOURCE_LIFECYCLE_NODES_TABLE
            async with self._database.transaction() as connection:
                await connection.execute(
                    update(table)
                    .where(
                        table.c.scope_id == self.scope_id,
                        table.c.node_id == event.node_id,
                    )
                    .values(payload=binding.model_dump_json(), available=event.available)
                )
            return await self._propagate(graph)

    async def _propagate(self, graph: dict[str, _Binding]) -> list[str]:
        desired: dict[str, bool] = {}

        def available(node_id: str) -> bool:
            if node_id not in desired:
                binding = graph[node_id]
                if binding.kind == "source":
                    desired[node_id] = binding.event is None or binding.event.available
                else:
                    values = [available(parent) for parent in binding.parents]
                    desired[node_id] = all(values) if binding.policy == "all" else any(values)
            return desired[node_id]

        for node_id in graph:
            available(node_id)
        await self._synchronize_memories(graph, desired)
        table = SOURCE_LIFECYCLE_NODES_TABLE
        async with self._database.transaction() as connection:
            for node_id, active in desired.items():
                await connection.execute(
                    update(table)
                    .where(
                        table.c.scope_id == self.scope_id,
                        table.c.node_id == node_id,
                        table.c.available != active,
                    )
                    .values(available=active)
                )
        return sorted(node_id for node_id, active in desired.items() if active)

    async def _synchronize_memories(self, graph: dict[str, _Binding], desired: dict[str, bool]) -> None:
        memories = [binding for binding in graph.values() if binding.kind == "memory"]
        if memories:
            async with self._runtime._context(self.scope_id) as context:
                service = context.artifacts.memory
                current = await service.head(context.artifacts.memory_artifact_id)
                entries = {entry.entry_id: entry for entry in await service.entries(current)}
                states = {entry.entry_id: entry.state for entry in current.content.manifest.entries}
                for active in (False, True):
                    selected = []
                    for binding in memories:
                        citation = binding.reference
                        if not isinstance(citation, MemoryCitation):
                            raise SourceLifecycleError("stored-reference")
                        entry = entries.get(citation.entry_id)
                        if entry is None or entry.entry_version_id != citation.entry_version_id:
                            raise SourceLifecycleError("body-version")
                        if desired[binding.node_id] == active and (states[entry.entry_id] == "active") != active:
                            selected.append(entry)
                    if selected:
                        operation = service.reactivate if active else service.forget
                        current = await operation(current, entries=selected, reason="Source lifecycle propagation")

    async def resolve(self, node_id: str, *, purpose: str) -> SourceRef | MemoryCitation | ArtifactRef:
        if purpose not in {"generation", "audit"}:
            raise SourceLifecycleError("purpose")
        async with self._runtime._scope_operation(self.scope_id):
            graph, states = await self._load()
            if node_id not in graph or (purpose == "generation" and not states[node_id]):
                raise SourceLifecycleError("unavailable")
            return graph[node_id].reference
