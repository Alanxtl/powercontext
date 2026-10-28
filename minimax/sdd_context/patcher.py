"""Apply the code changes produced by the coding assistant."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path

_FILE_BLOCK = re.compile(
    r"FILE:\s*(?P<path>[^\r\n]+)\r?\n(?P<body>```[^\r\n]*\r?\n.*?```)",
    re.DOTALL,
)


@dataclass(frozen=True)
class FileChange:
    """One file rewritten by the assistant."""

    path: str
    before: str
    after: str
    created: bool

    @property
    def diff(self) -> str:
        """Return the unified diff between the old and the new content."""

        before = self.before.splitlines(keepends=True)
        after = self.after.splitlines(keepends=True)
        return "".join(
            difflib.unified_diff(
                before,
                after,
                fromfile=f"a/{self.path}",
                tofile=f"b/{self.path}",
                n=3,
            )
        )


def parse_file_blocks(text: str) -> list[tuple[str, str]]:
    """Extract ``FILE:`` blocks from the assistant answer."""

    blocks: list[tuple[str, str]] = []
    for match in _FILE_BLOCK.finditer(text):
        path = match.group("path").strip().strip("`").strip()
        body = match.group("body")
        lines = body.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines)
        if content and not content.endswith("\n"):
            content += "\n"
        blocks.append((path, content))
    return blocks


def _safe_target(workspace: Path, relative: str) -> Path:
    """Resolve one workspace-relative path and refuse to escape the workspace."""

    candidate = (workspace / relative).resolve()
    root = workspace.resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError(f"refusing to write outside the workspace: {relative!r}")
    return candidate


def preview_file_changes(workspace: Path, changes: list[tuple[str, str]]) -> list[FileChange]:
    """Compute the diff of the proposed changes without touching the workspace."""

    previewed: list[FileChange] = []
    for relative, content in changes:
        target = _safe_target(workspace, relative)
        created = not target.exists()
        before = "" if created else target.read_text(encoding="utf-8")
        previewed.append(FileChange(path=relative, before=before, after=content, created=created))
    return previewed


def apply_file_changes(workspace: Path, changes: list[tuple[str, str]]) -> list[FileChange]:
    """Write the parsed file contents into the workspace."""

    applied: list[FileChange] = []
    for relative, content in changes:
        target = _safe_target(workspace, relative)
        created = not target.exists()
        before = "" if created else target.read_text(encoding="utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        applied.append(FileChange(path=relative, before=before, after=content, created=created))
    return applied


__all__ = ["FileChange", "apply_file_changes", "parse_file_blocks", "preview_file_changes"]
