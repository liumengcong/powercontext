from __future__ import annotations

import hashlib
from io import BytesIO
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict
from pypdf import PdfReader

from powercontext.artifacts import MemoryCitation
from powercontext.builtin.runtime.models import SearchMemoryRequest
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    AdapterSourceDefinition,
    Source,
    SourceMaterialization,
    TextEvidence,
)
from powercontext.sources.definitions import SourceDefinition


class PDFDocumentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    document_id: str
    content: bytes


class PDFPageInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageSource(Source):
    document_id: str
    document_sha256: str
    page_number: int
    text: str


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError("document_id must not be empty")
    if len(document_sha256) != 64 or document_sha256.lower() != document_sha256:
        raise ValueError("document_sha256 must be a lowercase SHA-256 digest")
    try:
        int(document_sha256, 16)
    except ValueError as error:
        raise ValueError("document_sha256 must be a lowercase SHA-256 digest") from error
    if type(page_number) is not int or page_number < 1:
        raise ValueError("page_number must be a positive integer")
    identity = f"pdf:{quote(document_id, safe='')}:{document_sha256}:{page_number}"
    if len(identity) > 256:
        raise ValueError("PDF page identity exceeds 256 characters")
    return identity


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    if not value.document_id.strip():
        raise ValueError("document_id must not be empty")
    if not value.content.startswith(b"%PDF-"):
        raise ValueError("content is not a PDF")
    try:
        reader = PdfReader(BytesIO(value.content), strict=True)
        if reader.is_encrypted:
            result = reader.decrypt("")
            if not result:
                raise ValueError("PDF requires a password")
        pages = tuple(
            PDFPageInput(
                document_id=value.document_id,
                document_sha256=hashlib.sha256(value.content).hexdigest(),
                page_number=index,
                text=page.extract_text() or "",
            )
            for index, page in enumerate(reader.pages, 1)
        )
    except Exception as error:
        if isinstance(error, ValueError) and str(error).startswith("PDF requires"):
            raise
        raise ValueError("PDF document could not be parsed completely") from error
    if not pages:
        raise ValueError("PDF document has no pages")
    return pages


class PDFPageSourceAdapter:
    input_class = PDFPageInput
    name = "pdf-page"
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        name = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=name,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
            materialization=SourceMaterialization.CAPTURED,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type="pdf-page",
            source_id=source.name,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_DEFINITION: SourceDefinition[PDFPageInput, PDFPageSource, PDFPageInput] = AdapterSourceDefinition(
    PDFPageSourceAdapter(), version="1", projections=(PDFPageTextEvidenceProjection(),)
)


async def search_pdf_pages(memory, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    if not question.strip() or type(limit) is not int or limit < 1:
        raise ValueError("question and positive limit are required")
    page = await memory.search(SearchMemoryRequest(query=question, limit=limit, mode="fts"))
    citations: list[MemoryCitation] = []
    seen: set[tuple[str, int, str]] = set()
    for hit in page.hits:
        citation = MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )
        identity = (str(hit.memory_ref.artifact_id), hit.memory_ref.revision, hit.entry_id)
        if identity not in seen:
            citations.append(citation)
            seen.add(identity)
        if len(citations) >= limit:
            break
    return tuple(citations)
