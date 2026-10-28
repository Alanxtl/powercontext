"""Engineering corpus stored in seekdb.

The corpus tables live in the same seekdb database that PowerContext uses, so
one embedded instance holds both the engineering materials and the task state.
Every retrieval query is logged into ``sdd_query_log`` as database evidence.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from sdd_context.workspace import CodeSymbol, FileDocument, SpecEntry, TestCase

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS sdd_spec (
        spec_id VARCHAR(96) PRIMARY KEY,
        path VARCHAR(512) NOT NULL,
        title VARCHAR(512) NOT NULL,
        status VARCHAR(64) NOT NULL,
        applies_to VARCHAR(512) NOT NULL,
        content MEDIUMTEXT NOT NULL,
        FULLTEXT INDEX ft_sdd_spec (content)
    ) ENGINE=InnoDB
    """,
    """
    CREATE TABLE IF NOT EXISTS sdd_code_symbol (
        symbol_id VARCHAR(512) PRIMARY KEY,
        path VARCHAR(512) NOT NULL,
        name VARCHAR(256) NOT NULL,
        qualname VARCHAR(512) NOT NULL,
        kind VARCHAR(32) NOT NULL,
        start_line INT NOT NULL,
        end_line INT NOT NULL,
        signature VARCHAR(1024) NOT NULL,
        docstring MEDIUMTEXT NOT NULL,
        content MEDIUMTEXT NOT NULL,
        FULLTEXT INDEX ft_sdd_code (content)
    ) ENGINE=InnoDB
    """,
    """
    CREATE TABLE IF NOT EXISTS sdd_test_case (
        test_id VARCHAR(512) PRIMARY KEY,
        path VARCHAR(512) NOT NULL,
        name VARCHAR(256) NOT NULL,
        docstring MEDIUMTEXT NOT NULL,
        content MEDIUMTEXT NOT NULL,
        FULLTEXT INDEX ft_sdd_test (content)
    ) ENGINE=InnoDB
    """,
    """
    CREATE TABLE IF NOT EXISTS sdd_document (
        document_id VARCHAR(512) PRIMARY KEY,
        path VARCHAR(512) NOT NULL,
        kind VARCHAR(32) NOT NULL,
        content MEDIUMTEXT NOT NULL,
        FULLTEXT INDEX ft_sdd_document (content)
    ) ENGINE=InnoDB
    """,
    """
    CREATE TABLE IF NOT EXISTS sdd_query_log (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        ran_at VARCHAR(40) NOT NULL,
        purpose VARCHAR(160) NOT NULL,
        sql_text MEDIUMTEXT NOT NULL,
        params MEDIUMTEXT NOT NULL,
        row_count INT NOT NULL,
        elapsed_ms INT NOT NULL
    ) ENGINE=InnoDB
    """,
)


@dataclass(frozen=True)
class CorpusRow:
    """One retrieved corpus row."""

    kind: str
    identifier: str
    path: str
    title: str
    score: float
    payload: dict[str, Any]


@dataclass(frozen=True)
class QueryRecord:
    """One executed database query, kept as database evidence."""

    purpose: str
    sql_text: str
    params: dict[str, Any]
    row_count: int
    elapsed_ms: float


