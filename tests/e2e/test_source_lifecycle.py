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

"""Evidence withdrawal preserves audit reads and recovers interrupted native writes."""

import asyncio
import sqlite3

import pytest
from sqlalchemy.exc import IntegrityError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import EmbeddingProfile, MemoryEntryInput
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    GetMemoryEntryRequest,
    GetTopicMemoryRequest,
    RememberMemoryRequest,
    SearchTopicMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.source_lifecycle import SourceLifecycleError, SourceLifecycleEvent
from powercontext.builtin.scope import ScopeDraft


class _Embeddings:
    profile = EmbeddingProfile(profile_id="lifecycle", model="test", dimension=2)

    async def embed(self, texts, /):
        return EmbeddingResult(vectors=tuple((1.0, 0.0) for _ in texts))


async def _evidence(runtime, key):
    scope = (await runtime.scopes.create(ScopeDraft(title=key, summary=key, idempotency_key=key))).scope_id
    source = await runtime.sources.for_scope(scope).capture(
        CaptureSource(source_id="origin", content="evidence", metadata={})
    )
    lifecycle = runtime.lifecycle.for_scope(scope)
    await lifecycle.register_source("root", source.source_ref, revision=1)
    memory = await runtime.memory.for_scope(scope).remember(
        RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text="supported evidence"),))
    )
    await lifecycle.register_memory("fact", memory.entry.citation, parents=("root",), policy="all")
    return scope, lifecycle, memory.entry.citation


def _event(available, sequence=1):
    return SourceLifecycleEvent(
        node_id="root", revision=1, sequence=sequence, available=available, reason="evidence state"
    )


def test_failed_native_write_replays_after_restart_without_extra_revisions(tmp_path):
    async def run():
        database = tmp_path / "lifecycle.db"
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"))
        async with open_builtin_runtime(config) as runtime:
            scope, lifecycle, citation = await _evidence(runtime, "recovery")
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "CREATE TRIGGER fail_lifecycle BEFORE INSERT ON pc_artifacts "
                    "WHEN NEW.family = 'memory' BEGIN SELECT RAISE(ABORT, 'interrupted lifecycle'); END"
                )
            with pytest.raises(IntegrityError, match="interrupted lifecycle"):
                await lifecycle.apply(_event(False))
        with sqlite3.connect(database) as connection:
            connection.execute("DROP TRIGGER fail_lifecycle")
        async with open_builtin_runtime(config) as runtime:
            lifecycle = runtime.lifecycle.for_scope(scope)
            assert await lifecycle.apply(_event(False)) == []
            memory = runtime.memory.for_scope(scope)
            before = await memory.list(include_inactive=True)
            assert before.entries[0].state == "inactive"
            assert await lifecycle.apply(_event(False)) == []
            assert (await memory.list(include_inactive=True)).memory_ref == before.memory_ref
            assert (await memory.get(GetMemoryEntryRequest(citation=citation))).entry.text == "supported evidence"
            await lifecycle.apply(_event(True, sequence=2))
            assert await lifecycle.resolve("fact", purpose="generation") == citation

    asyncio.run(run())


def test_withdrawn_topics_are_excluded_from_hybrid_candidates_but_exactly_readable():
    async def run():
        async with open_builtin_runtime(BuiltinConfig(), embedding_model=_Embeddings()) as runtime:
            scope, lifecycle, _ = await _evidence(runtime, "withdrawn")
            other_scope, other_lifecycle, _ = await _evidence(runtime, "independent")
            topic = await runtime.records.for_scope(scope).create_artifact(
                "topic-memory",
                ArtifactWrite(content={"title": "evidence", "summary": "supported evidence", "detail": "evidence"}),
            )
            ref = ArtifactRef(family="topic-memory", artifact_id=topic.artifact_id, revision=topic.revision)
            await lifecycle.register_topic("topic", ref, parents=("fact",), policy="all")
            assert (await runtime.topic_memory.for_scope(scope).search(SearchTopicMemoryRequest(query="evidence"))).hits
            await lifecycle.apply(_event(False))
            assert not (
                await runtime.topic_memory.for_scope(scope).search(SearchTopicMemoryRequest(query="evidence"))
            ).hits
            assert not await runtime.topic_memory.for_scope(scope).browse(limit=10)
            assert (
                await runtime.topic_memory.for_scope(scope).get(GetTopicMemoryRequest(artifact=ref))
            ).topic.as_ref() == ref
            assert await lifecycle.resolve("topic", purpose="audit") == ref
            with pytest.raises(SourceLifecycleError):
                await lifecycle.resolve("fact", purpose="generation")
            assert await other_lifecycle.resolve("fact", purpose="generation")
            assert (await runtime.memory.for_scope(other_scope).list()).entries
            await lifecycle.apply(_event(True, sequence=2))
            assert (await runtime.topic_memory.for_scope(scope).search(SearchTopicMemoryRequest(query="evidence"))).hits

    asyncio.run(run())
