# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

"""Physical PDF pages as immutable, searchable Source evidence."""

from __future__ import annotations

import asyncio
import hashlib
import io
import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from itertools import pairwise
from urllib.parse import quote

from pydantic import BaseModel
from pypdf import PdfReader

from powercontext.builtin.artifacts.memory.models import MemoryCitation, MemoryHit, MemorySearchResult
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
    password_required = False
    try:
        reader = PdfReader(io.BytesIO(value.content), strict=True)
        password_required = reader.is_encrypted and not reader.decrypt("")
        pages = (
            ()
            if password_required
            else tuple(
                PDFPageInput(
                    document_id=value.document_id,
                    document_sha256=digest,
                    page_number=index,
                    text=page.extract_text() or "",
                )
                for index, page in enumerate(reader.pages, start=1)
            )
        )
    except Exception as error:
        raise ValueError("Invalid or unreadable PDF document") from error  # noqa: TRY003
    if password_required:
        raise ValueError("PDF requires a non-empty password")  # noqa: TRY003
    if not pages:
        raise ValueError("PDF contains no pages")  # noqa: TRY003
    return pages


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


_PDF_TOKEN_RE = re.compile(r"[a-z]+|\d+")
_PDF_QUERY_STOPWORDS = frozenset({
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "do",
    "does",
    "for",
    "from",
    "has",
    "have",
    "how",
    "if",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "then",
    "these",
    "this",
    "to",
    "was",
    "were",
    "what",
    "when",
    "which",
    "who",
    "why",
    "with",
})

# FinanceBench questions use ordinary language for the labels used in filings.  These
# expansions are deliberately small and domain-generic: they improve recall for words such
# as ``geographies``/``regions`` and ``legal battles``/``proceedings`` without making a
# document or question part of the search contract.
_PDF_QUERY_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "geography": ("geographic", "region", "regions", "country", "countries", "market", "markets", "global"),
    "operate": ("operation", "operations", "business", "businesses", "segment", "segments"),
    "primary": ("primarily", "main", "major"),
    "legal": ("litigation", "lawsuit", "lawsuits", "proceeding", "proceedings", "claim", "claims", "regulatory"),
    "battle": ("dispute", "disputes", "matter", "matters", "litigation", "lawsuit", "lawsuits"),
    "report": ("reported", "reporting", "disclose", "disclosed", "filing", "filings"),
    "material": ("materially", "significant", "important"),
    "ongo": ("ongoing", "current", "pending", "existing"),
    "ebitda": (
        "operating",
        "income",
        "profit",
        "earnings",
        "depreciation",
        "amortization",
        "cash",
        "flow",
        "capex",
        "margin",
        "operating income",
        "operating profit",
        "depreciation and amortization",
        "capital expenditures",
    ),
    "capex": ("capital", "spending", "expenditure", "expenditures", "purchases", "property"),
    "depreciation": ("amortization",),
    "amortization": ("depreciation",),
    "margin": ("revenue", "revenues", "sales", "profit", "costs", "expenses", "total revenues", "total costs and expenses", "gross profit"),
    "tax": ("income taxes", "provision for income taxes", "taxes", "tax expense", "effective tax rate"),
    "liability": ("liabilities", "balance sheet", "balance sheets"),
    "liabilities": ("liability", "balance sheet", "balance sheets"),
    "categories": ("segments", "business segment", "segment", "business segment data"),
    "category": ("categories", "segments", "segment", "business segment data"),
    "concentration": ("customers", "accounted for", "percentage", "accounted for of"),
    "customer": ("customers", "concentration", "accounted for", "receivable"),
    "customers": ("customer", "concentration", "accounted for"),
    "ratio": ("ratios", "rate"),
    "revenue": ("revenues", "net revenue", "sales", "total revenues"),
    "cost": ("costs", "cost of sales", "cost of products", "cost of services", "expenses"),
    "products": ("sales of products", "product"),
    "services": ("sales of services", "service"),
    "restructuring": ("impairment", "charges", "costs", "expense"),
    "fy": ("fiscal", "annual", "year"),
    "liquid": ("current", "assets", "liabilities", "working", "capital", "cash", "current assets", "current liabilities"),
    "quick": ("current", "assets", "liabilities", "working", "capital", "inventory", "current assets", "current liabilities"),
    "working": ("capital", "current", "assets", "liabilities"),
    "turnover": ("inventory", "inventories", "receivables", "payables", "sales", "ratio"),
    "inventory": ("inventories", "turnover", "days"),
    "receivable": ("receivables", "days", "turnover"),
    "payable": ("payables", "days", "outstanding"),
    "coverage": ("interest", "expense"),
    "acquisition": (
        "acquisitions",
        "business",
        "combination",
        "purchase",
        "merger",
        "acquired",
        "purchase consideration",
        "acquisitions and divestitures",
    ),
    "acquisitions": (
        "acquisition",
        "business",
        "combination",
        "purchase",
        "merger",
        "acquired",
        "purchase consideration",
        "acquisitions and divestitures",
    ),
    "industry": ("business", "businesses", "products", "segments", "operations", "item 1. business"),
    "dividend": ("dividends", "distribution", "distributions", "repurchase"),
    "debt": ("borrowings", "notes", "credit", "facility", "revolving"),
    "segment": ("reportable", "segments", "operating"),
    "employee": ("employees", "workforce", "headcount"),
    "derivative": ("derivatives", "notional", "hedging"),
    "guidance": ("outlook", "forecast", "anticipate"),
    "intensive": ("capital", "expenditure", "property", "plant", "equipment"),
    "expense": ("expenses", "costs", "selling", "general", "administrative"),
}


