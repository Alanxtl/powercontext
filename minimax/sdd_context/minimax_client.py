"""MiniMax client used for the code modification step.

The client speaks both wire protocols that MiniMax exposes:

* ``chat-completions`` – the OpenAI-compatible endpoint of the MiniMax
  platform (``https://api.minimax.io/v1``).
* ``anthropic`` – the Anthropic Messages compatible endpoint.

Every call is recorded as a transcript (request, response, usage) so the
reviewers can verify that MiniMax produced the change.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from sdd_context.config import Settings


@dataclass(frozen=True)
class MiniMaxResult:
    """One MiniMax completion with its transcript."""

    text: str
    model: str
    protocol: str
    base_url: str
    usage: dict[str, Any]
    elapsed_ms: float
    transcript_path: str

    def to_dict(self) -> dict[str, Any]:
        """Return the transcript summary."""

        return {
            "model": self.model,
            "protocol": self.protocol,
            "base_url": self.base_url,
            "usage": self.usage,
            "elapsed_ms": self.elapsed_ms,
            "transcript_path": self.transcript_path,
        }


class MiniMaxError(RuntimeError):
    """Raised when the MiniMax request cannot be completed."""


class MiniMaxClient:
    """Small synchronous MiniMax chat client."""

    def __init__(self, settings: Settings, evidence_dir: Path | None = None) -> None:
        self._settings = settings
        self._evidence_dir = evidence_dir or settings.evidence_dir / "minimax"

    def _headers(self, protocol: str) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = self._settings.minimax_api_key
        if not key:
            raise MiniMaxError(
                "MINIMAX_API_KEY is not set; export it before running the modification step"
            )
        if protocol == "anthropic":
            headers["x-api-key"] = key
            headers["Authorization"] = f"Bearer {key}"
            headers["anthropic-version"] = "2023-06-01"
        else:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def complete(self, system: str, user: str, *, max_tokens: int | None = None) -> MiniMaxResult:
        """Send one chat completion request and record the transcript."""

        protocol = self._settings.resolved_protocol()
        base_url = self._settings.minimax_base_url
        model = self._settings.minimax_model
        limit = max_tokens or self._settings.minimax_max_tokens
        if protocol == "anthropic":
            url = f"{base_url}/messages"
            body: dict[str, Any] = {
                "model": model,
                "max_tokens": limit,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            }
        else:
            url = f"{base_url}/chat/completions"
            body = {
                "model": model,
                "max_tokens": limit,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=self._settings.minimax_timeout_seconds) as client:
                response = client.post(url, headers=self._headers(protocol), json=body)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as error:
            raise MiniMaxError(
                f"MiniMax returned HTTP {error.response.status_code}: {error.response.text[:400]}"
            ) from error
        except httpx.HTTPError as error:
            raise MiniMaxError(f"MiniMax request failed: {error}") from error
        elapsed_ms = (time.perf_counter() - started) * 1000
        text, usage = self._extract(protocol, payload)
        transcript_path = self._record_transcript(protocol, url, body, payload, text, usage, elapsed_ms)
        return MiniMaxResult(
            text=text,
            model=model,
            protocol=protocol,
            base_url=base_url,
            usage=usage,
            elapsed_ms=elapsed_ms,
            transcript_path=transcript_path,
        )

    @staticmethod
    def _extract(protocol: str, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Extract the assistant text and usage from a provider response."""

        if protocol == "anthropic":
            blocks = payload.get("content")
            text = ""
            if isinstance(blocks, list):
                text = "".join(
                    block.get("text", "")
                    for block in blocks
                    if isinstance(block, dict) and block.get("type") == "text"
                )
            usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
            return text, usage
        choices = payload.get("choices")
        text = ""
        if isinstance(choices, list) and choices:
            message = choices[0].get("message", {})
            text = message.get("content", "") or ""
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        return text, usage

    def _record_transcript(
        self,
        protocol: str,
        url: str,
        body: dict[str, Any],
        payload: dict[str, Any],
        text: str,
        usage: dict[str, Any],
        elapsed_ms: float,
    ) -> str:
        """Write the request and response to the evidence directory."""

        self._evidence_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = self._evidence_dir / f"call-{stamp}.json"
        document = {
            "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "provider": "MiniMax",
            "protocol": protocol,
            "url": url,
            "request": body,
            "response": payload,
            "text": text,
            "usage": usage,
            "elapsed_ms": round(elapsed_ms, 3),
        }
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        return path.as_posix()


__all__ = ["MiniMaxClient", "MiniMaxError", "MiniMaxResult"]
