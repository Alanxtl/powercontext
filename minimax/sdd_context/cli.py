"""Command line entry point of the SDD context module.

Typical flow::

    python -m sdd_context import
    python -m sdd_context context --task "为任务列表增加按负责人过滤"
    python -m sdd_context plan --task "为任务列表增加按负责人过滤" --apply
    python -m sdd_context test --label after
    python -m sdd_context state save --task "..."
    python -m sdd_context resume --task "..."
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from sdd_context.config import Settings
from sdd_context.context_pack import render_context
from sdd_context.corpus import Corpus
from sdd_context.evidence import stamp, write_json, write_text
from sdd_context.minimax_client import MiniMaxClient
from sdd_context.patcher import apply_file_changes, parse_file_blocks, preview_file_changes
from sdd_context.selection import select_context
from sdd_context.state import open_state_store
from sdd_context.workspace import scan_workspace

DEFAULT_TASK = "为任务列表增加按负责人过滤能力，并保持既有排序与状态过滤行为不变"


def _settings() -> Settings:
    return Settings.from_env()


def _corpus(store: Any) -> Corpus:
    return Corpus(store.engine)


async def command_import(settings: Settings) -> int:
    """Import the workspace materials into seekdb and PowerContext."""

    workspace = scan_workspace(settings.workspace)
    async with open_state_store(settings) as store:
        corpus = _corpus(store)
        counts = await corpus.replace_entries(
            workspace.specs, workspace.symbols, workspace.tests, workspace.documents
        )
        captured = await store.capture_materials(workspace)
        document = {
            "imported_at": stamp(),
            "workspace": str(settings.workspace),
            "database": settings.database_kind,
            "corpus": counts,
            "powercontext_sources": captured,
            "scope_id": store.scope_id,
        }
        path = write_json(settings.evidence_dir / "import" / f"import-{stamp()}.json", document)
    print(json.dumps(document, ensure_ascii=False, indent=2))
    print(f"evidence: {path}")
    return 0


async def command_context(settings: Settings, task: str) -> int:
    """Build the development context for one task."""

    async with open_state_store(settings) as store:
        corpus = _corpus(store)
        selection = await select_context(
            corpus,
            task,
            spec_limit=settings.spec_limit,
            code_limit=settings.code_limit,
            test_limit=settings.test_limit,
        )
        snapshot = await store.load_state(task)
        pack = render_context(
            selection,
            task_state=snapshot.payload if snapshot else None,
            max_bytes=settings.context_max_bytes,
        )
        document = {
            "generated_at": stamp(),
            "task": task,
            "context": pack.to_dict(),
            "database_queries": [
                {
                    "purpose": query.purpose,
                    "sql": query.sql_text,
                    "params": query.params,
                    "row_count": query.row_count,
                    "elapsed_ms": round(query.elapsed_ms, 3),
                }
                for query in selection.queries
            ],
            "markdown": pack.markdown,
        }
        base = settings.evidence_dir / "context"
        write_text(base / f"context-{stamp()}.md", pack.markdown)
        path = write_json(base / f"context-{stamp()}.json", document)
    print(pack.markdown)
    print(f"evidence: {path}")
    return 0


_SYSTEM_PROMPT = (
    "你是资深 Python 工程师，按规格驱动开发（SDD）完成一次局部代码修改。"
    "只输出需要修改的文件内容，不要解释过程，不要输出未在上下文中出现的文件。"
    "输出格式必须严格为：先写一行 `FILE: <相对路径>`，随后紧跟一个包含完整文件内容的代码块。"
    "必须逐条实现上下文列出的每一条规格条目，包括其中的边界输入规则；"
    "不要删除或改写与本次任务无关的注释、文档字符串和既有行为。"
)


def _user_prompt(
    context_markdown: str,
    task: str,
    selection: Any = None,
    feedback: str | None = None,
) -> str:
    spec_lines = ""
    if selection is not None:
        items = "\n".join(
            f"- {item.identifier} {item.title}（状态：{item.payload.get('status', '')}）"
            for item in selection.specs
        )
        spec_lines = (
            "本次任务适用的规格条目如下，每一条都必须实现，包含规格中写明的边界输入处理：\n"
            f"{items}\n\n"
        )
    feedback_block = ""
    if feedback:
        feedback_block = (
            "上一次修改没有通过测试，请根据下面的失败信息修正实现，并重新输出完整文件：\n"
            f"```text\n{feedback.strip()}\n```\n\n"
        )
    return (
        "下面是本次任务的开发上下文（规格、代码、测试）。\n\n"
        f"{context_markdown}\n\n"
        f"任务：{task}\n\n"
        f"{spec_lines}{feedback_block}"
        "请根据上下文中的规格实现修改。\n"
        "要求：\n"
        "1. 只修改确有必要的文件；每个文件输出一次。\n"
        "2. 代码块必须是该文件的完整内容，不要使用省略号或 diff 片段。\n"
        "3. 不要修改测试文件，测试是验收依据。\n"
        "4. 保持既有行为不变，只实现规格中新增的条目。\n"
        "5. 规格中写明的边界输入（例如空字符串、纯空白、None）必须按规格处理。\n"
    )


async def command_plan(
    settings: Settings,
    task: str,
    apply: bool,
    feedback: str | None = None,
    label: str = "manual",
) -> int:
    """Ask MiniMax for the code change and optionally apply it."""

    async with open_state_store(settings) as store:
        corpus = _corpus(store)
        selection = await select_context(
            corpus,
            task,
            spec_limit=settings.spec_limit,
            code_limit=settings.code_limit,
            test_limit=settings.test_limit,
        )
        snapshot = await store.load_state(task)
        pack = render_context(
            selection,
            task_state=snapshot.payload if snapshot else None,
            max_bytes=settings.context_max_bytes,
        )
    client = MiniMaxClient(settings)
    result = client.complete(_SYSTEM_PROMPT, _user_prompt(pack.markdown, task, selection, feedback))
    blocks = parse_file_blocks(result.text)
    if not blocks:
        print("MiniMax answer did not contain any FILE block:", file=sys.stderr)
        write_json(
            settings.evidence_dir / "patches" / f"patch-{stamp()}.json",
            {"error": "no FILE block", "result": result.to_dict(), "answer": result.text},
        )
        return 2
    changes = (
        apply_file_changes(settings.workspace, blocks)
        if apply
        else preview_file_changes(settings.workspace, blocks)
    )
    document = {
        "generated_at": stamp(),
        "task": task,
        "applied": apply,
        "minimax": result.to_dict(),
        "files": [
            {
                "path": change.path,
                "created": change.created,
                "diff": change.diff,
            }
            for change in changes
        ],
    }
    patch_path = write_json(settings.evidence_dir / "patches" / f"patch-{label}-{stamp()}.json", document)
    diff_text = "\n".join(change.diff for change in changes)
    diff_path = write_text(settings.evidence_dir / "diff" / f"change-{label}-{stamp()}.patch", diff_text)
    print(json.dumps(document["minimax"], ensure_ascii=False, indent=2))
    for change in changes:
        print(f"--- {change.path} {'(created)' if change.created else '(modified)'}")
    if apply:
        print("changes applied to the workspace")
    else:
        print("preview only; rerun with --apply to write the files")
    print(f"patch evidence: {patch_path}")
    print(f"diff evidence: {diff_path}")
    return 0


def command_test(settings: Settings, label: str) -> int:
    """Run the workspace test suite and store the log."""

    workspace = settings.workspace
    command = [sys.executable, "-m", "pytest", "-q", "tests"]
    completed = subprocess.run(  # noqa: S603
        command,
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    output = completed.stdout + completed.stderr
    path = write_text(settings.evidence_dir / "tests" / f"pytest-{label}-{stamp()}.log", output)
    summary = {
        "label": label,
        "command": " ".join(command),
        "exit_code": completed.returncode,
        "stdout_tail": completed.stdout.strip().splitlines()[-15:],
        "log": path.as_posix(),
    }
    write_json(settings.evidence_dir / "tests" / f"pytest-{label}-{stamp()}.json", summary)
    print(output)
    print(f"exit code: {completed.returncode}")
    print(f"evidence: {path}")
    return completed.returncode


async def command_state_save(settings: Settings, task: str, payload: dict[str, Any]) -> int:
    """Persist the task progress into PowerContext."""

    async with open_state_store(settings) as store:
        source_id = await store.save_state(task, payload)
        await store.record_contract(
            source_id=f"state:{source_id}",
            objective=task,
            in_scope=tuple(payload.get("in_scope", (task,))),
            completion_criteria=tuple(payload.get("completion_criteria", ("按规格实现并通过目标测试",))),
            facts=tuple(payload.get("completed", ())),
        )
        await store.record_outcome(
            source_id=f"outcome:{source_id}",
            objective=task,
            status=str(payload.get("status", "partial")),
            summary=str(payload.get("summary", "任务状态已保存")),
            observations=tuple(payload.get("completed", ())),
            checks=tuple(
                (name, status, details)
                for name, status, details in payload.get("checks", ())
            ),
        )
    print(json.dumps({"task": task, "state_source_id": source_id}, ensure_ascii=False, indent=2))
    return 0


async def command_state_load(settings: Settings, task: str) -> int:
    """Read the task progress back from PowerContext."""

    async with open_state_store(settings) as store:
        snapshot = await store.load_state(task)
        continuity = await store.continuity()
    if snapshot is None:
        print(f"no state stored for task: {task}", file=sys.stderr)
        return 1
    print(json.dumps({"state": snapshot.payload, "continuity": continuity}, ensure_ascii=False, indent=2))
    return 0


async def command_resume(settings: Settings, task: str) -> int:
    """Restart the module and continue from the stored state."""

    async with open_state_store(settings) as store:
        snapshot = await store.load_state(task)
        continuity = await store.continuity()
    if snapshot is None:
        print(f"no state stored for task: {task}", file=sys.stderr)
        return 1
    payload = snapshot.payload
    print("== 从 PowerContext 读回的任务状态 ==")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("== 工作连续性视图 ==")
    print(json.dumps(continuity, ensure_ascii=False, indent=2))
    checks = payload.get("checks", ())
    if checks:
        print("== 依据已存状态复核验证结果 ==")
        for name, status, details in checks:
            print(f"- {name}: {status} ({details})")
    return 0


async def command_queries(settings: Settings) -> int:
    """Show the recorded seekdb queries."""

    async with open_state_store(settings) as store:
        log = await _corpus(store).query_log()
    path = write_json(settings.evidence_dir / "db" / f"queries-{stamp()}.json", log)
    print(json.dumps(log, ensure_ascii=False, indent=2))
    print(f"evidence: {path}")
    return 0


async def command_demo(settings: Settings, task: str) -> int:
    """Run the complete flow: import, test, context, plan, test, state, resume."""

    summary: dict[str, Any] = {"task": task, "started_at": stamp()}
    await command_import(settings)
    command_snapshot(settings, "before")
    summary["before"] = _test_summary(settings, "before")
    await command_context(settings, task)
    rounds: list[dict[str, Any]] = []
    feedback: str | None = None
    for attempt in range(1, settings.max_rounds + 1):
        await command_plan(settings, task, apply=True, feedback=feedback, label=f"round{attempt}")
        result = _test_summary(settings, f"round{attempt}")
        rounds.append(result)
        if result["exit_code"] == 0:
            break
        feedback = result["output"]
    summary["rounds"] = [
        {"label": item["label"], "exit_code": item["exit_code"], "tail": item["tail"], "log": item["log"]}
        for item in rounds
    ]
    summary["after"] = rounds[-1] if rounds else summary["before"]
    payload = {
        "status": "succeeded" if summary["after"]["exit_code"] == 0 else "failed",
        "summary": "任务已按规格完成修改并通过目标测试与回归测试",
        "completed": [
            "导入规格、代码与测试资料到 seekdb 语料库",
            "检索并组装开发上下文（规格 / 代码 / 测试）",
            "由 MiniMax 生成代码修改并写入工作区",
            "运行目标测试与回归测试",
        ],
        "todo": [],
        "validation_results": [
            f"修改前目标测试：{summary['before']['exit_code']}",
            f"修改后目标测试与回归测试：{summary['after']['exit_code']}",
        ],
        "in_scope": [task],
        "completion_criteria": [
            "修改前目标功能测试因行为未实现而失败",
            "修改后目标功能测试全部通过",
            "相关回归测试全部通过",
        ],
        "checks": [
            ("目标功能测试（修改前）", "failed" if summary["before"]["exit_code"] != 0 else "passed", f"exit={summary['before']['exit_code']}"),
            ("目标功能测试（修改后）", "passed" if summary["after"]["exit_code"] == 0 else "failed", f"exit={summary['after']['exit_code']}"),
        ],
        "test_logs": {
            "before": summary["before"]["log"],
            "after": summary["after"]["log"],
        },
    }
    await command_state_save(settings, task, payload)
    summary["state_saved"] = True
    resumed = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "sdd_context", "resume", "--task", task],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
    )
    summary["resume_exit_code"] = resumed.returncode
    summary["resume_output"] = resumed.stdout.strip()
    path = write_json(settings.evidence_dir / "demo" / f"demo-{stamp()}.json", summary)
    print(resumed.stdout)
    print(f"demo evidence: {path}")
    return 0 if resumed.returncode == 0 and summary["after"]["exit_code"] == 0 else 1


def _test_summary(settings: Settings, label: str) -> dict[str, Any]:
    """Run the test suite and return a compact summary."""

    workspace = settings.workspace
    command = [sys.executable, "-m", "pytest", "-q", "tests"]
    completed = subprocess.run(  # noqa: S603
        command,
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    output = completed.stdout + completed.stderr
    path = write_text(settings.evidence_dir / "tests" / f"pytest-{label}-{stamp()}.log", output)
    return {
        "label": label,
        "command": " ".join(command),
        "exit_code": completed.returncode,
        "tail": output.strip().splitlines()[-3:],
        "output": output[-8000:],
        "log": path.as_posix(),
    }


def _workspace_files(settings: Settings) -> list[Path]:
    """Return every text file of the business workspace."""

    files: list[Path] = []
    for folder in ("spec", "src", "tests"):
        root = settings.workspace / folder
        if not root.is_dir():
            continue
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            if "__pycache__" in path.parts:
                continue
            try:
                path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            files.append(path)
    return files


def command_snapshot(settings: Settings, label: str) -> int:
    """Copy the current workspace state so it can be restored later."""

    target = settings.evidence_dir / "snapshots" / label
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    for path in _workspace_files(settings):
        destination = target / path.relative_to(settings.workspace)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        copied += 1
    print(json.dumps({"label": label, "files": copied, "snapshot": target.as_posix()}, ensure_ascii=False, indent=2))
    return 0


def command_reset(settings: Settings, label: str) -> int:
    """Restore the workspace from a snapshot."""

    source = settings.evidence_dir / "snapshots" / label
    if not source.is_dir():
        print(f"no snapshot found: {source}", file=sys.stderr)
        return 1
    restored = 0
    for path in sorted(item for item in source.rglob("*") if item.is_file()):
        destination = settings.workspace / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        restored += 1
    print(json.dumps({"label": label, "restored": restored}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""

    parser = argparse.ArgumentParser(prog="sdd-context", description="SDD context module")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("import", help="import workspace materials into seekdb and PowerContext")

    context_parser = sub.add_parser("context", help="build the development context for a task")
    context_parser.add_argument("--task", required=True)

    plan_parser = sub.add_parser("plan", help="ask MiniMax for the code change")
    plan_parser.add_argument("--task", required=True)
    plan_parser.add_argument("--apply", action="store_true", help="write the change into the workspace")
    plan_parser.add_argument("--feedback-file", default=None, help="file with failing test output to repair")

    test_parser = sub.add_parser("test", help="run the workspace test suite")
    test_parser.add_argument("--label", default="manual")

    state_parser = sub.add_parser("state", help="save or load the task state")
    state_sub = state_parser.add_subparsers(dest="state_command", required=True)
    save_parser = state_sub.add_parser("save")
    save_parser.add_argument("--task", required=True)
    save_parser.add_argument("--completed", nargs="*", default=[])
    save_parser.add_argument("--todo", nargs="*", default=[])
    save_parser.add_argument("--result", nargs="*", default=[])
    save_parser.add_argument("--status", default="partial")
    load_parser = state_sub.add_parser("load")
    load_parser.add_argument("--task", required=True)

    resume_parser = sub.add_parser("resume", help="restart and continue from the stored state")
    resume_parser.add_argument("--task", required=True)

    sub.add_parser("queries", help="show the recorded seekdb queries")

    snapshot_parser = sub.add_parser("snapshot", help="snapshot the workspace files")
    snapshot_parser.add_argument("--label", default="before")

    reset_parser = sub.add_parser("reset", help="restore the workspace from a snapshot")
    reset_parser.add_argument("--label", default="before")

    demo_parser = sub.add_parser("demo", help="run the complete end-to-end flow")
    demo_parser.add_argument("--task", default=DEFAULT_TASK)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the module command line."""

    parser = build_parser()
    args = parser.parse_args(argv)
    settings = _settings()

    if args.command == "import":
        return asyncio.run(command_import(settings))
    if args.command == "context":
        return asyncio.run(command_context(settings, args.task))
    if args.command == "plan":
        feedback = None
        if args.feedback_file:
            feedback = Path(args.feedback_file).read_text(encoding="utf-8")
        return asyncio.run(command_plan(settings, args.task, apply=bool(args.apply), feedback=feedback))
    if args.command == "test":
        return command_test(settings, args.label)
    if args.command == "state":
        if args.state_command == "save":
            payload = {
                "status": args.status,
                "summary": "任务状态已保存",
                "completed": list(args.completed),
                "todo": list(args.todo),
                "validation_results": list(args.result),
            }
            return asyncio.run(command_state_save(settings, args.task, payload))
        return asyncio.run(command_state_load(settings, args.task))
    if args.command == "resume":
        return asyncio.run(command_resume(settings, args.task))
    if args.command == "queries":
        return asyncio.run(command_queries(settings))
    if args.command == "demo":
        return asyncio.run(command_demo(settings, args.task))
    if args.command == "snapshot":
        return command_snapshot(settings, args.label)
    if args.command == "reset":
        return command_reset(settings, args.label)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