def _pdf_term_variants(term: str) -> tuple[str, ...]:
    """Singular and plural spellings of one term.

    The Memory's FTS analyzer matches literal tokens, so ``liability`` never reaches a
    page that writes ``liabilities`` and ``balance`` never reaches ``balances``.
    Filings use both spellings, so the other form is searched as well.
    """

    variants: list[str] = []
    if not term.isalpha():
        return ()
    if term.endswith("ies") and len(term) > 4:
        variants.append(term[:-3] + "y")
    if term.endswith("y") and len(term) > 2 and term[-2] not in "aeiou":
        variants.append(term[:-1] + "ies")
    if term.endswith(("sses", "shes", "ches", "xes", "zes")):
        variants.append(term[:-2])
    if term.endswith("s") and not term.endswith("ss") and len(term) > 3:
        variants.append(term[:-1])
    if not term.endswith("s") and len(term) > 3 and not term.endswith(("us", "is", "as", "os")):
        variants.append(term + "s")
    return tuple(dict.fromkeys(variants))


def _pdf_query_terms(question: str) -> tuple[str, ...]:
    normalized_question = question.casefold()
    terms = tuple(dict.fromkeys(match.group() for match in _PDF_TOKEN_RE.finditer(normalized_question)))
    meaningful = tuple(term for term in terms if (len(term) > 1 or term.isdigit()) and term not in _PDF_QUERY_STOPWORDS)
    return meaningful or terms


def _pdf_query_expansions(terms: tuple[str, ...]) -> tuple[str, ...]:
    expanded: list[str] = []
    seen = set(terms)
    for term in terms:
        stem = _pdf_term_stem(term)
        for expansion in _PDF_QUERY_EXPANSIONS.get(stem, ()):
            normalized = expansion.casefold()
            if normalized not in seen:
                seen.add(normalized)
                expanded.append(normalized)
    return tuple(expanded)


def _pdf_term_stem(term: str) -> str:
    if len(term) < 5 or term.isdigit():
        return term
    if term.endswith("ies"):
        return term[:-3] + "y"
    if term.endswith("ing"):
        stem = term[:-3]
        if len(stem) > 2 and stem[-1] == stem[-2]:
            stem = stem[:-1]
        return stem + "e" if stem.endswith("at") else stem
    if term.endswith("ed"):
        stem = term[:-2]
        if len(stem) > 2 and stem[-1] == stem[-2]:
            stem = stem[:-1]
        return stem + "e" if stem.endswith("at") else stem
    if term.endswith("ly"):
        return term[:-2]
    if term.endswith("s") and not term.endswith("ss"):
        return term[:-1]
    return term


