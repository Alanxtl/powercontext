"""Select the development context for one task.

Selection is retrieval driven: the task text is expanded into search terms,
the seekdb corpus answers with candidate specifications, code symbols and
tests, and the specification entries are then linked to the code symbols and
tests they mention. Nothing about a specific task is hard coded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sdd_context.corpus import Corpus, CorpusRow, QueryRecord

# A small bilingual glossary keeps Chinese task descriptions searchable in
# English specifications and code. It maps general engineering vocabulary, not
# any particular task.
_GLOSSARY: dict[str, tuple[str, ...]] = {
    "过滤": ("filter", "filtered"),
    "筛选": ("filter",),
    "负责人": ("assignee",),
    "指派": ("assignee",),
    "排序": ("sort", "order"),
    "状态": ("status",),
    "限制": ("limit",),
    "条数": ("limit",),
    "分页": ("page", "pagination"),
    "测试": ("test",),
    "用例": ("test",),
    "规格": ("spec", "specification"),
    "接口": ("api", "endpoint"),
    "参数": ("parameter", "argument"),
    "校验": ("validation", "validate"),
    "边界": ("boundary", "edge"),
    "错误": ("error",),
    "异常": ("exception",),
    "日志": ("log",),
    "缓存": ("cache",),
    "数据库": ("database",),
    "任务": ("task",),
    "列表": ("list",),
    "需求": ("requirement",),
    "新增": ("add", "create"),
    "增加": ("add",),
    "修改": ("modify", "change"),
    "修复": ("fix",),
    "支持": ("support",),
    "实现": ("implement",),
    "空": ("empty", "blank"),
    "大小写": ("case", "insensitive"),
    "空白": ("blank", "whitespace"),
}

_TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}|[一-鿿]+")
_IDENTIFIER_PATTERN = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]*)`|\b([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\b")

_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "add",
        "must",
        "should",
        "will",
        "using",
        "when",
        "there",
        "their",
        "code",
        "task",
    }
)


@dataclass(frozen=True)
class SelectedItem:
    """One selected development-context source."""

    kind: str
    identifier: str
    path: str
    title: str
    score: float
    reason: str
    payload: dict[str, Any] = field(default_factory=dict)

    def citation(self) -> dict[str, str]:
        """Return a machine-readable citation for the selected source."""

        return {
            "kind": self.kind,
            "id": self.identifier,
            "path": self.path,
            "title": self.title,
        }


@dataclass(frozen=True)
class Selection:
    """The complete development context selected for one task."""

    task: str
    search_terms: tuple[str, ...]
    specs: tuple[SelectedItem, ...]
    code: tuple[SelectedItem, ...]
    tests: tuple[SelectedItem, ...]
    files: tuple[SelectedItem, ...]
    queries: tuple[QueryRecord, ...]

    def citations(self) -> list[dict[str, str]]:
        """Return every citation of the selection."""

        return [item.citation() for item in (*self.specs, *self.code, *self.tests, *self.files)]


def _terms(task: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return (full-text terms, like terms) derived from a task description."""

    tokens = _TOKEN_PATTERN.findall(task)
    english: list[str] = []
    chinese: list[str] = []
    for token in tokens:
        if token.isascii():
            if token.lower() not in _STOPWORDS:
                english.append(token.lower())
            continue
        chinese.append(token)
        for key, values in _GLOSSARY.items():
            if key in token:
                english.extend(values)
    like_terms: list[str] = []
    for token in chinese:
        if len(token) >= 2:
            like_terms.append(token)
        for key in _GLOSSARY:
            if key in token:
                like_terms.append(key)
    like_terms.extend(english)
    seen: set[str] = set()
    unique_english = [term for term in english if not (term in seen or seen.add(term))]
    unique_like = [term for term in like_terms if not (term in seen or seen.add(term))]
    return tuple(unique_english), tuple(unique_like)


def _identifiers(texts: tuple[str, ...]) -> tuple[str, ...]:
    """Extract referenced code identifiers from specification and task text."""

    found: list[str] = []
    for text in texts:
        for backticked, dotted in _IDENTIFIER_PATTERN.findall(text):
            value = backticked or dotted
            if not value:
                continue
            tail = value.rsplit(".", 1)[-1]
            if len(tail) < 3 or tail.lower() in _STOPWORDS:
                continue
            if tail not in found:
                found.append(tail)
    return tuple(found)


