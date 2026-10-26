# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

"""Share in-flight embeddings and cache successful vectors across callers."""

from __future__ import annotations

import asyncio
from collections import OrderedDict

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage


class CoalescingEmbeddingModel:
    def __init__(self, model, *, max_batch_size: int = 64, coalesce_ms: float = 5.0, cache_size: int = 1024) -> None:
        if max_batch_size <= 0 or coalesce_ms < 0 or cache_size < 0:
            raise ValueError("Invalid embedding batching or cache configuration")  # noqa: TRY003
        self.model = model
        self.profile = model.profile
        self.max_batch_size = max_batch_size
        self.coalesce_ms = coalesce_ms
        self.cache_size = cache_size
        self._cache: OrderedDict[str, EmbeddingVector] = OrderedDict()
        self._pending: list[str] = []
        self._inflight: dict[str, asyncio.Future[tuple[EmbeddingVector, object, InferenceUsage]]] = {}
        self._dispatcher: asyncio.Task[None] | None = None

    async def embed(self, texts: tuple[str, ...]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))
        loop = asyncio.get_running_loop()
        selected: dict[str, EmbeddingVector | asyncio.Future] = {}
        for text in texts:
            if text in selected:
                continue
            if text in self._cache:
                self._cache.move_to_end(text)
                selected[text] = self._cache[text]
            elif text in self._inflight:
                selected[text] = self._inflight[text]
            else:
                future: asyncio.Future[tuple[EmbeddingVector, object, InferenceUsage]] = loop.create_future()
                self._inflight[text] = future
                self._pending.append(text)
                selected[text] = future
        if self._pending and (self._dispatcher is None or self._dispatcher.done()):
            self._dispatcher = asyncio.create_task(self._dispatch())
        waited = [asyncio.shield(value) for value in selected.values() if isinstance(value, asyncio.Future)]
        resolved = await asyncio.gather(*waited) if waited else ()
        results = dict(
            zip((key for key, value in selected.items() if isinstance(value, asyncio.Future)), resolved, strict=True)
        )
        vectors: list[EmbeddingVector] = []
        batches: dict[object, InferenceUsage] = {}
        for text in texts:
            value = selected[text]
            if isinstance(value, asyncio.Future):
                vector, batch_id, usage = results[text]
                batches[batch_id] = usage
            else:
                vector = value
            vectors.append(vector)
        return EmbeddingResult(
            vectors=tuple(vectors),
            usage=InferenceUsage(
                requests=sum(usage.requests for usage in batches.values()),
                input_tokens=sum(usage.input_tokens or 0 for usage in batches.values()) if batches else None,
                output_tokens=sum(usage.output_tokens or 0 for usage in batches.values()) if batches else None,
            ),
        )

    async def _dispatch(self) -> None:
        await asyncio.sleep(self.coalesce_ms / 1000)
        pending, self._pending = self._pending, []
        for index in range(0, len(pending), self.max_batch_size):
            asyncio.create_task(self._run_batch(tuple(pending[index : index + self.max_batch_size])))
        if self._pending:
            self._dispatcher = asyncio.create_task(self._dispatch())

    async def _run_batch(self, texts: tuple[str, ...]) -> None:
        futures = [self._inflight[text] for text in texts]
        try:
            result = await self.model.embed(texts)
            if len(result.vectors) != len(texts):
                raise ValueError("Embedding vector count does not match input")  # noqa: TRY003
            batch_id = object()
            for text, vector, future in zip(texts, result.vectors, futures, strict=True):
                if self.cache_size:
                    self._cache[text] = vector
                    self._cache.move_to_end(text)
                    if len(self._cache) > self.cache_size:
                        self._cache.popitem(last=False)
                future.set_result((vector, batch_id, result.usage))
        except Exception as error:
            for future in futures:
                future.set_exception(error)
        finally:
            for text in texts:
                del self._inflight[text]
