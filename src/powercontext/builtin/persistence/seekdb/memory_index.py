# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0

"""SeekDB Memory index composition with one-statement hybrid candidates."""

from __future__ import annotations

from pyobvector import VECTOR
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.memory import (
    CapabilityNotSupportedError,
    MemorySearchChannels,
    MemorySearchRequest,
)
from powercontext.builtin.artifacts.memory.canonical import validate_embedding
from powercontext.builtin.persistence.memory_index import CompositeMemoryIndex, MemoryIndex, memory_channel_hits
from powercontext.builtin.persistence.oceanbase.memory_index import OceanBaseMemoryVectorIndex
from powercontext.builtin.persistence.tags import memory_tag_parameters, memory_tag_sql


class SeekDBMemoryIndex(CompositeMemoryIndex):
    """Keep OceanBase projections while combining seekdb candidate channels in one SQL."""

    def __init__(self, *indexes: MemoryIndex) -> None:
        super().__init__(*indexes)
        vectors = [index for index in indexes if isinstance(index, OceanBaseMemoryVectorIndex)]
        self._vector = vectors[0] if vectors else None

    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: MemorySearchRequest,
        /,
    ) -> MemorySearchChannels:
        if request.mode != "hybrid":
            return await super().search(connection, scope_id, request)
        if self._vector is None or request.embedding_profile != self._vector.profile or request.query_vector is None:
            raise CapabilityNotSupportedError("hybrid", "a complete seekdb embedding profile is required")

        tag_clause = "" if request.tag_filter is None else memory_tag_sql("h")
        vector_tag_clause = "" if request.tag_filter is None else memory_tag_sql("m")
        statement = text(
            f"""
            WITH fts_candidates AS (
                SELECT h.memory_artifact_id, h.head_revision, h.entry_id, h.entry_version_id,
                       v.text, MATCH(h.searchable_text) AGAINST (:query IN BOOLEAN MODE) AS score
                FROM pc_memory_entry_heads AS h
                JOIN pc_memory_entry_versions AS v
                  ON v.scope_id = h.scope_id
                 AND v.memory_artifact_id = h.memory_artifact_id
                 AND v.entry_version_id = h.entry_version_id
                WHERE h.scope_id = :scope_id
                  AND h.memory_artifact_id IN :memory_artifact_ids
                  AND MATCH(h.searchable_text) AGAINST (:query IN BOOLEAN MODE)
                  {tag_clause}
                ORDER BY score DESC, h.memory_artifact_id, h.entry_id, h.entry_version_id
                LIMIT :candidate_limit
            ), vector_candidates AS (
                SELECT m.memory_artifact_id, m.head_revision, m.entry_id, m.entry_version_id,
                       v.text, l2_distance(m.embedding, :query_vector) AS distance
                FROM pc_memory_vector_entries AS m
                JOIN pc_memory_entry_versions AS v
                  ON v.scope_id = m.scope_id
                 AND v.memory_artifact_id = m.memory_artifact_id
                 AND v.entry_version_id = m.entry_version_id
                WHERE m.scope_id = :scope_id
                  AND m.memory_artifact_id IN :memory_artifact_ids
                  {vector_tag_clause}
                ORDER BY l2_distance(m.embedding, :query_vector) APPROXIMATE
                LIMIT :candidate_limit
            )
            SELECT memory_artifact_id, head_revision, entry_id, entry_version_id, text,
                   'fts' AS matched_by, NULL AS distance
            FROM fts_candidates
            UNION ALL
            SELECT memory_artifact_id, head_revision, entry_id, entry_version_id, text,
                   'vector' AS matched_by, distance
            FROM vector_candidates
            """
        )
        bind_parameters = [
            bindparam("memory_artifact_ids", expanding=True),
            bindparam("query_vector", type_=VECTOR(self._vector.profile.dimension)),
        ]
        if request.tag_filter is not None:
            bind_parameters.extend((bindparam("tag_keys", expanding=True), bindparam("tag_hashes", expanding=True)))
        statement = statement.bindparams(*bind_parameters)
        parameters = {
            "scope_id": scope_id,
            "memory_artifact_ids": tuple(memory.artifact_id for memory in request.memories),
            "query": request.analyzed_query,
            "query_vector": validate_embedding(request.query_vector, dimension=self._vector.profile.dimension),
            "candidate_limit": request.candidate_limit,
            **memory_tag_parameters(request.tag_filter),
        }
        rows = tuple((await connection.execute(statement, parameters)).mappings())
        fts = tuple(row for row in rows if row["matched_by"] == "fts")
        vector = tuple(row for row in rows if row["matched_by"] == "vector")
        return MemorySearchChannels(
            fts=memory_channel_hits(fts, request.memories),
            vector=memory_channel_hits(vector, request.memories),
        )


__all__ = ["SeekDBMemoryIndex"]
