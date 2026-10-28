"""Verify that a specification update is picked up after re-importing.

The check amends an existing, relevant specification (``SPEC-FILTER-001``) with a
marker sentence. After re-importing, the generated context must contain the new
sentence. The amendment is then reverted and the context must follow the
revert, proving the module answers from the stored material instead of a
fixed answer.

Run it from anywhere::

    python minimax/scripts/verify_spec_update.py
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKSPACE = ROOT / "workspace"
EVIDENCE = ROOT / "evidence" / "verification"
PYTHON = sys.executable
SPEC = WORKSPACE / "spec" / "SPEC-FILTER-001.md"
TASK = "为任务列表增加按负责人过滤能力，并保持既有排序与状态过滤行为不变"

MARKER = "zzz-spec-update-marker"
ORIGINAL_LINE = "- 未指派（`assignee is None`）的任务永远不出现在任何具体负责人的过滤结果中。"
UPDATED_LINE = f"{ORIGINAL_LINE}\n- 负责人比较忽略大小写（{MARKER}）。"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, "-m", "sdd_context", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )


def latest_context() -> tuple[str, pathlib.Path]:
    files = sorted((ROOT / "evidence" / "context").glob("context-*.md"), key=lambda item: item.stat().st_mtime)
    path = files[-1]
    return path.read_text(encoding="utf-8"), path


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    original = SPEC.read_text(encoding="utf-8")
    result: dict[str, object] = {"check": "specification-update-is-picked-up"}

    try:
        SPEC.write_text(original.replace(ORIGINAL_LINE, UPDATED_LINE), encoding="utf-8")
        imported = run("import")
        run("context", "--task", TASK)
        text, path = latest_context()
        result["with_updated_spec"] = {
            "import_exit_code": imported.returncode,
            "context_file": path.name,
            "contains_marker": MARKER in text,
            "contains_original_line": ORIGINAL_LINE in text,
        }
    finally:
        SPEC.write_text(original, encoding="utf-8")

    imported = run("import")
    run("context", "--task", TASK)
    text, path = latest_context()
    result["after_reverting_spec"] = {
        "import_exit_code": imported.returncode,
        "context_file": path.name,
        "contains_marker": MARKER in text,
        "contains_original_line": ORIGINAL_LINE in text,
    }
    result["passed"] = bool(
        result["with_updated_spec"]["contains_marker"]
        and result["with_updated_spec"]["contains_original_line"]
        and not result["after_reverting_spec"]["contains_marker"]
        and result["after_reverting_spec"]["contains_original_line"]
    )
    path_out = EVIDENCE / "spec-update-check.json"
    path_out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"evidence: {path_out}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
