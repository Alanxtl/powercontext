# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

from powercontext import ArtifactRef
from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.sources.pdf_pages import (
    _pdf_bm25_rank,
    _pdf_exclusive_documents,
    _pdf_named_documents,
    _pdf_page_document,
    _pdf_query_terms,
    _pdf_term_stem,
    _pdf_term_variants,
)


def _hit(entry_id: str, text: str) -> MemoryHit:
    return MemoryHit(
        memory_ref=ArtifactRef(family="memory", artifact_id="memory", revision=1),
        entry_id=entry_id,
        entry_version_id=f"version-{entry_id}",
        text=text,
        score=1 / 61,
        matched_by=("fts",),
    )


def test_pdf_query_terms_casefold_before_tokenizing() -> None:
    assert _pdf_query_terms("What are the geographies that Northwind primarily operates in as of FY2024?") == (
        "geographies",
        "northwind",
        "primarily",
        "operates",
        "fy",
        "2024",
    )


def test_pdf_term_stemming_covers_common_financial_word_forms() -> None:
    assert [_pdf_term_stem(term) for term in ("reported", "materially", "geographies", "operates")] == [
        "report",
        "material",
        "geography",
        "operate",
    ]


def test_pdf_term_variants_cover_singular_and_plural_spellings() -> None:
    assert "liabilities" in _pdf_term_variants("liability")
    assert "liability" in _pdf_term_variants("liabilities")
    assert "balances" in _pdf_term_variants("balance")
    assert "sheets" in _pdf_term_variants("sheet")
    assert "balance" in _pdf_term_variants("balances")


def test_pdf_ranking_prefers_complete_nearby_query_evidence() -> None:
    evidence = _hit("evidence", "FY2022 income statement lists restructuring costs of $42 million.")
    noisy = _hit("noisy", ("restructuring " * 40) + "costs occur elsewhere, while the report covers 2022.")
    pool = {
        (hit.memory_ref.artifact_id, hit.memory_ref.revision, hit.entry_id, hit.entry_version_id): hit.text
        for hit in (noisy, evidence)
    }

    ranked = _pdf_bm25_rank(pool, {"restructuring": 1.0, "costs": 1.0, "2022": 1.0}, 2)

    assert ranked[0][2] == "evidence"


def test_pdf_ranking_folds_the_other_spelling_of_a_question_word() -> None:
    plural = _hit("plural", "CONSOLIDATED BALANCE SHEETS Liabilities total one hundred.")
    unrelated = _hit("unrelated", "The balance of the discussion is about markets and regions.")
    pool = {
        (hit.memory_ref.artifact_id, hit.memory_ref.revision, hit.entry_id, hit.entry_version_id): hit.text
        for hit in (unrelated, plural)
    }

    ranked = _pdf_bm25_rank(
        pool,
        {"liability": 1.0},
        2,
        groups={"liability": ("liability", "liabilities")},
    )

    assert ranked[0][2] == "plural"


def test_pdf_ranking_prefers_the_page_with_the_statement_wording() -> None:
    statement = _hit(
        "statement",
        "Total current assets 5,308 Total current liabilities 4,120 Quick ratio 1.2",
    )
    narrative = _hit(
        "narrative",
        "Our liquidity is strong and our current position is good. " * 8,
    )
    pool = {
        (hit.memory_ref.artifact_id, hit.memory_ref.revision, hit.entry_id, hit.entry_version_id): hit.text
        for hit in (narrative, statement)
    }

    ranked = _pdf_bm25_rank(
        pool,
        {"quick": 1.0, "ratio": 1.0},
        2,
        expansion_phrases=(("current", "assets"), ("current", "liabilities")),
    )

    assert ranked[0][2] == "statement"


def test_pdf_page_document_reads_the_document_of_a_projected_page() -> None:
    assert _pdf_page_document("ACME_2024_10K\nNet revenue of the fiscal year.") == (
        "ACME_2024_10K"
    )
    assert _pdf_page_document("ACME_2024_10K\n") == "ACME_2024_10K"
    assert _pdf_page_document("a page without a document line") == (
        "a page without a document line"
    )


def test_pdf_named_documents_matches_the_name_of_a_filing() -> None:
    documents = ("NORTHWIND_2024_10K", "BLUERIDGE_2024_10K", "CRESTVIEW_2024_10K")

    assert _pdf_named_documents(documents, "What did Blue Ridge report in FY2024?") == (
        "BLUERIDGE_2024_10K",
    )
    assert _pdf_named_documents(documents, "What are the geographies Northwind operates in?") == (
        "NORTHWIND_2024_10K",
    )
    assert _pdf_named_documents(documents, "What is the operating margin profile?") == ()


def test_pdf_named_documents_reads_a_three_letter_name_as_a_whole_word() -> None:
    documents = ("ZYX_2024_10K", "GRANITE_2024_10K")

    assert _pdf_named_documents(documents, "Does ZYX have a healthy liquidity profile?") == (
        "ZYX_2024_10K",
    )
    assert _pdf_named_documents(documents, "Did Granite improve its quick ratio?") == (
        "GRANITE_2024_10K",
    )


def test_pdf_exclusive_documents_finds_the_filing_of_a_word_it_uses_alone() -> None:
    pages = {
        ("a", 1, f"crv-{index}", "v"): (
            f"CRESTVIEW_2024_10K\nCRV reported a better operating margin, page {index}."
        )
        for index in range(6)
    }
    pages[("a", 1, "granite", "v")] = "GRANITE_2024_10K\nCRV is only mentioned once here."
    pool = dict(pages)
    documents = {key: _pdf_page_document(text) for key, text in pool.items()}

    assert _pdf_exclusive_documents(pool, documents, ("crv", "margin")) == (
        "CRESTVIEW_2024_10K",
    )


def test_pdf_ranking_gives_every_filing_its_own_statistics() -> None:
    answer = _hit("answer", "CRESTVIEW_2024_10K\nQuick ratio of 2.4 with current assets and liabilities.")
    other = _hit("other", "NORTHWIND_2024_10K\nThe quick ratio is not discussed on this page.")
    pool = {
        (hit.memory_ref.artifact_id, hit.memory_ref.revision, hit.entry_id, hit.entry_version_id): hit.text
        for hit in (other, answer)
    }
    documents = {key: _pdf_page_document(text) for key, text in pool.items()}

    ranked = _pdf_bm25_rank(
        pool,
        {"quick": 1.0, "ratio": 1.0},
        2,
        documents=documents,
    )

    assert ranked[0][2] == "answer"