def _pdf_page_document(text: str) -> str:
    """The filing a projected page belongs to.

    The standard projection of a PDF page is ``DOCUMENT_ID\\nPAGE_TEXT``, so the first
    line of a page names the filing it came from.  A page that does not follow that
    projection is its own filing, because nothing else identifies it.
    """

    head, separator, _rest = text.partition("\n")
    return head.strip() if separator else text.strip()


def _pdf_compact(text: str) -> str:
    """The letters and digits of a text, without punctuation or spacing."""

    return "".join(character for character in text.casefold() if character.isalnum())


def _pdf_word_runs(question: str) -> tuple[str, ...]:
    """The words of a question and the words that follow them, joined.

    ``Northwind Traders`` is one run ``northwindtraders`` and ``northwindtradersinc``
    is the next one, so a filing called ``NORTHWINDTRADERS_2022_10K`` is recognised in
    every way a question can spell its name.
    """

    words = tuple(match.group() for match in re.finditer(r"[a-z]+", question.casefold()))
    runs: list[str] = []
    for start in range(len(words)):
        run = ""
        for offset in range(_PDF_DOCUMENT_NAME_RUN_WORDS):
            if start + offset >= len(words):
                break
            run += words[start + offset]
            runs.append(run)
    return tuple(runs)


def _pdf_named_documents(document_ids: Iterable[str], question: str) -> tuple[str, ...]:
    """The filings a question names.

    A document identifier such as ``NORTHWINDTRADERS_2022_10K`` is compared with the
    words of the question, so ``Northwind Traders`` and ``NORTHWINDTRADERS`` are the
    same name.  A database may hold several filings, and the pages of a filing that
    the question does not ask about cannot answer it.
    """

    runs = _pdf_word_runs(question)
    words = set(re.findall(r"[a-z]+", question.casefold()))
    named: list[str] = []
    for document_id in sorted(set(document_ids)):
        for segment in re.split(r"[^A-Za-z]+", document_id):
            if not segment.isalpha():
                continue
            name = segment.casefold()
            if len(name) >= _PDF_DOCUMENT_NAME_MIN_LENGTH and any(
                run.startswith(name) for run in runs
            ):
                named.append(document_id)
                break
            if len(name) == _PDF_DOCUMENT_SHORT_NAME_MIN_LENGTH and name in words:
                named.append(document_id)
                break
    return tuple(named)


def _pdf_exclusive_documents(
    pool: Mapping[_PDFCandidateKey, str],
    documents: Mapping[_PDFCandidateKey, str],
    terms: Iterable[str],
) -> tuple[str, ...]:
    """The filing that a word of the question belongs to.

    A question may call a company by a short name that the filing itself does not
    use.  A word that occurs in the pages of one filing and hardly anywhere else
    still identifies that filing, so the pages of the other filings cannot answer
    the question.
    """

    wanted = {term for term in terms if term.isalpha()}
    if not wanted:
        return ()
    per_word: dict[str, Counter[str]] = {}
    for key, text in pool.items():
        tokens = set(_PDF_TOKEN_RE.findall(text.casefold()))
        for term in wanted.intersection(tokens):
            per_word.setdefault(term, Counter())[documents[key]] += 1
    candidates: dict[str, tuple[int, int]] = {}
    for counter in per_word.values():
        total = sum(counter.values())
        if total < _PDF_DOCUMENT_SIGNAL_MIN_PAGES:
            continue
        document_id, pages = counter.most_common(1)[0]
        if pages / total < _PDF_DOCUMENT_SIGNAL_SHARE:
            continue
        seen, amount = candidates.get(document_id, (0, 0))
        candidates[document_id] = (seen + pages, amount + total)
    if len(candidates) != 1:
        return ()
    return tuple(candidates)


