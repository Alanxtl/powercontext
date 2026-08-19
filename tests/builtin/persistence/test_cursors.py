from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence import GenerationConflictError
from powercontext.builtin.persistence.cursors import SourceCursorRepository, StoredSourceCursor
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import SHARED_TABLES
from powercontext.builtin.sources import SourceCursor
from tests.builtin.persistence.contract import repository_profile


def test_source_cursor_uses_generation_compare_and_swap() -> None:
    async def scenario() -> None:
        async with repository_profile() as (profile, repositories):  # noqa: SIM117
            async with profile.database.transaction() as connection:
                first = await repositories.cursors.save(
                    connection,
                    "scope-a",
                    "memory-source-window",
                    SourceCursor(sequence=1),
                    expected_generation=None,
                )
                second = await repositories.cursors.save(
                    connection,
                    "scope-a",
                    "memory-source-window",
                    SourceCursor(sequence=2),
                    expected_generation=first.generation,
                )

                assert first.generation == 1
                assert second.generation == 2
                assert await repositories.cursors.load(connection, "scope-a", "memory-source-window") == second
                with pytest.raises(GenerationConflictError) as error:
                    await repositories.cursors.save(
                        connection,
                        "scope-a",
                        "memory-source-window",
                        SourceCursor(sequence=3),
                        expected_generation=first.generation,
                    )
                assert error.value.actual == 2

    asyncio.run(scenario())


def test_source_cursor_initial_creation_translates_a_racing_insert() -> None:
    class StaleInitialReadRepository(SourceCursorRepository):
        def __init__(self) -> None:
            self._first_read = True

        async def load(
            self,
            connection: AsyncConnection,
            scope_id: str,
            binding_name: str,
            /,
            *,
            for_update: bool = False,
        ) -> StoredSourceCursor | None:
            if self._first_read and not for_update:
                self._first_read = False
                return None
            return await super().load(connection, scope_id, binding_name, for_update=for_update)

    async def scenario() -> None:
        async with repository_profile() as (profile, _):  # noqa: SIM117
            async with profile.database.transaction() as connection:
                await SourceCursorRepository().save(
                    connection,
                    "scope-a",
                    "memory-source-window",
                    SourceCursor(sequence=1),
                    expected_generation=None,
                )

                repository = StaleInitialReadRepository()
                with pytest.raises(GenerationConflictError) as error:
                    await repository.save(
                        connection,
                        "scope-a",
                        "memory-source-window",
                        SourceCursor(sequence=2),
                        expected_generation=None,
                    )
                assert error.value.actual == 1

    asyncio.run(scenario())


def test_source_cursor_initial_creation_normalizes_unique_conflict(tmp_path: Path) -> None:
    async def scenario() -> None:
        url = f"sqlite+aiosqlite:///{tmp_path / 'cursor-race.db'}"
        config = SQLiteConfig(url=url)
        repository = SourceCursorRepository()

        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as first_profile:  # noqa: SIM117
            async with first_profile.database.transaction() as first_connection:
                await repository.save(
                    first_connection,
                    "scope-a",
                    "memory-source-window",
                    SourceCursor(sequence=1),
                    expected_generation=None,
                )

        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as second_profile:  # noqa: SIM117
            async with second_profile.database.transaction() as second_connection:
                with pytest.raises(GenerationConflictError) as error:
                    await repository.save(
                        second_connection,
                        "scope-a",
                        "memory-source-window",
                        SourceCursor(sequence=2),
                        expected_generation=None,
                    )
                assert error.value.actual == 1

    asyncio.run(scenario())
