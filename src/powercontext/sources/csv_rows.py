"""Captured row-level Sources for offline CSV documents."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator
from typing import TYPE_CHECKING
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict

from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    AdapterSourceDefinition,
    Source,
    SourceMaterialization,
    TextEvidence,
)

if TYPE_CHECKING:
    from powercontext.sources.definitions import SourceDefinition


class CSVDocumentInput(BaseModel):
    """An offline CSV document and the column that uniquely identifies its rows."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str
    primary_key: str
    content: bytes


class CSVRowInput(BaseModel):
    """One validated row with its original ordered columns and string values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class CSVRowSource(Source):
    """A captured CSV row that can be durably cited as text evidence."""

    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


def _validate_identity_component(value: str, field: str, *, allow_surrounding_space: bool = False) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field} must not be empty")  # noqa: TRY003
    if not allow_surrounding_space and value != value.strip():
        raise ValueError(f"{field} must not have leading or trailing whitespace")  # noqa: TRY003


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Return a stable, delimiter-safe identity for a row in a document."""

    _validate_identity_component(document_id, "document_id")
    _validate_identity_component(primary_key_value, "primary_key_value", allow_surrounding_space=True)
    source_id = f"csv:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(source_id) > 256:
        raise ValueError("CSV row source identity must not exceed 256 characters")  # noqa: TRY003
    return source_id


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Decode, parse, and validate a complete CSV document atomically."""

    _validate_identity_component(value.document_id, "document_id")
    _validate_identity_component(value.primary_key, "primary_key", allow_surrounding_space=True)

    try:
        text = value.content.decode("utf-8-sig", errors="strict")
        records = csv.reader(io.StringIO(text, newline=""), delimiter=",", quotechar='"', strict=True)
        header = _parse_header(next(records, None), value.primary_key)
        return _parse_rows(records, value, header)
    except (UnicodeDecodeError, csv.Error) as error:
        raise ValueError("CSV document must be valid strict UTF-8 CSV") from error  # noqa: TRY003


def _parse_header(header: list[str] | None, primary_key: str) -> tuple[str, ...]:
    if not header:
        raise ValueError("CSV document must contain a header")  # noqa: TRY003
    if any(not column.strip() for column in header):
        raise ValueError("CSV header fields must not be empty or whitespace-only")  # noqa: TRY003
    if len(set(header)) != len(header):
        raise ValueError("CSV header fields must be unique")  # noqa: TRY003
    if primary_key not in header:
        raise ValueError("primary_key must exactly match a CSV header field")  # noqa: TRY003
    return tuple(header)


def _parse_rows(
    records: Iterator[list[str]], value: CSVDocumentInput, columns: tuple[str, ...]
) -> tuple[CSVRowInput, ...]:
    key_index = columns.index(value.primary_key)
    seen_keys: set[str] = set()
    rows: list[CSVRowInput] = []
    for record in records:
        if not record:
            continue
        if len(record) != len(columns):
            raise ValueError("CSV row field count must match the header")  # noqa: TRY003
        values = tuple(record)
        key = values[key_index]
        if not key.strip():
            raise ValueError("CSV primary key values must not be empty or whitespace-only")  # noqa: TRY003
        if key in seen_keys:
            raise ValueError("CSV primary key values must be unique within a document")  # noqa: TRY003
        seen_keys.add(key)
        csv_row_source_id(value.document_id, key)
        rows.append(
            CSVRowInput(
                document_id=value.document_id,
                primary_key=value.primary_key,
                primary_key_value=key,
                columns=columns,
                values=values,
            )
        )
    return tuple(rows)


class CSVRowSourceAdapter:
    input_class = CSVRowInput
    name = "csv-row"
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        source_id = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=source_id,
            source_id=source_id,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
            materialization=SourceMaterialization.CAPTURED,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


class CSVRowTextEvidenceProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(
            source_type="csv-row",
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


CSV_ROW_SOURCE_DEFINITION: SourceDefinition[CSVRowInput, CSVRowSource, CSVRowInput] = AdapterSourceDefinition(
    CSVRowSourceAdapter(),
    version="1",
    projections=(CSVRowTextEvidenceProjection(),),
)


__all__ = [
    "CSV_ROW_SOURCE_DEFINITION",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "csv_row_source_id",
    "parse_csv_document",
]