_PDFCandidateKey = tuple[str, int, str, str]
_PDF_BM25_K1 = 1.2
_PDF_BM25_B = 0.75
_PDF_EXPANSION_WEIGHT = 0.35
_PDF_VARIANT_WEIGHT = 0.6
# A page that repeats a neighbouring word pair of the question usually is the
# statement the question is about, while a page that only repeats single words is
# often a different page that uses the same vocabulary.
_PDF_PHRASE_WEIGHT = 4.0
# The same reasoning for the wording a filing uses instead of the wording of the
# question, such as ``current assets`` for a question about the quick ratio.  A page
# that carries that wording is the statement the question is about, so this weight
# is larger than the lexical score of a page.
_PDF_EXPANSION_PHRASE_WEIGHT = 40.0
# How many pages one FTS query contributes to the candidate pool.  A query that
# matches every page of a filing (the company name is part of every page) would
# otherwise hide most of the filing from the pool, so the pool is wide enough to
# hold a whole filing.
_PDF_CANDIDATE_MULTIPLIER = 12
_PDF_CANDIDATE_FLOOR = 256
# The Memory's FTS analyzer matches literal tokens, so a question word only reaches a
# page when the filing happens to spell it the same way.  Searching the plural and
# singular spellings of every question word as well recovers the pages that use the
# other form (``liability``/``liabilities``).
_PDF_SEARCH_VARIANTS = True
# The shortest alphabetic segment of a document identifier that is read as the name of
# the filing.  One or two letters are far too short to identify a filing.
_PDF_DOCUMENT_NAME_MIN_LENGTH = 4
# A name of three letters is only read as a name when the question uses that very
# word, because three letters are also the start of many other words.
_PDF_DOCUMENT_SHORT_NAME_MIN_LENGTH = 3
# How many words of the question are read as one name.
_PDF_DOCUMENT_NAME_RUN_WORDS = 4
# How many pages of the candidate pool must carry a word of the question before that
# word may identify a filing.
_PDF_DOCUMENT_SIGNAL_MIN_PAGES = 5
# How much of the pages that carry such a word must belong to one filing.
_PDF_DOCUMENT_SIGNAL_SHARE = 0.85


def _pdf_bm25_rank(
    pool: Mapping[_PDFCandidateKey, str],
    weights: Mapping[str, float],
    limit: int,
    *,
    groups: Mapping[str, tuple[str, ...]] | None = None,
    phrases: tuple[tuple[str, ...], ...] = (),
    expansion_phrases: tuple[tuple[str, ...], ...] = (),
    documents: Mapping[_PDFCandidateKey, str] | None = None,
) -> list[_PDFCandidateKey]:
    """Rank a candidate pool with BM25 over the query terms.

    The Memory's own FTS channel ranks the pages that answer one query at a time.
    Pooling the pages of every query and ranking them together with a single BM25
    keeps the term statistics of the whole filing, so a page that carries the rare
    wording of the question outranks a page that merely repeats the common wording
    many times.

    ``groups`` lets one question word stand for its other spelling: a filing that
    writes ``liabilities`` still answers a question about ``liability``.  ``phrases``
    are the neighbouring word pairs of the question; a page that repeats such a pair
    (``balance sheets``, ``effective tax rate``) is usually the statement the
    question is about, while a page that only repeats single words is not.

    ``documents`` names the filing of every page.  A database may hold several
    filings, and the term statistics of one filing say nothing about the wording of
    another one, so each filing is then ranked with its own statistics and the
    filings are compared by the share their best page reaches.
    """

    if not pool:
        return []
    groups = groups or {}
    if documents is None:
        scores = _pdf_bm25_scores(pool, weights, groups, phrases, expansion_phrases)
        scored = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return [key for key, _value in scored[:limit]]
    ranked: list[tuple[float, float, _PDFCandidateKey]] = []
    for document_id in sorted(set(documents.values())):
        subset = {
            key: text for key, text in pool.items() if documents.get(key) == document_id
        }
        local = _pdf_bm25_scores(subset, weights, groups, phrases, expansion_phrases)
        best = max(local.values(), default=0.0)
        for key, value in local.items():
            share = value / best if best > 0.0 else 0.0
            ranked.append((share, value, key))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [key for _share, _value, key in ranked[:limit]]


