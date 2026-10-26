# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

"""Capture visible events from one selected Pi session branch."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import quote

from pydantic import BaseModel, JsonValue

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.connectors import (
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
    ConnectorSubmissionStatus,
)
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence


class PiSessionInput(BaseModel):
    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


def _identity(session_id: str, event_id: str) -> str:
    identity = f"pi:{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError("Pi event identity exceeds the Source limit")  # noqa: TRY003
    return identity


def _visible_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif kind == "toolCall":
            name, call_id = block.get("name"), block.get("id")
            arguments = json.dumps(
                block.get("arguments", {}), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            parts.append(f"[tool_call {name} {call_id}] {arguments}")
    return "\n".join(parts)


def _event(session_id: str, record: dict[str, JsonValue]) -> PiEventInput | None:
    kind = record.get("type")
    message = record.get("message")
    role: str
    body: str
    if kind == "message" and isinstance(message, dict):
        role = str(message.get("role", ""))
        if role == "bashExecution":
            command, output = message.get("command", ""), message.get("output", "")
            body = f"$ {command}\n{output}"
        elif role == "toolResult":
            name, call_id = message.get("toolName"), message.get("toolCallId")
            body = f"[tool_result {name} {call_id}] {_visible_text(message.get('content'))}"
        else:
            body = _visible_text(message.get("content"))
    elif kind == "custom_message":
        role, body = "custom", _visible_text(record.get("content"))
    elif kind in {"compaction", "branch_summary"}:
        role, body = str(kind), str(record.get("summary", ""))
    else:
        return None
    if not body.strip():
        return None
    return PiEventInput(
        session_id=session_id,
        event_id=str(record["id"]),
        parent_id=record.get("parentId"),
        event_type=str(kind),
        role=role,
        content=f"{role}: {body}",
        record=record,
    )


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:  # noqa: C901
    try:
        records = [json.loads(line) for line in value.content.decode("utf-8-sig").splitlines() if line.strip()]
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Invalid Pi session JSONL") from error  # noqa: TRY003
    if not records or not isinstance(records[0], dict):
        raise ValueError("Missing Pi session header")  # noqa: TRY003
    header = records[0]
    session_id = header.get("id")
    if (
        header.get("type") != "session"
        or header.get("version") != 3
        or not isinstance(session_id, str)
        or not session_id
    ):
        raise ValueError("Invalid Pi session header")  # noqa: TRY003
    nodes: dict[str, dict[str, JsonValue]] = {}
    last_id: str | None = None
    for record in records[1:]:
        if not isinstance(record, dict):
            raise ValueError("Invalid Pi session record")  # noqa: TRY003, TRY004
        event_id, parent_id = record.get("id"), record.get("parentId")
        if (
            not isinstance(event_id, str)
            or not event_id
            or event_id in nodes
            or (parent_id is not None and (not isinstance(parent_id, str) or parent_id not in nodes))
        ):
            raise ValueError("Invalid Pi event graph")  # noqa: TRY003
        nodes[event_id] = record
        last_id = event_id
    leaf = value.leaf_id if value.leaf_id is not None else last_id
    if leaf is None:
        return ()
    if leaf not in nodes:
        raise ValueError("Pi branch leaf does not exist")  # noqa: TRY003
    path: list[dict[str, JsonValue]] = []
    seen: set[str] = set()
    while leaf is not None:
        if leaf in seen:
            raise ValueError("Pi event graph contains a cycle")  # noqa: TRY003
        seen.add(leaf)
        node = nodes[leaf]
        path.append(node)
        leaf = node.get("parentId")
    return tuple(event for record in reversed(path) if (event := _event(session_id, record)) is not None)


class PiEventSourceAdapter:
    input_class = PiEventInput
    name = "pi-event"
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        return PiEventSource(
            name=_identity(value.session_id, value.event_id),
            materialization=SourceMaterialization.CAPTURED,
            **value.model_dump(),
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput.model_validate(source.model_dump(include=set(PiEventInput.model_fields)))


class PiEventTextEvidenceProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type="pi-event",
            source_id=source.name,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    PiEventSourceAdapter(), projections=(PiEventTextEvidenceProjection(),)
)


class PiSessionConnector:
    name = "pi-session-history"
    version = "1"
    source_definitions = frozenset({"pi-event"})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self.path = Path(path)
        self.leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        events = parse_pi_session(PiSessionInput(content=self.path.read_bytes(), leaf_id=self.leaf_id))
        previous = session.checkpoint or {}
        if not isinstance(previous, dict):
            raise ValueError("Invalid Pi connector checkpoint")  # noqa: TRY003, TRY004
        imported = dict(previous)
        for event in events:
            identity = _identity(event.session_id, event.event_id)
            digest = hashlib.sha256(
                json.dumps(event.record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if identity in imported:
                if imported[identity] != digest:
                    raise ValueError("Previously imported Pi event was changed")  # noqa: TRY003
                continue
            outcome = await session.submit(identity, "pi-event", event)
            if outcome.status is ConnectorSubmissionStatus.ACCEPTED:
                imported[identity] = digest
        return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE, checkpoint=imported)