class Corpus:
    """Read and write the engineering corpus inside seekdb."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ensure_schema(self) -> None:
        """Create the corpus tables when they do not exist yet."""

        async with self._engine.begin() as connection:
            for statement in _SCHEMA_STATEMENTS:
                await connection.execute(text(statement))

    async def replace_entries(
        self,
        specs: tuple[SpecEntry, ...],
        symbols: tuple[CodeSymbol, ...],
        tests: tuple[TestCase, ...],
        documents: tuple[FileDocument, ...] = (),
    ) -> dict[str, int]:
        """Replace the corpus with the current workspace content."""

        await self.ensure_schema()
        async with self._engine.begin() as connection:
            for table in ("sdd_spec", "sdd_code_symbol", "sdd_test_case", "sdd_document"):
                await connection.execute(text(f"DELETE FROM {table}"))
            for spec in specs:
                await connection.execute(
                    text(
                        "INSERT INTO sdd_spec (spec_id, path, title, status, applies_to, content) "
                        "VALUES (:spec_id, :path, :title, :status, :applies_to, :content)"
                    ),
                    {
                        "spec_id": spec.spec_id,
                        "path": spec.path,
                        "title": spec.title,
                        "status": spec.status,
                        "applies_to": spec.applies_to,
                        "content": spec.content,
                    },
                )
            for symbol in symbols:
                await connection.execute(
                    text(
                        "INSERT INTO sdd_code_symbol "
                        "(symbol_id, path, name, qualname, kind, start_line, end_line, signature, docstring, content) "
                        "VALUES (:symbol_id, :path, :name, :qualname, :kind, :start_line, :end_line, "
                        ":signature, :docstring, :content)"
                    ),
                    {
                        "symbol_id": symbol.symbol_id,
                        "path": symbol.path,
                        "name": symbol.name,
                        "qualname": symbol.qualname,
                        "kind": symbol.kind,
                        "start_line": symbol.start_line,
                        "end_line": symbol.end_line,
                        "signature": symbol.signature,
                        "docstring": symbol.docstring,
                        "content": symbol.content,
                    },
                )
            for case in tests:
                await connection.execute(
                    text(
                        "INSERT INTO sdd_test_case (test_id, path, name, docstring, content) "
                        "VALUES (:test_id, :path, :name, :docstring, :content)"
                    ),
                    {
                        "test_id": case.test_id,
                        "path": case.path,
                        "name": case.name,
                        "docstring": case.docstring,
                        "content": case.content,
                    },
                )
            for document in documents:
                await connection.execute(
                    text(
                        "INSERT INTO sdd_document (document_id, path, kind, content) "
                        "VALUES (:document_id, :path, :kind, :content)"
                    ),
                    {
                        "document_id": document.document_id,
                        "path": document.path,
                        "kind": document.kind,
                        "content": document.content,
                    },
                )
        return {
            "specs": len(specs),
            "symbols": len(symbols),
            "tests": len(tests),
            "documents": len(documents),
        }

    async def fetch_documents(self, paths: tuple[str, ...]) -> tuple[list[CorpusRow], QueryRecord]:
        """Return the complete file contents of the given workspace paths."""

        if not paths:
            return [], QueryRecord("fetch-documents", "", {}, 0, 0.0)
        started = time.perf_counter()
        placeholders = ", ".join(f":path_{index}" for index in range(len(paths)))
        params = {f"path_{index}": path for index, path in enumerate(paths)}
        sql_text = (
            "SELECT document_id, path, kind, content, 0.0 AS score FROM sdd_document "
            f"WHERE path IN ({placeholders})"
        )
        async with self._engine.begin() as connection:
            result = await connection.execute(text(sql_text), params)
            rows = list(result.fetchall())
            elapsed_ms = (time.perf_counter() - started) * 1000
            await self._log_query(connection, "fetch-documents", sql_text, params, len(rows), elapsed_ms)
        return (
            [
                CorpusRow(
                    kind="file",
                    identifier=row[0],
                    path=row[1],
                    title=f"{row[1]}（{row[2]}）",
                    score=float(row[4]),
                    payload={"kind": row[2], "content": row[3]},
                )
                for row in rows
            ],
            QueryRecord("fetch-documents", sql_text, params, len(rows), elapsed_ms),
        )

    async def _search(
        self,
        connection: AsyncConnection,
        purpose: str,
        table: str,
        columns: str,
        like_columns: tuple[str, ...],
        match_terms: str,
        like_terms: tuple[str, ...],
        limit: int,
    ) -> tuple[list[CorpusRow], QueryRecord]:
        """Run one full-text retrieval and log it as database evidence."""

        started = time.perf_counter()
        sql_text = ""
        rows: list[Any] = []
        if match_terms:
            sql_text = (
                f"SELECT {columns}, MATCH(content) AGAINST (:match IN BOOLEAN MODE) AS score "
                f"FROM {table} WHERE MATCH(content) AGAINST (:match IN BOOLEAN MODE) "
                f"ORDER BY score DESC LIMIT :limit"
            )
            try:
                result = await connection.execute(
                    text(sql_text), {"match": match_terms, "limit": limit}
                )
                rows = list(result.fetchall())
            except Exception:  # noqa: BLE001 - a full-text miss must not break the run
                rows = []
        if not rows and like_terms:
            conditions = " OR ".join(
                f"{column} LIKE :term_{index}" for index, column in enumerate(like_columns)
            )
            sql_text = f"SELECT {columns}, 0.0 AS score FROM {table} WHERE {conditions} LIMIT :limit"
            params: dict[str, Any] = {"limit": limit}
            for index, term in enumerate(like_terms):
                params[f"term_{index}"] = f"%{term}%"
            result = await connection.execute(text(sql_text), params)
            rows = list(result.fetchall())
        elapsed_ms = (time.perf_counter() - started) * 1000
        await self._log_query(connection, purpose, sql_text, {"match": match_terms, "limit": limit}, len(rows), elapsed_ms)
        return rows, QueryRecord(
            purpose=purpose,
            sql_text=sql_text,
            params={"match": match_terms, "limit": limit},
            row_count=len(rows),
            elapsed_ms=elapsed_ms,
        )

    async def _log_query(
        self,
        connection: AsyncConnection,
        purpose: str,
        sql_text: str,
        params: dict[str, Any],
        row_count: int,
        elapsed_ms: float,
    ) -> None:
        """Persist one query record so the database usage stays auditable."""

        await connection.execute(
            text(
                "INSERT INTO sdd_query_log (ran_at, purpose, sql_text, params, row_count, elapsed_ms) "
                "VALUES (:ran_at, :purpose, :sql_text, :params, :row_count, :elapsed_ms)"
            ),
            {
                "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "purpose": purpose,
                "sql_text": sql_text,
                "params": repr(params),
                "row_count": row_count,
                "elapsed_ms": round(elapsed_ms, 3),
            },
        )

    async def search_specs(self, match_terms: str, like_terms: tuple[str, ...], limit: int) -> tuple[list[CorpusRow], QueryRecord]:
        """Retrieve specification entries relevant to the task."""

        async with self._engine.begin() as connection:
            rows, record = await self._search(
                connection,
                "select-specifications",
                "sdd_spec",
                "spec_id, path, title, status, applies_to, content",
                ("content", "title"),
                match_terms,
                like_terms,
                limit,
            )
        return (
            [
                CorpusRow(
                    kind="spec",
                    identifier=row[0],
                    path=row[1],
                    title=row[2],
                    score=float(row[6]),
                    payload={"status": row[3], "applies_to": row[4], "content": row[5]},
                )
                for row in rows
            ],
            record,
        )

    async def search_code(self, match_terms: str, like_terms: tuple[str, ...], limit: int) -> tuple[list[CorpusRow], QueryRecord]:
        """Retrieve code symbols relevant to the task."""

        async with self._engine.begin() as connection:
            rows, record = await self._search(
                connection,
                "select-code-symbols",
                "sdd_code_symbol",
                "symbol_id, path, name, qualname, kind, start_line, end_line, signature, docstring, content",
                ("content", "docstring", "signature"),
                match_terms,
                like_terms,
                limit,
            )
        return (
            [
                CorpusRow(
                    kind="code",
                    identifier=row[0],
                    path=row[1],
                    title=f"{row[3]} ({row[2]})",
                    score=float(row[10]),
                    payload={
                        "name": row[2],
                        "qualname": row[3],
                        "kind": row[4],
                        "start_line": row[5],
                        "end_line": row[6],
                        "signature": row[7],
                        "docstring": row[8],
                        "content": row[9],
                    },
                )
                for row in rows
            ],
            record,
        )

    async def search_tests(self, match_terms: str, like_terms: tuple[str, ...], limit: int) -> tuple[list[CorpusRow], QueryRecord]:
        """Retrieve test cases relevant to the task."""

        async with self._engine.begin() as connection:
            rows, record = await self._search(
                connection,
                "select-test-cases",
                "sdd_test_case",
                "test_id, path, name, docstring, content",
                ("content", "name", "docstring"),
                match_terms,
                like_terms,
                limit,
            )
        return (
            [
                CorpusRow(
                    kind="test",
                    identifier=row[0],
                    path=row[1],
                    title=row[2],
                    score=float(row[5]),
                    payload={"docstring": row[3], "content": row[4]},
                )
                for row in rows
            ],
            record,
        )

    async def fetch_spec(self, spec_id: str) -> dict[str, Any] | None:
        """Return one specification row by identifier."""

        async with self._engine.begin() as connection:
            result = await connection.execute(
                text("SELECT spec_id, path, title, status, applies_to, content FROM sdd_spec WHERE spec_id = :spec_id"),
                {"spec_id": spec_id},
            )
            row = result.fetchone()
        if row is None:
            return None
        return {
            "spec_id": row[0],
            "path": row[1],
            "title": row[2],
            "status": row[3],
            "applies_to": row[4],
            "content": row[5],
        }

    async def find_symbols(self, names: tuple[str, ...]) -> tuple[list[CorpusRow], QueryRecord]:
        """Return the code symbols whose name is in ``names``."""

        if not names:
            return [], QueryRecord("find-code-symbols", "", {}, 0, 0.0)
        started = time.perf_counter()
        placeholders = ", ".join(f":name_{index}" for index in range(len(names)))
        params = {f"name_{index}": name for index, name in enumerate(names)}
        sql_text = (
            "SELECT symbol_id, path, name, qualname, kind, start_line, end_line, signature, docstring, content, 0.0 AS score "
            f"FROM sdd_code_symbol WHERE name IN ({placeholders})"
        )
        async with self._engine.begin() as connection:
            result = await connection.execute(text(sql_text), params)
            rows = list(result.fetchall())
            elapsed_ms = (time.perf_counter() - started) * 1000
            await self._log_query(connection, "find-code-symbols", sql_text, params, len(rows), elapsed_ms)
        return (
            [
                CorpusRow(
                    kind="code",
                    identifier=row[0],
                    path=row[1],
                    title=f"{row[3]} ({row[2]})",
                    score=float(row[10]),
                    payload={
                        "name": row[2],
                        "qualname": row[3],
                        "kind": row[4],
                        "start_line": row[5],
                        "end_line": row[6],
                        "signature": row[7],
                        "docstring": row[8],
                        "content": row[9],
                    },
                )
                for row in rows
            ],
            QueryRecord("find-code-symbols", sql_text, params, len(rows), elapsed_ms),
        )

    async def find_tests_mentioning(self, names: tuple[str, ...]) -> tuple[list[CorpusRow], QueryRecord]:
        """Return the test cases whose body mentions one of ``names``."""

        if not names:
            return [], QueryRecord("find-related-tests", "", {}, 0, 0.0)
        started = time.perf_counter()
        conditions = " OR ".join(f"content LIKE :name_{index}" for index in range(len(names)))
        params = {f"name_{index}": f"%{name}%" for index, name in enumerate(names)}
        sql_text = (
            "SELECT test_id, path, name, docstring, content, 0.0 AS score "
            f"FROM sdd_test_case WHERE {conditions}"
        )
        async with self._engine.begin() as connection:
            result = await connection.execute(text(sql_text), params)
            rows = list(result.fetchall())
            elapsed_ms = (time.perf_counter() - started) * 1000
            await self._log_query(connection, "find-related-tests", sql_text, params, len(rows), elapsed_ms)
        return (
            [
                CorpusRow(
                    kind="test",
                    identifier=row[0],
                    path=row[1],
                    title=row[2],
                    score=float(row[5]),
                    payload={"docstring": row[3], "content": row[4]},
                )
                for row in rows
            ],
            QueryRecord("find-related-tests", sql_text, params, len(rows), elapsed_ms),
        )

    async def query_log(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the recorded database queries, newest first."""

        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    "SELECT ran_at, purpose, sql_text, params, row_count, elapsed_ms "
                    "FROM sdd_query_log ORDER BY id DESC LIMIT :limit"
                ),
                {"limit": limit},
            )
            rows = result.fetchall()
        return [
            {
                "ran_at": row[0],
                "purpose": row[1],
                "sql": row[2],
                "params": row[3],
                "row_count": row[4],
                "elapsed_ms": row[5],
            }
            for row in rows
        ]


__all__ = ["Corpus", "CorpusRow", "QueryRecord"]