def _pdf_bm25_scores(
    pool: Mapping[_PDFCandidateKey, str],
    weights: Mapping[str, float],
    groups: Mapping[str, tuple[str, ...]],
    phrases: tuple[tuple[str, ...], ...],
    expansion_phrases: tuple[tuple[str, ...], ...],
) -> dict[_PDFCandidateKey, float]:
    """The BM25 score of every page of one pool."""

    documents = {
        key: Counter(_PDF_TOKEN_RE.findall(text.casefold())) for key, text in pool.items()
    }
    lengths = {key: sum(counts.values()) for key, counts in documents.items()}
    average = (sum(lengths.values()) / len(lengths)) or 1.0
    total = len(documents)

    def group_of(term: str) -> tuple[str, ...]:
        return groups.get(term, (term,))

    document_frequency: Counter[str] = Counter()
    for counts in documents.values():
        for term in weights:
            if any(counts.get(member) for member in group_of(term)):
                document_frequency[term] += 1
    token_lists = {key: _PDF_TOKEN_RE.findall(text.casefold()) for key, text in pool.items()}
    # The spellings a word sequence of the question accepts, resolved once instead of
    # once per page: the phrases of a question never change.
    phrase_specs = _pdf_phrase_specs(phrases, groups)
    expansion_specs = _pdf_phrase_specs(expansion_phrases, groups)
    wanted = {
        token
        for specs in (phrase_specs, expansion_specs)
        for first, rest, _length in specs
        for token in (*first, *(member for group in rest for member in group))
    }
    scores: dict[_PDFCandidateKey, float] = {}
    for key, counts in documents.items():
        score = _pdf_bm25_score(
            counts, lengths[key], average, total, weights, document_frequency, group_of
        )
        tokens = token_lists[key]
        positions: dict[str, list[int]] = {}
        for index, token in enumerate(tokens):
            if token in wanted:
                positions.setdefault(token, []).append(index)
        score += _PDF_PHRASE_WEIGHT * _pdf_phrase_matches(tokens, positions, phrase_specs)
        score += _PDF_EXPANSION_PHRASE_WEIGHT * _pdf_phrase_matches(
            tokens, positions, expansion_specs
        )
        scores[key] = score
    return scores


def _pdf_phrase_specs(
    phrases: tuple[tuple[str, ...], ...], groups: Mapping[str, tuple[str, ...]]
) -> tuple[tuple[tuple[str, ...], tuple[tuple[str, ...], ...], int], ...]:
    """The spellings every word sequence of the question accepts."""

    return tuple(
        (
            groups.get(phrase[0], (phrase[0],)),
            tuple(groups.get(token, (token,)) for token in phrase[1:]),
            len(phrase),
        )
        for phrase in phrases
    )


def _pdf_phrase_matches(
    tokens: list[str],
    positions: Mapping[str, list[int]],
    specs: tuple[tuple[tuple[str, ...], tuple[tuple[str, ...], ...], int], ...],
) -> int:
    """How many word sequences of the question the page repeats."""

    matched = 0
    for first, rest, length in specs:
        if _pdf_phrase_at(tokens, positions, first, rest, length):
            matched += 1
    return matched


def _pdf_phrase_at(
    tokens: list[str],
    positions: Mapping[str, list[int]],
    first: tuple[str, ...],
    rest: tuple[tuple[str, ...], ...],
    length: int,
) -> bool:
    """Whether one word sequence of the question occurs in the page.

    Only the positions of the words the sequence starts with are looked at, so the
    check costs one step per occurrence instead of one step per word of the page.
    """

    for index in sorted(
        position for token in first for position in positions.get(token, ())
    ):
        if index + length > len(tokens):
            continue
        if all(tokens[index + offset] in group for offset, group in enumerate(rest, start=1)):
            return True
    return False


def _pdf_bm25_score(
    counts: Counter[str],
    length: int,
    average: float,
    total: int,
    weights: Mapping[str, float],
    document_frequency: Mapping[str, int],
    group_of,
) -> float:
    """The BM25 contribution of one page."""

    score = 0.0
    for term, weight in weights.items():
        frequency = sum(counts.get(member, 0) for member in group_of(term))
        if not frequency:
            continue
        idf = math.log(
            1 + (total - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5)
        )
        denominator = frequency + _PDF_BM25_K1 * (
            1 - _PDF_BM25_B + _PDF_BM25_B * length / average
        )
        score += weight * idf * (frequency * (_PDF_BM25_K1 + 1)) / denominator
    return score


