"""Helpers that store run artifacts in the evidence directory."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def stamp() -> str:
    """Return a filesystem-safe UTC timestamp."""

    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_json(path: Path, document: Any) -> Path:
    """Write one JSON document, creating parent directories."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def write_text(path: Path, text: str) -> Path:
    """Write one text file, creating parent directories."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def read_json(path: Path) -> Any:
    """Read one JSON document."""

    return json.loads(path.read_text(encoding="utf-8"))


__all__ = ["read_json", "stamp", "write_json", "write_text"]
