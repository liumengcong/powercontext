from __future__ import annotations

import math

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import (
    MemoryRerankDecision,
    _validate_bounds,
)
from powercontext.builtin.inference import InferenceUsage


class HTTPMemoryReranker:
    """Score Memory candidates with an injected HTTP service in bounded batches."""

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = "http-cross-encoder-v1",
    ) -> None:
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise ValueError("endpoint must be non-empty")
        if not isinstance(client, httpx.AsyncClient):
            raise ValueError("client must be an httpx.AsyncClient")
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("batch_size must be positive")
        if not isinstance(policy_id, str) or not policy_id.strip():
            raise ValueError("policy_id must be non-empty")
        self.endpoint = endpoint
        self.client = client
        self.batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be non-empty")
        _validate_bounds(len(candidates), limit)
        scores: dict[int, float] = {}
        requests = 0
        for offset in range(0, len(candidates), self.batch_size):
            batch = candidates[offset : offset + self.batch_size]
            ranks = tuple(range(offset + 1, offset + len(batch) + 1))
            response = await self.client.post(
                self.endpoint,
                json={
                    "query": query,
                    "candidates": [
                        {"rank": rank, "text": candidate.text} for rank, candidate in zip(ranks, batch, strict=True)
                    ],
                },
            )
            requests += 1
            response.raise_for_status()
            try:
                payload = response.json()
            except (ValueError, UnicodeDecodeError) as error:
                raise ValueError("reranker returned invalid JSON") from error
            if not isinstance(payload, dict) or set(payload) != {"scores"} or not isinstance(payload["scores"], list):
                raise ValueError("reranker response must contain only a scores array")
            batch_scores: dict[int, float] = {}
            for item in payload["scores"]:
                if not isinstance(item, dict) or set(item) != {"rank", "score"}:
                    raise ValueError("reranker score must contain exactly rank and score")
                rank, score = item["rank"], item["score"]
                if type(rank) is not int or rank not in ranks or rank in batch_scores:
                    raise ValueError("reranker scores must cover this batch's ranks exactly once")
                if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                    raise ValueError("reranker scores must be finite numbers")
                batch_scores[rank] = float(score)
            if set(batch_scores) != set(ranks):
                raise ValueError("reranker scores must cover this batch's ranks exactly once")
            scores.update(batch_scores)

        selected = tuple(sorted(scores, key=lambda rank: (-scores[rank], rank))[:limit])
        return MemoryRerankDecision(
            selected_ranks=selected,
            usage=InferenceUsage(requests=requests),
        )


__all__ = ["HTTPMemoryReranker"]