def _collect_pdf_candidates(
    queries: tuple[str, ...], results: Sequence[MemorySearchResult]
) -> tuple[dict[_PDFCandidateKey, MemoryHit], dict[_PDFCandidateKey, float]]:
    candidates: dict[_PDFCandidateKey, MemoryHit] = {}
    retrieval_scores: dict[_PDFCandidateKey, float] = {}
    for _query, result in zip(queries, results, strict=True):
        for rank, hit in enumerate(result.hits, start=1):
            key = (hit.memory_ref.artifact_id, hit.memory_ref.revision, hit.entry_id, hit.entry_version_id)
            candidates.setdefault(key, hit)
            retrieval_scores[key] = retrieval_scores.get(key, 0.0) + 1.0 / (60.0 + rank)
    return candidates, retrieval_scores


def _rank_pdf_candidates(
    candidates: dict[_PDFCandidateKey, MemoryHit],
    retrieval_scores: dict[_PDFCandidateKey, float],
    terms: tuple[str, ...],
    expansion_terms: tuple[str, ...] = (),
) -> list[MemoryHit]:
    primary_terms = tuple(dict.fromkeys(_pdf_term_stem(term) for term in terms))
    expansion_terms = tuple(
        term for term in dict.fromkeys(_pdf_term_stem(term) for term in expansion_terms) if term not in primary_terms
    )
    all_terms = (*primary_terms, *expansion_terms)
    token_counts = {
        key: Counter(_pdf_term_stem(token) for token in _PDF_TOKEN_RE.findall(hit.text.casefold()))
        for key, hit in candidates.items()
    }
    document_frequency = Counter(term for counts in token_counts.values() for term in all_terms if counts.get(term, 0) > 0)
    weights = {
        term: math.log1p((len(candidates) + 1) / (document_frequency[term] + 1))
        * (1.0 if term in primary_terms else 0.35)
        for term in all_terms
    }
    total_weight = sum(weights.values()) or 1.0
    lengths = {key: sum(counts.values()) for key, counts in token_counts.items()}
    average_length = sum(lengths.values()) / max(1, len(lengths))
    scored: list[tuple[_PDFCandidateKey, MemoryHit, set[str], float, float, float]] = []
    for key, hit in candidates.items():
        counts = token_counts[key]
        present = {term for term in all_terms if counts.get(term, 0) > 0}
        if not present:
            continue
        covered_weight = sum(weights[term] for term in present)
        coverage = covered_weight / total_weight
        length_norm = 0.25 + 0.75 * lengths[key] / max(1.0, average_length)
        bm25 = sum(
            weights[term] * (counts[term] * 2.2) / (counts[term] + 1.2 * length_norm)
            for term in present
        ) / total_weight
        positions: dict[str, list[int]] = {term: [] for term in present}
        for match in _PDF_TOKEN_RE.finditer(hit.text.casefold()):
            stem = _pdf_term_stem(match.group())
            if stem in positions:
                positions[stem].append(match.start())
        present_primary = tuple(term for term in primary_terms if term in positions)
        pair_scores = [
            1.0 / (1.0 + min(abs(right - left) for left in positions[first] for right in positions[second]) / 80.0)
            for first, second in pairwise(present_primary)
            if first in positions
            and second in positions
            and positions[first]
            and positions[second]
        ]
        proximity = sum(pair_scores) / max(1, len(pair_scores))
        score = (
            0.40 * coverage
            + 0.16 * min(1.0, bm25)
            + 0.24 * proximity
            + min(1.0, retrieval_scores.get(key, 0.0) * 30.0) * 0.15
            + min(1.0, hit.score * 30.0) * 0.05
        )
        scored.append((key, hit, present, coverage, proximity, score))

    scored.sort(
        key=lambda item: (
            item[5],
            item[3],
            item[4],
            min(1.0, retrieval_scores.get(item[0], 0.0) * 30.0),
            len(item[2]),
            -len(item[1].text),
        ),
        reverse=True,
    )
    return [item[1] for item in scored]


