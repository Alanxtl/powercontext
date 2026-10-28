"""Scan the business workspace into specification, code and test entries.

The scan is intentionally language-light: Python sources are parsed with the
standard library AST, so the module runs everywhere without extra parsers.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

_SPEC_ID_PATTERN = re.compile(r"^\s*[-*]\s*标识\s*[:：]\s*(?P<value>[A-Za-z0-9_.\-]+)", re.MULTILINE)
_SPEC_STATUS_PATTERN = re.compile(r"^\s*[-*]\s*状态\s*[:：]\s*(?P<value>\S+)", re.MULTILINE)
_SPEC_APPLIES_PATTERN = re.compile(r"^\s*[-*]\s*适用范围\s*[:：]\s*(?P<value>.+)$", re.MULTILINE)
_TITLE_PATTERN = re.compile(r"^#\s+(?P<value>.+)$", re.MULTILINE)


@dataclass(frozen=True)
class SpecEntry:
    """One specification entry loaded from ``spec/``."""

    spec_id: str
    path: str
    title: str
    status: str
    applies_to: str
    content: str


@dataclass(frozen=True)
class CodeSymbol:
    """One function or class defined in ``src/``."""

    symbol_id: str
    path: str
    name: str
    qualname: str
    kind: str
    start_line: int
    end_line: int
    signature: str
    docstring: str
    content: str


@dataclass(frozen=True)
class TestCase:
    """One test function loaded from ``tests/``."""

    test_id: str
    path: str
    name: str
    docstring: str
    content: str


@dataclass(frozen=True)
class FileDocument:
    """One complete file of the workspace."""

    document_id: str
    path: str
    kind: str
    content: str


@dataclass(frozen=True)
class Workspace:
    """Everything the module knows about the business workspace."""

    root: Path
    specs: tuple[SpecEntry, ...] = field(default_factory=tuple)
    symbols: tuple[CodeSymbol, ...] = field(default_factory=tuple)
    tests: tuple[TestCase, ...] = field(default_factory=tuple)
    documents: tuple[FileDocument, ...] = field(default_factory=tuple)

    def symbol_by_name(self, name: str) -> CodeSymbol | None:
        """Return the first symbol whose name or qualname ends with ``name``."""

        for symbol in self.symbols:
            if symbol.name == name or symbol.qualname.endswith(f".{name}"):
                return symbol
        return None


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _parse_spec(path: Path, root: Path) -> SpecEntry | None:
    text = path.read_text(encoding="utf-8")
    identifier = _SPEC_ID_PATTERN.search(text)
    if identifier is None:
        return None
    spec_id = identifier.group("value")
    title_match = _TITLE_PATTERN.search(text)
    title = title_match.group("value").strip() if title_match else spec_id
    if title.startswith(spec_id):
        title = title[len(spec_id) :].strip(" -—:：") or spec_id
    status_match = _SPEC_STATUS_PATTERN.search(text)
    applies_match = _SPEC_APPLIES_PATTERN.search(text)
    return SpecEntry(
        spec_id=spec_id,
        path=_relative(root, path),
        title=title,
        status=status_match.group("value").strip() if status_match else "未标注",
        applies_to=applies_match.group("value").strip() if applies_match else "",
        content=text,
    )


def _parse_code(path: Path, root: Path) -> list[CodeSymbol]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    symbols: list[CodeSymbol] = []
    relative = _relative(root, path)

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{prefix}{child.name}"
                arguments = ast.unparse(child.args)
                signature = f"def {child.name}({arguments})"
                if isinstance(child, ast.AsyncFunctionDef):
                    signature = f"async {signature}"
                body = "\n".join(lines[child.lineno - 1 : child.end_lineno])
                symbols.append(
                    CodeSymbol(
                        symbol_id=f"{relative}::{qualname}",
                        path=relative,
                        name=child.name,
                        qualname=qualname,
                        kind="function",
                        start_line=child.lineno,
                        end_line=child.end_lineno or child.lineno,
                        signature=signature,
                        docstring=(ast.get_docstring(child) or "").strip(),
                        content=body,
                    )
                )
                visit(child, f"{qualname}.")
            elif isinstance(child, ast.ClassDef):
                qualname = f"{prefix}{child.name}"
                body = "\n".join(lines[child.lineno - 1 : child.end_lineno])
                symbols.append(
                    CodeSymbol(
                        symbol_id=f"{relative}::{qualname}",
                        path=relative,
                        name=child.name,
                        qualname=qualname,
                        kind="class",
                        start_line=child.lineno,
                        end_line=child.end_lineno or child.lineno,
                        signature=f"class {child.name}",
                        docstring=(ast.get_docstring(child) or "").strip(),
                        content=body,
                    )
                )
                visit(child, f"{qualname}.")

    visit(tree, "")
    return symbols


def _parse_tests(path: Path, root: Path) -> list[TestCase]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    relative = _relative(root, path)
    cases: list[TestCase] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            body = "\n".join(lines[node.lineno - 1 : node.end_lineno])
            cases.append(
                TestCase(
                    test_id=f"{relative}::{node.name}",
                    path=relative,
                    name=node.name,
                    docstring=(ast.get_docstring(node) or "").strip(),
                    content=body,
                )
            )
    return cases


def scan_workspace(root: Path) -> Workspace:
    """Read specifications, code symbols and tests from the workspace root."""

    root = root.resolve()
    specs: list[SpecEntry] = []
    symbols: list[CodeSymbol] = []
    tests: list[TestCase] = []
    documents: list[FileDocument] = []

    spec_dir = root / "spec"
    if spec_dir.is_dir():
        for path in sorted(spec_dir.rglob("*.md")):
            entry = _parse_spec(path, root)
            if entry is not None:
                specs.append(entry)

    source_dir = root / "src"
    if source_dir.is_dir():
        for path in sorted(source_dir.rglob("*.py")):
            symbols.extend(_parse_code(path, root))
            documents.append(
                FileDocument(
                    document_id=_relative(root, path),
                    path=_relative(root, path),
                    kind="code",
                    content=path.read_text(encoding="utf-8"),
                )
            )

    test_dir = root / "tests"
    if test_dir.is_dir():
        for path in sorted(test_dir.rglob("*.py")):
            tests.extend(_parse_tests(path, root))
            documents.append(
                FileDocument(
                    document_id=_relative(root, path),
                    path=_relative(root, path),
                    kind="test",
                    content=path.read_text(encoding="utf-8"),
                )
            )

    return Workspace(
        root=root,
        specs=tuple(specs),
        symbols=tuple(symbols),
        tests=tuple(tests),
        documents=tuple(documents),
    )


__all__ = ["CodeSymbol", "FileDocument", "SpecEntry", "TestCase", "Workspace", "scan_workspace"]
