# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

"""Deterministic, independently verifiable incremental context transport."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass

PACK_SCHEMA = "powercontext.context-pack.v1"
DELTA_SCHEMA = "powercontext.context-pack-delta.v1"


class ContextPackError(ValueError):
    """Invalid context pack wire data or reconstruction."""


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _encoded(items: tuple[bytes, ...]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


def _decoded(values: object) -> tuple[bytes, ...]:
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ContextPackError("Invalid Base64 item array")
    try:
        result = tuple(base64.b64decode(value, validate=True) for value in values)
    except (ValueError, binascii.Error) as error:
        raise ContextPackError("Invalid Base64 item") from error
    if _encoded(result) != values:
        raise ContextPackError("Non-canonical Base64 item")
    return result


def _payload(raw: bytes, fields: set[str], schema: str) -> dict:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise ContextPackError("Invalid JSON") from error
    if not isinstance(value, dict) or set(value) != fields or value.get("schema") != schema:
        raise ContextPackError("Invalid context pack fields")
    return value


@dataclass(frozen=True, slots=True)
class ContextPack:
    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        if any(not isinstance(item, bytes) for item in items):
            raise ContextPackError("Pack items must be bytes")
        unsigned = {"schema": PACK_SCHEMA, "items": _encoded(items)}
        digest = "sha256:" + hashlib.sha256(_json_bytes(unsigned)).hexdigest()
        payload = {**unsigned, "digest": digest, "byte_count": 0}
        count = len(_json_bytes(payload))
        while True:
            updated = len(_json_bytes({**payload, "byte_count": count}))
            if updated == count:
                break
            count = updated
        return cls(items=items, digest=digest, byte_count=count)

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = 512) -> ContextPack:
        if chunk_bytes <= 0:
            raise ContextPackError("chunk_bytes must be positive")
        chunks: list[bytes] = []
        current = bytearray()
        for character in content:
            encoded = character.encode("utf-8")
            if len(encoded) > chunk_bytes:
                raise ContextPackError("UTF-8 character exceeds chunk limit")
            if len(current) + len(encoded) > chunk_bytes:
                chunks.append(bytes(current))
                current.clear()
            current.extend(encoded)
        if current:
            chunks.append(bytes(current))
        return cls.build(tuple(chunks))

    def to_bytes(self) -> bytes:
        return _json_bytes({
            "schema": PACK_SCHEMA,
            "items": _encoded(self.items),
            "digest": self.digest,
            "byte_count": self.byte_count,
        })

    @classmethod
    def from_bytes(cls, raw: bytes) -> ContextPack:
        value = _payload(raw, {"schema", "items", "digest", "byte_count"}, PACK_SCHEMA)
        pack = cls.build(_decoded(value["items"]))
        if (
            value["digest"] != pack.digest
            or type(value["byte_count"]) is not int
            or value["byte_count"] != pack.byte_count
        ):
            raise ContextPackError("Pack digest or byte count mismatch")
        return pack


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        prefix = 0
        for old, new in zip(base.items, target.items):
            if old != new:
                break
            prefix += 1
        return cls(base.digest, target.digest, prefix, target.items[prefix:])

    def to_bytes(self) -> bytes:
        return _json_bytes({
            "schema": DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": _encoded(self.appended),
        })

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    @classmethod
    def from_bytes(cls, raw: bytes) -> ContextPackDelta:
        value = _payload(raw, {"schema", "base_digest", "target_digest", "prefix_items", "appended"}, DELTA_SCHEMA)
        prefix = value["prefix_items"]
        if type(prefix) is not int or prefix < 0:
            raise ContextPackError("Invalid prefix item count")
        for field in ("base_digest", "target_digest"):
            digest = value[field]
            if not isinstance(digest, str) or len(digest) != 71 or not digest.startswith("sha256:"):
                raise ContextPackError("Invalid pack digest")
            try:
                int(digest[7:], 16)
            except ValueError as error:
                raise ContextPackError("Invalid pack digest") from error
        return cls(value["base_digest"], value["target_digest"], prefix, _decoded(value["appended"]))


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    if (
        base.digest != delta.base_digest
        or type(delta.prefix_items) is not int
        or not 0 <= delta.prefix_items <= len(base.items)
    ):
        raise ContextPackError("Delta base or prefix mismatch")
    target = ContextPack.build(base.items[: delta.prefix_items] + delta.appended)
    if target.digest != delta.target_digest:
        raise ContextPackError("Delta target digest mismatch")
    return target
