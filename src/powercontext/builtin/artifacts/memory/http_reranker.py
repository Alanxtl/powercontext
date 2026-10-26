# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

"""HTTP scoring adapter for identity-preserving Memory reranking."""

from __future__ import annotations

import math

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage


class HTTPMemoryReranker:
    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = "http-cross-encoder-v1",
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")  # noqa: TRY003
        self.endpoint = endpoint
        self.client = client
        self.batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(self, query: str, candidates: tuple[MemoryHit, ...], limit: int, /) -> MemoryRerankDecision:
        if not candidates or not 1 <= limit <= len(candidates):
            raise ValueError("invalid rerank candidate count or limit")  # noqa: TRY003
        scores: dict[int, float] = {}
        requests = 0
        for offset in range(0, len(candidates), self.batch_size):
            batch = tuple(range(offset + 1, min(offset + self.batch_size, len(candidates)) + 1))
            response = await self.client.post(
                self.endpoint,
                json={
                    "query": query,
                    "candidates": [{"rank": rank, "text": candidates[rank - 1].text} for rank in batch],
                },
            )
            requests += 1
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as error:
                raise ValueError("invalid rerank response JSON") from error  # noqa: TRY003
            if not isinstance(payload, dict) or set(payload) != {"scores"} or not isinstance(payload["scores"], list):
                raise ValueError("invalid rerank response shape")  # noqa: TRY003
            batch_scores: dict[int, float] = {}
            for item in payload["scores"]:
                if not isinstance(item, dict) or set(item) != {"rank", "score"}:
                    raise ValueError("invalid rerank score shape")  # noqa: TRY003
                rank, score = item["rank"], item["score"]
                if (
                    type(rank) is not int
                    or rank not in batch
                    or rank in batch_scores
                    or type(score) not in (int, float)
                    or not math.isfinite(score)
                ):
                    raise ValueError("invalid rerank score or rank")  # noqa: TRY003
                batch_scores[rank] = float(score)
            if set(batch_scores) != set(batch):
                raise ValueError("rerank response does not cover the batch")  # noqa: TRY003
            scores.update(batch_scores)
        selected = tuple(sorted(scores, key=lambda rank: (-scores[rank], rank))[:limit])
        return MemoryRerankDecision(selected_ranks=selected, usage=InferenceUsage(requests=requests))
