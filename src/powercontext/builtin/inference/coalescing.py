from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Protocol

from powercontext.builtin.inference.models import EmbeddingResult, InferenceUsage


class _EmbeddingProvider(Protocol):
    profile: object

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult: ...


@dataclass(frozen=True, slots=True)
class _ResolvedVector:
    vector: tuple[float, ...]
    usage: InferenceUsage | None
    batch_id: int | None


class CoalescingEmbeddingModel:
    """Coalesce concurrent duplicate inputs and cache successful vectors with an LRU."""

    def __init__(
        self,
        model: _EmbeddingProvider,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if type(max_batch_size) is not int or max_batch_size < 1:
            raise ValueError("max_batch_size must be positive")
        if isinstance(coalesce_ms, bool) or not isinstance(coalesce_ms, (int, float)) or coalesce_ms < 0:
            raise ValueError("coalesce_ms must be non-negative")
        if type(cache_size) is not int or cache_size < 0:
            raise ValueError("cache_size must be non-negative")
        self._model = model
        self.profile = model.profile
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = float(coalesce_ms) / 1000
        self._cache_size = cache_size
        self._cache: OrderedDict[str, tuple[float, ...]] = OrderedDict()
        self._inflight: dict[str, asyncio.Future[_ResolvedVector]] = {}
        self._pending: deque[str] = deque()
        self._lock = asyncio.Lock()
        self._dispatcher: asyncio.Task[None] | None = None
        self._next_batch_id = 0

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        if not isinstance(texts, tuple) or any(not isinstance(text, str) for text in texts):
            raise ValueError("texts must be a tuple of strings")
        if not texts:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0, input_tokens=0, output_tokens=0))

        requested: dict[str, asyncio.Future[_ResolvedVector] | tuple[float, ...]] = {}
        async with self._lock:
            for text in dict.fromkeys(texts):
                cached = self._cache.get(text)
                if cached is not None:
                    self._cache.move_to_end(text)
                    requested[text] = cached
                    continue
                future = self._inflight.get(text)
                if future is None:
                    future = asyncio.get_running_loop().create_future()
                    future.add_done_callback(_consume_future_exception)
                    self._inflight[text] = future
                    self._pending.append(text)
                requested[text] = future
            if self._pending and (self._dispatcher is None or self._dispatcher.done()):
                self._dispatcher = asyncio.create_task(self._dispatch())

        resolved: dict[str, _ResolvedVector] = {}
        for text, item in requested.items():
            if isinstance(item, tuple):
                resolved[text] = _ResolvedVector(item, None, None)
            else:
                resolved[text] = await asyncio.shield(item)

        vectors = tuple(resolved[text].vector for text in texts)
        batches: dict[int, InferenceUsage] = {}
        for item in resolved.values():
            if item.batch_id is not None and item.usage is not None:
                batches[item.batch_id] = item.usage
        usage = InferenceUsage(
            requests=sum(value.requests for value in batches.values()),
            input_tokens=_sum_optional(value.input_tokens for value in batches.values()),
            output_tokens=_sum_optional(value.output_tokens for value in batches.values()),
        )
        return EmbeddingResult(vectors=vectors, usage=usage)

    async def _dispatch(self) -> None:
        while True:
            if self._coalesce_seconds:
                await asyncio.sleep(self._coalesce_seconds)
            else:
                await asyncio.sleep(0)
            async with self._lock:
                batch_texts = tuple(self._pending.popleft() for _ in range(min(len(self._pending), self._max_batch_size)))
                if not batch_texts:
                    return
                batch_futures = tuple(self._inflight[text] for text in batch_texts)
                batch_id = self._next_batch_id
                self._next_batch_id += 1
            try:
                result = await self._model.embed(batch_texts)
                if len(result.vectors) != len(batch_texts):
                    raise ValueError("embedding provider returned the wrong vector count")
                vectors = tuple(tuple(vector) for vector in result.vectors)
            except Exception as error:
                async with self._lock:
                    for text, future in zip(batch_texts, batch_futures, strict=True):
                        self._inflight.pop(text, None)
                        if not future.done():
                            future.set_exception(error)
                continue

            async with self._lock:
                for text, vector, future in zip(batch_texts, vectors, batch_futures, strict=True):
                    self._inflight.pop(text, None)
                    if self._cache_size:
                        self._cache[text] = vector
                        self._cache.move_to_end(text)
                        while len(self._cache) > self._cache_size:
                            self._cache.popitem(last=False)
                    if not future.done():
                        future.set_result(_ResolvedVector(vector, result.usage, batch_id))


def _sum_optional(values) -> int | None:
    numbers = tuple(values)
    if not numbers or all(value is None for value in numbers):
        return None
    return sum(value or 0 for value in numbers)


def _consume_future_exception(future: asyncio.Future[object]) -> None:
    if not future.cancelled():
        future.exception()


__all__ = ["CoalescingEmbeddingModel"]