def _score_item(row: CorpusRow, boost: float = 0.0) -> float:
    return round(row.score + boost, 6)


async def select_context(
    corpus: Corpus,
    task: str,
    *,
    spec_limit: int = 8,
    code_limit: int = 8,
    test_limit: int = 8,
) -> Selection:
    """Select specifications, code symbols and tests for one task."""

    english, like_terms = _terms(task)
    match_terms = " ".join(english)
    queries: list[QueryRecord] = []

    spec_rows, record = await corpus.search_specs(match_terms, like_terms, spec_limit)
    queries.append(record)
    code_rows, record = await corpus.search_code(match_terms, like_terms, code_limit)
    queries.append(record)
    test_rows, record = await corpus.search_tests(match_terms, like_terms, test_limit)
    queries.append(record)

    spec_texts = tuple(str(row.payload.get("content", "")) for row in spec_rows) + (task,)
    referenced = _identifiers(spec_texts)

    linked_code: list[CorpusRow] = []
    if referenced:
        rows, record = await corpus.find_symbols(referenced)
        queries.append(record)
        linked_code = rows
    linked_tests: list[CorpusRow] = []
    if referenced:
        rows, record = await corpus.find_tests_mentioning(referenced)
        queries.append(record)
        linked_tests = rows

    specs: list[SelectedItem] = []
    for row in spec_rows:
        status = str(row.payload.get("status", ""))
        # A specification that is not in effect yet describes behaviour a change
        # request has to implement, so it ranks above already effective entries.
        boost = 0.25 if "待实现" in status or "未实现" in status else 0.0
        specs.append(
            SelectedItem(
                kind="spec",
                identifier=row.identifier,
                path=row.path,
                title=row.title,
                score=_score_item(row, boost),
                reason=f"specification mentions {row.path}",
                payload=row.payload,
            )
        )
    specs.sort(key=lambda item: (-item.score, item.path))

    merged_code: dict[str, tuple[CorpusRow, float, str]] = {}
    for row in linked_code:
        merged_code[row.identifier] = (row, 1.0, "referenced by the selected specification")
    for row in code_rows:
        existing = merged_code.get(row.identifier)
        if existing is None:
            merged_code[row.identifier] = (row, 0.0, "matches the task description")
        else:
            merged_code[row.identifier] = (existing[0], existing[1] + 0.5, existing[2])

    code: list[SelectedItem] = []
    for row, boost, reason in merged_code.values():
        code.append(
            SelectedItem(
                kind="code",
                identifier=row.identifier,
                path=row.path,
                title=row.title,
                score=_score_item(row, boost),
                reason=reason,
                payload=row.payload,
            )
        )
    code.sort(key=lambda item: (-item.score, item.path))

    merged_tests: dict[str, tuple[CorpusRow, float, str]] = {}
    for row in linked_tests:
        merged_tests[row.identifier] = (row, 1.0, "verifies the selected code symbol")
    for row in test_rows:
        existing = merged_tests.get(row.identifier)
        if existing is None:
            merged_tests[row.identifier] = (row, 0.0, "matches the task description")
        else:
            merged_tests[row.identifier] = (existing[0], existing[1] + 0.5, existing[2])

    tests: list[SelectedItem] = []
    for row, boost, reason in merged_tests.values():
        tests.append(
            SelectedItem(
                kind="test",
                identifier=row.identifier,
                path=row.path,
                title=row.title,
                score=_score_item(row, boost),
                reason=reason,
                payload=row.payload,
            )
        )
    tests.sort(key=lambda item: (-item.score, item.path))

    # The assistant has to emit complete files, so the selected symbols are
    # accompanied by the full content of the files they live in.
    paths = tuple(sorted({item.path for item in code}))
    file_rows: list[CorpusRow] = []
    if paths:
        file_rows, record = await corpus.fetch_documents(paths)
        queries.append(record)
    files = tuple(
        SelectedItem(
            kind="file",
            identifier=row.identifier,
            path=row.path,
            title=row.title,
            score=row.score,
            reason="complete file of a selected code symbol",
            payload=row.payload,
        )
        for row in file_rows
    )

    return Selection(
        task=task,
        search_terms=english,
        specs=tuple(specs[:spec_limit]),
        code=tuple(code[:code_limit]),
        tests=tuple(tests[:test_limit]),
        files=files,
        queries=tuple(queries),
    )


__all__ = ["SelectedItem", "Selection", "select_context"]
