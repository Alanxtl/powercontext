# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

"""Physical PDF pages as immutable, searchable Source evidence."""

from __future__ import annotations

import asyncio
import hashlib
import io
import re
from urllib.parse import quote

from pydantic import BaseModel
from pypdf import PdfReader

from powercontext.builtin.artifacts.memory.models import MemoryCitation
from powercontext.builtin.runtime import SearchMemoryRequest
from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence


class PDFDocumentInput(BaseModel):
    document_id: str
    content: bytes


class PDFPageInput(BaseModel):
    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageSource(Source):
    document_id: str
    document_sha256: str
    page_number: int
    text: str


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    if not value.document_id.strip() or not value.content:
        raise ValueError("PDF document ID and content must be non-empty")  # noqa: TRY003
    digest = hashlib.sha256(value.content).hexdigest()
    try:
        reader = PdfReader(io.BytesIO(value.content), strict=True)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF requires a non-empty password")  # noqa: TRY003
        pages = tuple(
            PDFPageInput(
                document_id=value.document_id,
                document_sha256=digest,
                page_number=index,
                text=page.extract_text() or "",
            )
            for index, page in enumerate(reader.pages, start=1)
        )
        if not pages:
            raise ValueError("PDF contains no pages")  # noqa: TRY003
        return pages
    except Exception as error:
        raise ValueError("Invalid or unreadable PDF document") from error  # noqa: TRY003


class PDFPageSourceAdapter:
    input_class = PDFPageInput
    name = "pdf-page"
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        identity = f"pdf:{quote(value.document_id, safe='')}:{value.document_sha256}:{value.page_number}"
        if len(identity) > MAX_SOURCE_ID_LENGTH:
            raise ValueError("PDF page identity exceeds the Source limit")  # noqa: TRY003
        return PDFPageSource(name=identity, materialization=SourceMaterialization.CAPTURED, **value.model_dump())

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput.model_validate(source.model_dump(include=set(PDFPageInput.model_fields)))


class PDFPageTextEvidenceProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

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


PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    PDFPageSourceAdapter(), projections=(PDFPageTextEvidenceProjection(),)
)


async def search_pdf_pages(memory, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    if limit <= 0:
        return ()
    terms = tuple(dict.fromkeys(re.findall(r"[a-z]+|\d+", question.lower())))
    queries = (question, *[term for term in terms if len(term) > 2][:12])
    results = await asyncio.gather(
        *(memory.search(SearchMemoryRequest(query=query, mode="fts", limit=max(limit, 20))) for query in queries)
    )
    candidates = {}
    for result in results:
        for hit in result.hits:
            key = (hit.entry_id, hit.entry_version_id)
            text = hit.text.lower()
            overlap = sum(text.count(term) for term in terms)
            candidates[key] = (overlap, hit)
    seen: set[tuple[str, str]] = set()
    citations: list[MemoryCitation] = []
    for _, hit in sorted(candidates.values(), key=lambda item: (-item[0], -item[1].score))[:limit]:
        key = (hit.entry_id, hit.entry_version_id)
        if key in seen:
            continue
        seen.add(key)
        citations.append(
            MemoryCitation(memory_ref=hit.memory_ref, entry_id=hit.entry_id, entry_version_id=hit.entry_version_id)
        )
    return tuple(citations)