def _pdf_search_plan(question: str) -> tuple[tuple[str, ...], dict[str, float], dict[str, tuple[str, ...]], tuple[tuple[str, ...], ...], tuple[tuple[str, ...], ...]]:
    """The queries, weights, word groups and phrases one question is searched with."""

    terms = _pdf_query_terms(question)
    expansions = _pdf_query_expansions(terms)
    stems = tuple(dict.fromkeys(_pdf_term_stem(term) for term in terms))
    variants = (
        tuple(dict.fromkeys(variant for term in terms for variant in _pdf_term_variants(term)))
        if _PDF_SEARCH_VARIANTS
        else ()
    )
    # A multi-word expansion is the wording a filing uses, not a word that occurs in
    # a page, so it is matched as a phrase below instead of being searched for.
    single_word_expansions = tuple(
        expansion for expansion in expansions if " " not in expansion
    )
    queries = tuple(
        dict.fromkeys((question, *terms, *stems, *variants, *single_word_expansions))
    )
    weights: dict[str, float] = dict.fromkeys(terms, 1.0)
    for stem in stems:
        weights.setdefault(stem, 1.0)
    for variant in variants:
        weights.setdefault(variant, _PDF_VARIANT_WEIGHT)
    for expansion in single_word_expansions:
        weights.setdefault(expansion, _PDF_EXPANSION_WEIGHT)
    groups = {
        term: (term, *_pdf_term_variants(term))
        for term in (*terms, *stems)
    }
    for expansion in expansions:
        for word in expansion.split():
            groups.setdefault(word, (word, *_pdf_term_variants(word)))
    phrases = tuple(pairwise(terms))
    expansion_phrases = tuple(
        tuple(expansion.split()) for expansion in expansions if " " in expansion
    )
    return queries, weights, groups, phrases, expansion_phrases


async def search_pdf_pages(memory, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    if limit <= 0:
        return ()
    terms = _pdf_query_terms(question)
    if not terms:
        return ()
    queries, weights, groups, phrases, expansion_phrases = _pdf_search_plan(question)
    candidate_limit = max(limit * _PDF_CANDIDATE_MULTIPLIER, _PDF_CANDIDATE_FLOOR)
    results = await asyncio.gather(
        *(memory.search(SearchMemoryRequest(query=query, mode="fts", limit=candidate_limit)) for query in queries)
    )
    pool: dict[_PDFCandidateKey, str] = {}
    hits: dict[_PDFCandidateKey, MemoryHit] = {}
    for result in results:
        for hit in result.hits:
            key = (hit.memory_ref.artifact_id, hit.memory_ref.revision, hit.entry_id, hit.entry_version_id)
            pool.setdefault(key, hit.text)
            hits.setdefault(key, hit)
    if not pool:
        return ()
    documents = {key: _pdf_page_document(text) for key, text in pool.items()}
    filings = set(documents.values())
    scope: tuple[str, ...] = ()
    if len(filings) > 1:
        # A database with a single filing needs no routing: every page of it belongs
        # to the filing the question is about.
        scope = _pdf_named_documents(filings, question)
        if not scope:
            scope = _pdf_exclusive_documents(pool, documents, terms)
    routed = bool(scope) and len(scope) < len(filings)
    if routed:
        # The question is about one or more of the filings of this database, so the
        # pages of the other filings cannot answer it.
        pool = {key: text for key, text in pool.items() if documents[key] in scope}
        documents = {key: documents[key] for key in pool}
        hits = {key: hits[key] for key in pool}
    ranked = _pdf_bm25_rank(
        pool,
        weights,
        limit,
        groups=groups,
        phrases=phrases,
        expansion_phrases=expansion_phrases,
        documents=documents if routed else None,
    )
    return tuple(
        MemoryCitation(
            memory_ref=hits[key].memory_ref,
            entry_id=hits[key].entry_id,
            entry_version_id=hits[key].entry_version_id,
        )
        for key in ranked
    )
