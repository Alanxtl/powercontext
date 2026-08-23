from __future__ import annotations

import asyncio
from typing import Any

from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.runtime import (
    BuiltinRuntime,
    ProposeExperienceRequest,
    RuntimeCapabilities,
)


class _UnusedProvider:
    async def get(self, scope_id: str, /) -> Any:
        raise AssertionError(scope_id)


class _ReviewService:
    def __init__(self, *, block_first: bool = False) -> None:
        self._block_first = block_first
        self._calls = 0
        self._active = 0
        self.max_active = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def propose_experience(self, *args: object, **kwargs: object) -> Any:
        del args, kwargs
        self._calls += 1
        self._active += 1
        self.max_active = max(self.max_active, self._active)
        try:
            if self._block_first and self._calls == 1:
                self.started.set()
                await self.release.wait()
        finally:
            self._active -= 1
        return object()


def _proposal() -> ProposeExperienceRequest:
    return ProposeExperienceRequest(
        proposal=ExperienceContent(
            situation="Concurrent scope operations share a serialization boundary.",
            action="Retain the scope lease until all lock waiters finish.",
            outcome="Cache pressure cannot create a second lock for the same scope.",
            lesson="Evict only inactive scope resources.",
        )
    )


def test_scope_cache_does_not_evict_a_lock_with_holders_or_waiters() -> None:
    async def scenario() -> None:
        services = {"same": _ReviewService(block_first=True)}
        evicted: list[str] = []
        same_scope_leases = 0
        both_same_scope_operations_started = asyncio.Event()

        def review_service(scope_id: str) -> Any:
            return services.setdefault(scope_id, _ReviewService(block_first=scope_id == "same"))

        def observe(cached: int, active: int) -> None:
            nonlocal same_scope_leases
            if cached == 1 and active == 1:
                same_scope_leases += 1
                if same_scope_leases == 2:
                    both_same_scope_operations_started.set()

        runtime = BuiltinRuntime(
            provider=_UnusedProvider(),
            capabilities=RuntimeCapabilities(memory_extraction=False, memory_search_modes=()),
            review_service=review_service,
            scope_cache_size=1,
            scope_evictor=evicted.append,
            scope_cache_observer=observe,
        )
        request = _proposal()
        same = runtime.experience.for_scope("same")
        first = asyncio.create_task(same.propose(request))
        await services["same"].started.wait()
        second = asyncio.create_task(same.propose(request))
        await both_same_scope_operations_started.wait()

        await runtime.experience.for_scope("other").propose(request)
        assert evicted == ["other"]

        services["same"].release.set()
        await asyncio.gather(first, second)
        assert services["same"].max_active == 1

        await runtime.experience.for_scope("replacement").propose(request)
        assert evicted == ["other", "same"]
        await runtime.close()

    asyncio.run(scenario())
