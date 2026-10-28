"""Render the selected development context into a prompt-ready package."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sdd_context.selection import Selection, SelectedItem


@dataclass(frozen=True)
class ContextPack:
    """The assembled development context for one task."""

    task: str
    markdown: str
    citations: tuple[dict[str, str], ...]
    selection: Selection

    def to_dict(self) -> dict[str, Any]:
        """Return the machine-readable form of the context package."""

        return {
            "task": self.task,
            "search_terms": list(self.selection.search_terms),
            "citations": list(self.citations),
            "sections": {
                "specs": [item.identifier for item in self.selection.specs],
                "code": [item.identifier for item in self.selection.code],
                "tests": [item.identifier for item in self.selection.tests],
                "files": [item.identifier for item in self.selection.files],
            },
        }


def _spec_block(item: SelectedItem) -> str:
    payload = item.payload
    status = payload.get("status", "")
    applies_to = payload.get("applies_to", "")
    lines = [
        f"### {item.identifier} — {item.title}",
        f"- 来源：`{item.path}`",
        f"- 状态：{status}",
    ]
    if applies_to:
        lines.append(f"- 适用范围：{applies_to}")
    lines.append(f"- 选中原因：{item.reason}（检索得分 {item.score}）")
    lines.append("")
    lines.append("```markdown")
    lines.append(str(payload.get("content", "")).strip())
    lines.append("```")
    return "\n".join(lines)


def _code_block(item: SelectedItem) -> str:
    payload = item.payload
    start = payload.get("start_line", 0)
    end = payload.get("end_line", 0)
    lines = [
        f"### {payload.get('qualname', item.title)}",
        f"- 来源：`{item.path}` L{start}-L{end}",
        f"- 符号：{payload.get('signature', '')}",
        f"- 选中原因：{item.reason}（检索得分 {item.score}）",
        "",
        "```python",
        str(payload.get("content", "")).strip(),
        "```",
    ]
    return "\n".join(lines)


def _test_block(item: SelectedItem) -> str:
    payload = item.payload
    lines = [
        f"### {payload.get('name', item.title)}",
        f"- 来源：`{item.path}`",
        f"- 选中原因：{item.reason}（检索得分 {item.score}）",
        "",
        "```python",
        str(payload.get("content", "")).strip(),
        "```",
    ]
    return "\n".join(lines)


def render_context(
    selection: Selection,
    *,
    task_state: dict[str, Any] | None = None,
    max_bytes: int = 24000,
) -> ContextPack:
    """Render the selection as Markdown plus a citation list."""

    lines: list[str] = [
        "# 开发上下文（规格驱动开发）",
        "",
        "## 1. 本次任务",
        "",
        selection.task.strip(),
        "",
        "## 2. 适用的规格条目",
        "",
    ]
    if selection.specs:
        lines.extend(_spec_block(item) for item in selection.specs)
    else:
        lines.append("（检索未命中规格条目，请检查导入结果）")
    lines.extend(["", "## 3. 需要修改的代码", ""])
    if selection.code:
        lines.extend(_code_block(item) for item in selection.code)
    else:
        lines.append("（检索未命中代码符号）")
    lines.extend(["", "## 4. 用于验证的测试", ""])
    if selection.tests:
        lines.extend(_test_block(item) for item in selection.tests)
    else:
        lines.append("（检索未命中测试用例）")
    if selection.files:
        lines.extend(["", "## 4.1 需要修改的文件的完整内容", ""])
        lines.append("修改时请保持以上文件中的其余内容不变，只实现规格要求的行为。")
        lines.append("")
        for item in selection.files:
            lines.append(f"### {item.path}")
            lines.append("")
            lines.append("```python")
            lines.append(str(item.payload.get("content", "")).strip())
            lines.append("```")
            lines.append("")
    lines.extend(["", "## 5. 任务状态", ""])
    if task_state:
        completed = task_state.get("completed", [])
        todo = task_state.get("todo", [])
        results = task_state.get("validation_results", [])
        lines.append("- 已完成事项：" + ("；".join(completed) if completed else "（无）"))
        lines.append("- 待办事项：" + ("；".join(todo) if todo else "（无）"))
        lines.append("- 已有验证结果：" + ("；".join(results) if results else "（无）"))
    else:
        lines.append("- 已完成事项：（尚未记录）")
        lines.append("- 待办事项：（尚未记录）")
        lines.append("- 已有验证结果：（尚未记录）")
    lines.extend(["", "## 6. 来源清单", ""])
    citations = selection.citations()
    for citation in citations:
        lines.append(f"- [{citation['kind']}] `{citation['path']}` — {citation['title']}（{citation['id']}）")
    markdown = "\n".join(lines)
    encoded = markdown.encode("utf-8")
    if len(encoded) > max_bytes:
        markdown = encoded[:max_bytes].decode("utf-8", errors="ignore") + "\n\n（上下文超出字节预算，已截断）"
    return ContextPack(
        task=selection.task,
        markdown=markdown,
        citations=tuple(citations),
        selection=selection,
    )


__all__ = ["ContextPack", "render_context"]
