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

"""Prepare final, bounded context from approved Artifact evidence."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from itertools import chain, pairwise
from typing import TypeVar

from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.builtin.artifacts.experience import Experience, ExperienceSearchHit, render_experience
from powercontext.builtin.artifacts.memory.models import MemoryCitation, MemoryHit
from powercontext.builtin.artifacts.profile.models import Profile
from powercontext.builtin.artifacts.topic_memory import TopicMemory, TopicMemorySearchHit
from powercontext.builtin.runtime.errors import PreparedContextInvariantError
from powercontext.builtin.runtime.models import PrepareContextRequest, PreparedContext
from powercontext.builtin.runtime.prepared_code import (
    CodeEvidenceRef,
    PreparedCodeCandidate,
    assemble_code,
)
from powercontext.builtin.runtime.prepared_text import (
    TRUST_POLICY,
    ContextTextItem,
    fit_context_text_item,
    render_context_text,
)
from powercontext.builtin.runtime.recall_sufficiency import RecallBudgetView

_MIN_TRUNCATED_CONTENT_BYTES = 64
# A window of two or three sentences is the longest one that is still worth a citation
# of its own: a longer window spends the budget that a second, separate statement of
# the same abstract needs.  Two windows that touch are merged when they are scored, so
# a longer stretch of text is still covered by two cheap windows.
_SELECTION_RUN_LENGTHS = (2, 3, 4)
_SELECTION_RUN_REQUIRES_NEW_TERMS = False
_SELECTION_EXTEND_SENTENCES = 3
_SELECTION_EXTEND_FIRST_ONLY = False
# The retrieval stage's own ranking is a prior that the selection stage keeps: the
# first candidate of the retrieval order is discounted gently, so a window of the
# abstract that the retrieval stage ranked first stays the best candidate.
_SELECTION_HIT_WEIGHT = "rank"
# How strongly the retrieval order still counts once the content signal is known.
# The content signal ranks the abstracts by the query terms they actually carry;
# the retrieval order only breaks a tie, because the retrieval stage compares whole
# entries while the evidence of one claim lives in a few sentences of one abstract.
_SELECTION_ORDER_TIEBREAK = 0.0
# A query term that only one candidate entry carries is the strongest hint that
# this entry holds the evidence, so it counts more than a term every entry shares.
_SELECTION_EXCLUSIVE_BONUS = 2.0
_SELECTION_OTHER_ENTRY_FACTOR = 1.0
# How one candidate window is compared with the others.
#
# "absolute" ranks by the lexical weight a window adds, which favours a window that
# happens to mention one more (possibly generic) query word.
# "efficiency" divides that weight by the number of characters the window costs, so
# a tight window that carries the claim's content words wins over a sprawling one.
# "density" ranks by the weight per token of the window.
_SELECTION_RANK_MODE = "absolute"
# A claim can be supported by several statements of one abstract, and every statement
# is evidence.  When two windows carry the same query words, the one that reaches into
# a part of the abstract that is still untouched answers a different statement, so it
# is preferred over a second window over the part that is already selected.
_SELECTION_SPREAD_REGIONS = True
# Whether an untouched part of the abstract outranks a window that repeats what the
# output already carries.  With it off, the windows are ranked by the lexical weight
# they add alone.
_SELECTION_SPREAD_FIRST = True
_ELLIPSIS = "…"
_BEGIN_MARKER = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1"
_END_MARKER = "END_POWERCONTEXT_PREPARED_CONTEXT_V1"
_Item = TypeVar("_Item")
_SELECTION_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "could",
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
    }
)


def _selection_terms(value: str) -> tuple[tuple[str, int, int], ...]:
    terms: list[tuple[str, int, int]] = []
    start: int | None = None
    token: list[str] = []

    def flush(end: int) -> None:
        nonlocal start
        if start is not None:
            terms.append(("".join(token).casefold(), start, end))
            token.clear()
            start = None

    for index, character in enumerate(value):
        if _is_cjk(character):
            flush(index)
            terms.append((character, index, index + 1))
        elif character.isalnum() and not _is_cjk(character):
            if start is None:
                start = index
            token.append(character)
        else:
            flush(index)
    flush(len(value))
    return tuple(terms)


def _is_cjk(char: str) -> bool:
    point = ord(char)
    return 0x3400 <= point <= 0x4DBF or 0x4E00 <= point <= 0x9FFF or 0xF900 <= point <= 0xFAFF


# A full stop that ends an abbreviation does not end a sentence: "vs.", "e.g.",
# "S. aureus" and "U.S." all continue.  Splitting there would cut a sentence in
# half and make the rest of it unreachable for a window that has to contain whole
# sentences.
_SELECTION_ABBREVIATIONS = frozenset(
    {
        "al",
        "approx",
        "ca",
        "cf",
        "dr",
        "e.g",
        "eg",
        "eq",
        "eqs",
        "est",
        "etc",
        "fig",
        "figs",
        "i.e",
        "ie",
        "inc",
        "jr",
        "ltd",
        "mr",
        "mrs",
        "ms",
        "no",
        "nos",
        "prof",
        "ref",
        "refs",
        "rep",
        "sr",
        "st",
        "tab",
        "vol",
        "vs",
    }
)


def _selection_sentence_boundaries(value: str) -> list[tuple[int, int]]:
    """The spans of the sentences of ``value``, keeping abbreviations together."""

    spans: list[tuple[int, int]] = []
    start = 0
    for boundary in re.finditer(r"(?<=[.!?])\s+|\n+", value):
        cut = boundary.start()
        if start < cut and not _selection_ends_abbreviation(value, start, cut):
            spans.append((start, cut))
            start = boundary.end()
        elif start >= cut:
            start = boundary.end()
    if start < len(value):
        spans.append((start, len(value)))
    return spans


def _selection_ends_ellipsis(value: str, start: int, cut: int) -> bool:
    """Whether the sentence candidate ends in a run of full stops.

    Scientific text marks a gap with " . . . " far more often than it ends a
    sentence, so a run of full stops continues the sentence instead of ending it.
    """

    # Walk back over the full stops and the spaces between them.
    index = cut - 1
    full_stops = 0
    while index >= start:
        character = value[index]
        if character == ".":
            full_stops += 1
            index -= 1
            continue
        if character in " \t":
            index -= 1
            continue
        break
    if full_stops < 2:
        return False
    # A run of full stops that reaches the start of the sentence is an ellipsis
    # itself, not the end of one.
    return index >= start


def _selection_ends_abbreviation(value: str, start: int, cut: int) -> bool:
    """Whether the sentence candidate that ends at ``cut`` is really an abbreviation."""

    if value[cut - 1] != ".":
        return False
    # An ellipsis continues the sentence: "in vitro . . . and in vivo . . . were".
    if _selection_ends_ellipsis(value, start, cut):
        return True
    # The word in front of the full stop decides.
    word_start = cut - 1
    while word_start > start and (value[word_start - 1].isalnum() or value[word_start - 1] == "."):
        word_start -= 1
    word = value[word_start : cut - 1]
    if not word:
        return False
    if word.casefold() in _SELECTION_ABBREVIATIONS:
        return True
    # A single capital letter is an initial, as in "S. aureus" or "H. pylori".
    if len(word) == 1 and word.isalpha() and word.isupper():
        return True
    # A dotted acronym such as "U.S." keeps its last letter in the word.
    return (
        len(word) > 1
        and all(part.isalpha() for part in word.split("."))
        and word.replace(".", "").isupper()
    )


def _fragment_end_boundaries(value: str) -> tuple[int, ...]:
    return tuple(
        offset
        for offset in range(1, len(value) + 1)
        if offset == len(value)
        or value[offset].isspace()
        or _is_cjk(value[offset - 1])
        or value[offset - 1] in ".,;:!?)]}\"'"
    )


@dataclass(frozen=True)
class _SelectionCandidate:
    """One sentence-aligned candidate window inside a Memory entry."""

    address: MemoryEntryAddress
    start: int
    end: int
    text: str
    covered: frozenset[str]
    coverage: float
    proximity: float
    density: float
    required_end: int
    sentence_index: int
    hit_weight: float


def _selection_term_weights(
    query_terms: set[str], memory_candidates: Sequence[PreparedMemoryCandidates]
) -> tuple[dict[str, float], float]:
    hits = tuple((group, hit) for group in memory_candidates for hit in group.hits)
    document_frequency = Counter(
        term
        for _group, hit in hits
        for term in query_terms.intersection({token for token, _start, _end in _selection_terms(hit.text)})
    )
    weights = {term: math.log1p((len(hits) + 1) / (document_frequency[term] + 1)) for term in query_terms}
    return weights, sum(weights.values()) or 1.0


def _selection_proximity(
    ordered_query_terms: Sequence[str],
    matches: Sequence[tuple[str, int, int]],
) -> float:
    """Score how close the covered query terms sit to each other inside one window."""

    pair_scores: list[float] = []
    for left, right in pairwise(ordered_query_terms):
        left_ends = [match_end for term, _match_start, match_end in matches if term == left]
        right_starts = [match_start for term, match_start, _match_end in matches if term == right]
        distances = [right_start - left_end for left_end in left_ends for right_start in right_starts if right_start >= left_end]
        if distances:
            pair_scores.append(1.0 / (1.0 + min(distances) / 24.0))
    return sum(pair_scores) / max(1, len(ordered_query_terms) - 1)


def _selection_candidate(
    address: MemoryEntryAddress,
    text: str,
    start: int,
    end: int,
    matches: Sequence[tuple[str, int, int]],
    *,
    weights: dict[str, float],
    total_weight: float,
    ordered_query_terms: Sequence[str],
    sentence_index: int,
    hit_weight: float,
) -> _SelectionCandidate:
    inside = tuple(match for match in matches if start <= match[1] and match[2] <= end)
    covered = frozenset(match[0] for match in inside)
    covered_weight = sum(weights[term] for term in covered)
    coverage = covered_weight / total_weight
    density = covered_weight / math.sqrt(max(1, len(_selection_terms(text[start:end]))))
    required_end = max(
        (match_end for term, _match_start, match_end in inside),
        default=end,
    )
    return _SelectionCandidate(
        address=address,
        start=start,
        end=end,
        text=text[start:end],
        covered=covered,
        coverage=coverage,
        proximity=_selection_proximity(ordered_query_terms, inside),
        density=density,
        required_end=required_end,
        sentence_index=sentence_index,
        hit_weight=hit_weight,
    )


def _selection_hit_weight(rank: int, score: float, group: PreparedMemoryCandidates) -> float:
    """How much the retrieval stage's own ranking discounts one candidate entry.

    The retrieval stage already decided which Memory entries answer the query, so
    that ranking is a prior the selection stage should not throw away.
    """

    if _SELECTION_HIT_WEIGHT == "flat":
        return 1.0
    if _SELECTION_HIT_WEIGHT == "steep":
        return 1.0 / (1.0 + rank) ** 2
    if _SELECTION_HIT_WEIGHT == "score":
        best = max((float(hit.score) for hit in group.hits), default=0.0)
        return float(score) / best if best > 0 else 1.0
    return 1.0 / (1.0 + rank)


def _selection_entry_weights(
    group: PreparedMemoryCandidates,
    query_terms: set[str],
    weights: dict[str, float],
) -> dict[str, float]:
    """How well each candidate entry answers the query, independent of its rank.

    The retrieval stage compares whole entries, so its order says little about
    which entry holds the evidence of one claim: that evidence lives in a few
    sentences.  This score looks at the query terms an entry carries and gives
    extra weight to the terms that only this entry carries, because a term that
    every candidate carries cannot tell them apart.
    """

    entries: dict[str, tuple[PreparedMemoryCandidates, object]] = {}
    for hit in group.hits:
        entries[hit.entry_id] = (group, hit)
    if not entries:
        return {}
    document_frequency = Counter()
    for _group, hit in entries.values():
        present = {term for term, _start, _end in _selection_terms(hit.text)}
        for term in query_terms.intersection(present):
            document_frequency[term] += 1
    scores: dict[str, float] = {}
    for entry_id, (_group, hit) in entries.items():
        present = {term for term, _start, _end in _selection_terms(hit.text)}
        covered = query_terms.intersection(present)
        weight = sum(weights.get(term, 1.0) for term in covered)
        exclusive = sum(1 for term in covered if document_frequency[term] == 1)
        scores[entry_id] = weight + _SELECTION_EXCLUSIVE_BONUS * exclusive
    best = max(scores.values(), default=0.0)
    if best <= 0:
        return dict.fromkeys(scores, 1.0)
    return {entry_id: score / best for entry_id, score in scores.items()}


def _selection_candidate_weight(
    rank: int,
    score: float,
    group: PreparedMemoryCandidates,
    entry_weight: float,
) -> float:
    """Combine the content signal of an entry with the retrieval order."""

    order = _selection_hit_weight(rank, score, group)
    return entry_weight * (1.0 - _SELECTION_ORDER_TIEBREAK) + order * _SELECTION_ORDER_TIEBREAK


def _sentence_term_matches(
    sentences: tuple[tuple[int, int], ...],
    matches: Sequence[tuple[str, int, int]],
) -> dict[int, frozenset[str]]:
    """The query terms each sentence of an entry carries."""

    return {
        index: frozenset(
            term for term, match_start, _match_end in matches if start <= match_start < end
        )
        for index, (start, end) in enumerate(sentences)
    }


def _selection_candidates(
    memory_candidates: Sequence[PreparedMemoryCandidates],
    query_terms: set[str],
    ordered_query_terms: Sequence[str],
    weights: dict[str, float],
    total_weight: float,
) -> tuple[
    tuple[_SelectionCandidate, ...],
    dict[tuple[str, str, str], tuple[tuple[int, int], ...]],
    dict[tuple[str, str, str], dict[int, frozenset[str]]],
]:
    """Build sentence-aligned candidate windows for every Memory candidate.

    A candidate never starts or ends inside a sentence, so a selected window either
    contains a whole sentence or none of it.  That keeps the selected text verifiable
    against the original entry and avoids paying budget for half a sentence.
    """

    candidates: list[_SelectionCandidate] = []
    sentences_by_entry: dict[tuple[str, str, str], tuple[tuple[int, int], ...]] = {}
    matches_by_entry: dict[tuple[str, str, str], dict[int, frozenset[str]]] = {}
    for group in memory_candidates:
        entry_weights = _selection_entry_weights(group, query_terms, weights)
        for rank, hit in enumerate(group.hits):
            matches = tuple(item for item in _selection_terms(hit.text) if item[0] in query_terms)
            if not matches:
                continue
            address = MemoryEntryAddress(
                memory=ArtifactAddress(scope_id=group.scope_id, artifact=hit.memory_ref),
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
            # The content of the entry decides which one answers the query; the
            # retrieval order only breaks a tie between equally relevant entries.
            hit_weight = _selection_candidate_weight(
                rank, hit.score, group, entry_weights.get(hit.entry_id, 1.0)
            )
            sentences = tuple(_selection_sentence_boundaries(hit.text))
            sentences_by_entry[_selection_address_key(address)] = sentences
            matches_by_entry[_selection_address_key(address)] = _sentence_term_matches(
                sentences, matches
            )
            singles: list[_SelectionCandidate] = []
            for index, (start, end) in enumerate(sentences):
                if not any(start <= match_start < end for _term, match_start, _match_end in matches):
                    continue
                singles.append(
                    _selection_candidate(
                        address,
                        hit.text,
                        start,
                        end,
                        matches,
                        weights=weights,
                        total_weight=total_weight,
                        ordered_query_terms=ordered_query_terms,
                        sentence_index=index,
                        hit_weight=hit_weight,
                    )
                )
            candidates.extend(singles)
            # Contiguous sentence runs let one envelope item carry two neighbouring
            # sentences, which is cheaper than paying a second citation for them.
            for length in _SELECTION_RUN_LENGTHS:
                for index in range(len(sentences) - length + 1):
                    start = sentences[index][0]
                    end = sentences[index + length - 1][1]
                    inside = tuple(match for match in matches if start <= match[1] and match[2] <= end)
                    if not inside:
                        continue
                    covered = frozenset(match[0] for match in inside)
                    single_covered = frozenset(
                        term for candidate in singles if candidate.start >= start and candidate.end <= end for term in candidate.covered
                    )
                    if covered <= single_covered and _SELECTION_RUN_REQUIRES_NEW_TERMS:
                        continue
                    candidates.append(
                        _selection_candidate(
                            address,
                            hit.text,
                            start,
                            end,
                            matches,
                            weights=weights,
                            total_weight=total_weight,
                            ordered_query_terms=ordered_query_terms,
                            sentence_index=index,
                            hit_weight=hit_weight,
                        )
                    )
    return tuple(candidates), sentences_by_entry, matches_by_entry


def _fit_selection_fragment(
    selected: Sequence[PreparedMemoryFragment],
    fragment: PreparedMemoryFragment,
    required_end: int,
    max_bytes: int,
) -> tuple[PreparedMemoryFragment, str] | None:
    rendered = _render_fragments((*selected, fragment))
    if len(rendered.encode("utf-8")) <= max_bytes:
        return fragment, rendered
    valid_ends = tuple(
        offset for offset in _fragment_end_boundaries(fragment.text) if fragment.start + offset >= required_end
    )
    low, high = 0, len(valid_ends) - 1
    best: PreparedMemoryFragment | None = None
    while low <= high:
        middle = (low + high) // 2
        end = fragment.start + valid_ends[middle]
        shortened = replace(fragment, end=end, text=fragment.text[: valid_ends[middle]])
        if len(_render_fragments((*selected, shortened)).encode("utf-8")) <= max_bytes:
            best = shortened
            low = middle + 1
        else:
            high = middle - 1
    if best is None:
        return None
    return best, _render_fragments((*selected, best))


def _selection_fragment_key(fragment: PreparedMemoryFragment) -> tuple[str, str, str, int, int]:
    return (
        fragment.address.memory.scope_id,
        fragment.address.entry_id,
        fragment.address.entry_version_id,
        fragment.start,
        fragment.end,
    )


def _selection_entry_key(fragment: PreparedMemoryFragment) -> tuple[str, str, str]:
    return (
        fragment.address.memory.scope_id,
        fragment.address.entry_id,
        fragment.address.entry_version_id,
    )


def _selection_address_key(address: MemoryEntryAddress) -> tuple[str, str, str]:
    return (address.memory.scope_id, address.entry_id, address.entry_version_id)


def _selection_overlaps(selected: Sequence[PreparedMemoryFragment], start: int, end: int, key: tuple[str, str, str]) -> bool:
    return any(
        _selection_entry_key(fragment) == key and fragment.start < end and start < fragment.end
        for fragment in selected
    )


def _selection_item_overhead() -> int:
    """Bytes one extra envelope item costs on top of its own text."""

    sample = PreparedMemoryFragment(
        address=MemoryEntryAddress(
            memory=ArtifactAddress(scope_id="s" * 24, artifact=ArtifactRef(family="memory", artifact_id="m", revision=1)),
            entry_id="e" * 40,
            entry_version_id="v" * 40,
        ),
        start=0,
        end=0,
        text="",
    )
    base = len(_render_fragments(()).encode("utf-8"))
    single = len(_render_fragments((sample,)).encode("utf-8"))
    return max(0, single - base)


def _extend_selection_fragment(
    selected: Sequence[PreparedMemoryFragment],
    fragment: PreparedMemoryFragment,
    sentences: tuple[tuple[int, int], ...],
    source_text: str,
    max_bytes: int,
    *,
    first_only: bool = True,
    enabled: bool = True,
    matches_by_sentence: dict[int, frozenset[str]] | None = None,
    weights: dict[str, float] | None = None,
    covered: set[str] | None = None,
) -> PreparedMemoryFragment:
    """Widen one selected fragment over its neighbouring sentences while it fits.

    Annotated evidence often spans two adjacent sentences.  Folding a neighbour into
    the same fragment keeps the whole range covered without paying for a second
    citation, which is the most expensive part of the envelope.  The neighbour that
    adds the most query terms the output does not have yet is preferred, because the
    sentence on the other side of the fragment usually repeats what is already there.
    """

    if not enabled or not sentences or (first_only and selected):
        return fragment
    covered_sentences = [
        index
        for index, (start, end) in enumerate(sentences)
        if start < fragment.end and fragment.start < end
    ]
    if not covered_sentences:
        return fragment
    first, last = covered_sentences[0], covered_sentences[-1]
    current = fragment
    for _step in range(_SELECTION_EXTEND_SENTENCES):
        widened = _widen_to_neighbour(
            selected,
            current,
            sentences,
            source_text,
            max_bytes,
            first,
            last,
            matches_by_sentence or {},
            weights or {},
            covered or set(),
        )
        if widened is None:
            break
        current, first, last = widened
    return current


def _widen_to_neighbour(
    selected: Sequence[PreparedMemoryFragment],
    fragment: PreparedMemoryFragment,
    sentences: tuple[tuple[int, int], ...],
    source_text: str,
    max_bytes: int,
    first: int,
    last: int,
    matches_by_sentence: dict[int, frozenset[str]],
    weights: dict[str, float],
    covered: set[str],
) -> tuple[PreparedMemoryFragment, int, int] | None:
    """Add the neighbouring sentence that carries the most query weight."""

    def neighbour_weight(index: int) -> float:
        terms = matches_by_sentence.get(index, frozenset())
        return sum(weights.get(term, 1.0) for term in terms if term not in covered)

    options: list[tuple[float, int, bool]] = []
    if last + 1 <= len(sentences) - 1:
        options.append((neighbour_weight(last + 1), last + 1, True))
    if first - 1 >= 0:
        options.append((neighbour_weight(first - 1), first - 1, False))
    options.sort(key=lambda item: (-item[0], item[1]))
    for _weight, index, forward in options:
        start, end = sentences[index]
        if forward:
            widened = PreparedMemoryFragment(
                fragment.address, fragment.start, end, source_text[fragment.start:end]
            )
        else:
            widened = PreparedMemoryFragment(
                fragment.address, start, fragment.end, source_text[start:fragment.end]
            )
        if len(_render_fragments((*selected, widened)).encode("utf-8")) > max_bytes:
            continue
        if forward:
            return widened, first, index
        return widened, index, last
    return None


def _selection_candidate_key(
    candidate: _SelectionCandidate,
    covered: set[str],
    weights: dict[str, float],
) -> tuple[float, ...]:
    """Rank one candidate by the lexical weight it adds, discounted by its entry rank."""

    gain = sum(weights[term] for term in candidate.covered.difference(covered))
    if _SELECTION_RANK_MODE == "efficiency":
        primary = gain / max(1, len(candidate.text))
    elif _SELECTION_RANK_MODE == "density":
        primary = candidate.density
    elif _SELECTION_RANK_MODE == "absolute_density":
        primary = gain * candidate.density
    else:
        primary = gain
    return (
        primary * candidate.hit_weight,
        candidate.coverage,
        candidate.proximity,
        candidate.density,
        -len(candidate.text),
    )


def _select_best_candidate(
    remaining: Sequence[_SelectionCandidate],
    covered: set[str],
    weights: dict[str, float],
    anchor: tuple[str, str, str] | None,
    selected: Sequence[PreparedMemoryFragment] = (),
) -> _SelectionCandidate | None:
    """The candidate that adds the most lexical weight, discounted by its entry rank."""

    best: _SelectionCandidate | None = None
    best_key: tuple[float, ...] | None = None
    for candidate in remaining:
        key = _selection_candidate_key(candidate, covered, weights)
        if key[0] <= 0 and candidate.coverage <= 0:
            continue
        entry_key = _selection_address_key(candidate.address)
        other_entry = anchor is not None and entry_key != anchor
        if other_entry:
            # The evidence of one claim lives in one document; a second window from
            # another document only spends budget that the first document needs.
            key = (key[0] * _SELECTION_OTHER_ENTRY_FACTOR, *key[1:])
        spread = 0.0
        if (
            _SELECTION_SPREAD_REGIONS
            and selected
            and not other_entry
            and not _selection_overlaps(selected, candidate.start, candidate.end, entry_key)
        ):
            spread = 1.0
        # A claim can be supported by several statements of one abstract, and every
        # statement is evidence.  A window over a part of the abstract that is still
        # untouched answers a different statement, so it comes before a window that
        # repeats what the output already carries.
        key = (
            (spread, *key)
            if _SELECTION_SPREAD_FIRST and spread
            else (key[0], spread, *key[1:])
        )
        if best_key is None or key > best_key:
            best, best_key = candidate, key
    return best

def _pack_selection_candidates(
    candidates: Sequence[_SelectionCandidate],
    max_bytes: int,
    weights: dict[str, float],
    sentences_by_entry: dict[tuple[str, str, str], tuple[tuple[int, int], ...]] | None = None,
    text_by_entry: dict[tuple[str, str, str], str] | None = None,
    matches_by_entry: dict[tuple[str, str, str], dict[int, frozenset[str]]] | None = None,
) -> tuple[PreparedMemoryFragment, ...]:
    """Greedily pack sentence-aligned candidates into the byte budget.

    Candidates are ranked by the lexical weight they add, discounted by the retrieval
    stage's own ranking of the entry they live in.  Shared phrases count for more than
    isolated words.  A candidate is taken whole whenever it fits; only a window that
    cannot fit at all is shortened, and then only at a legal text boundary that keeps
    every term it was selected for.
    """

    if not candidates:
        return ()
    selected: list[PreparedMemoryFragment] = []
    covered: set[str] = set()
    remaining = list(candidates)
    while remaining:
        anchor = _selection_entry_key(selected[0]) if selected else None
        best = _select_best_candidate(remaining, covered, weights, anchor, selected)
        if best is None:
            break
        remaining.remove(best)
        entry_key = _selection_address_key(best.address)
        if _selection_overlaps(selected, best.start, best.end, entry_key):
            covered |= best.covered
            continue
        fragment = PreparedMemoryFragment(best.address, best.start, best.end, best.text)
        fitted = _fit_selection_fragment(selected, fragment, best.required_end, max_bytes)
        if fitted is None:
            continue
        if len(fitted[0].text.encode("utf-8")) < _MIN_TRUNCATED_CONTENT_BYTES:
            # A fragment that only keeps a couple of words spends a citation on
            # almost nothing; the budget is better left for a whole window.
            continue
        selected.append(
            _extend_selection_fragment(
                selected,
                fitted[0],
                (sentences_by_entry or {}).get(_selection_entry_key(fitted[0]), ()),
                (text_by_entry or {}).get(_selection_entry_key(fitted[0]), ""),
                max_bytes,
                first_only=_SELECTION_EXTEND_FIRST_ONLY,
                enabled=_SELECTION_EXTEND_SENTENCES > 0,
                matches_by_sentence=(matches_by_entry or {}).get(_selection_entry_key(fitted[0]), {}),
                weights=weights,
                covered=covered | best.covered,
            )
        )
        covered |= best.covered
    return tuple(selected)


@dataclass(frozen=True)
class _PreparedContextEntry:
    origin: PreparedContextOrigin
    kind: str
    citation: dict[str, object]
    content: str
    truncated: bool


@dataclass(frozen=True)
class _EntryFit:
    """One fit attempt on the non-assembly path: the entry that fitted, or why none did.

    The two drop reasons are disjoint and mirror the assembly path's
    :class:`~powercontext.builtin.runtime.prepared_text.FitOutcome`. An entry is dropped
    *below the minimum truncated content* when a shorter rendering exists and fits but is too
    short to be an honest delivery; it is dropped *with no fitting truncation* when no
    shortened rendering fits at all.
    """

    entry: _PreparedContextEntry | None = None
    dropped_below_min_bytes: bool = False
    dropped_no_fitting_truncation: bool = False


@dataclass(frozen=True)
class PreparedContextOmissions:
    """Aggregate counts of the items the byte budget omitted in one build.

    Every count is a per-call aggregate, never per-entry attribution and never a verdict about
    an entry: an entry losing to the budget is not a negative result about that entry.

    ``dropped_items`` is the sum of its two sub-counts, so "the budget could not fit this
    item" can be told apart from "this item was too short to truncate into the remaining
    space". A single merged counter would misreport the second cause as the first.
    """

    truncated_items: int = 0
    dropped_items: int = 0
    dropped_below_min_bytes: int = 0
    dropped_no_fitting_truncation: int = 0


@dataclass(frozen=True)
class PreparedContextBuild:
    """Final public context and the exact origins selected to produce it.

    ``omissions`` is always filled by the Builder. The RFC 1560 recall trace is deliberately
    **not** a field here: ``ScopedContextApplication._prepare`` returns ``build.context`` and
    discards the rest, so such a field would have no production observer. The trace is
    delivered through the Runtime's optional ``RecallEffortSink`` instead.
    """

    context: PreparedContext
    origins: tuple[PreparedContextOrigin, ...]
    omissions: PreparedContextOmissions = PreparedContextOmissions()
    code_origins: tuple[CodeEvidenceRef, ...] = ()
    fragments: tuple[PreparedMemoryFragment, ...] = ()


@dataclass(frozen=True)
class MemoryEntryAddress:
    """Identify one exact Memory entry version across Scope boundaries."""

    memory: ArtifactAddress
    entry_id: str
    entry_version_id: str


@dataclass(frozen=True)
class PreparedMemoryFragment:
    address: MemoryEntryAddress
    start: int
    end: int
    text: str


PreparedContextOrigin = MemoryCitation | ArtifactRef | MemoryEntryAddress | ArtifactAddress


@dataclass(frozen=True)
class PreparedMemoryCandidates:
    """Memory candidates read from one Scope."""

    scope_id: str
    memory_ref: ArtifactRef | None = None
    hits: tuple[MemoryHit, ...] = ()


@dataclass(frozen=True)
class PreparedExperienceCandidates:
    """Experience candidates read from one Scope."""

    scope_id: str
    hits: tuple[ExperienceSearchHit, ...] = ()


@dataclass(frozen=True)
class PreparedProfileCandidate:
    """The latest committed Profile snapshot read from one Scope."""

    scope_id: str
    profile: Profile


class PreparedContextBuilder:
    """Select and render final context without I/O, persistence, or reranking."""

    memory_candidate_limit = 16
    topic_memory_candidate_limit = 8
    experience_candidate_limit = 8
    candidate_limit = memory_candidate_limit
    entry_limit = 8
    topic_memory_entry_limit = 8
    experience_entry_limit = 2
    max_entry_content_bytes = 2000
    _text_entry_limit: int | None = None

    def empty(self) -> PreparedContext:
        return PreparedContext(status="empty", content=None, content_bytes=0)

    def build(
        self,
        *,
        request: PrepareContextRequest,
        scope_id: str | None = None,
        memory_ref: ArtifactRef | None = None,
        hits: Sequence[MemoryHit] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_hits: Sequence[ExperienceSearchHit] = (),
    ) -> PreparedContext:
        return self.build_result(
            request=request,
            scope_id=scope_id,
            memory_ref=memory_ref,
            hits=hits,
            topic_memory_hits=topic_memory_hits,
            experience_hits=experience_hits,
        ).context

    def build_result(
        self,
        *,
        request: PrepareContextRequest,
        scope_id: str | None = None,
        memory_ref: ArtifactRef | None = None,
        hits: Sequence[MemoryHit] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_hits: Sequence[ExperienceSearchHit] = (),
    ) -> PreparedContextBuild:
        return self.build_scopes_result(
            request=request,
            current_scope_id=scope_id,
            memory_candidates=(
                PreparedMemoryCandidates(scope_id=scope_id or "", memory_ref=memory_ref, hits=tuple(hits)),
            ),
            experience_candidates=(PreparedExperienceCandidates(scope_id=scope_id or "", hits=tuple(experience_hits)),),
            topic_memory_hits=topic_memory_hits,
        )

    def build_scopes_result(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_candidates: Sequence[PreparedExperienceCandidates] = (),
        profile_candidates: Sequence[PreparedProfileCandidate] = (),
        code_candidates: Sequence[PreparedCodeCandidate] = (),
    ) -> PreparedContextBuild:
        if request.evidence_selection:
            return self._build_evidence_selection(request, memory_candidates)
        if request.include_code:

            def historical(history_request: PrepareContextRequest, entries: int) -> PreparedContextBuild:
                builder = PreparedContextBuilder()
                builder.entry_limit = entries
                builder._text_entry_limit = entries if entries < self.entry_limit else None
                return builder.build_scopes_result(
                    request=history_request.model_copy(update={"include_code": False}),
                    current_scope_id=current_scope_id,
                    memory_candidates=memory_candidates,
                    topic_memory_hits=topic_memory_hits,
                    experience_candidates=experience_candidates,
                    profile_candidates=profile_candidates,
                )

            return assemble_code(request, code_candidates, self.entry_limit, historical)

        if sum(len(candidates.hits) for candidates in memory_candidates) > self.memory_candidate_limit:
            raise PreparedContextInvariantError("memory-candidate-limit")
        if len(topic_memory_hits) > self.topic_memory_candidate_limit:
            raise PreparedContextInvariantError("topic-memory-candidate-limit")
        if sum(len(candidates.hits) for candidates in experience_candidates) > self.experience_candidate_limit:
            raise PreparedContextInvariantError("experience-candidate-limit")

        content, origins, omissions = self._select_entries(
            request=request,
            current_scope_id=current_scope_id,
            memory_candidates=memory_candidates,
            experience_candidates=experience_candidates,
            profile_candidates=profile_candidates,
            topic_memory_hits=topic_memory_hits,
        )
        if content is None:
            return PreparedContextBuild(context=self.empty(), origins=(), omissions=omissions)
        content_bytes = len(content.encode("utf-8"))
        # The assembly path is bounded by construction (every fitted item is measured against
        # `max_bytes`), so the ceiling check belongs to the non-assembly renderer only.
        if request.assembly is None and content_bytes > request.max_bytes:
            raise PreparedContextInvariantError("output-budget")
        return PreparedContextBuild(
            context=PreparedContext(status="ready", content=content, content_bytes=content_bytes),
            origins=origins,
            omissions=omissions,
        )

    def _build_evidence_selection(
        self, request: PrepareContextRequest, memory_candidates: Sequence[PreparedMemoryCandidates]
    ) -> PreparedContextBuild:
        ordered_query_terms = tuple(
            dict.fromkeys(
                term
                for term, _start, _end in _selection_terms(request.query)
                if term not in _SELECTION_STOPWORDS
            )
        )
        query_terms = set(ordered_query_terms)
        if not query_terms:
            return PreparedContextBuild(context=self.empty(), origins=())
        weights, total_weight = _selection_term_weights(query_terms, memory_candidates)
        candidates, sentences_by_entry, matches_by_entry = _selection_candidates(
            memory_candidates, query_terms, ordered_query_terms, weights, total_weight
        )
        text_by_entry = {
            _selection_address_key(
                MemoryEntryAddress(
                    memory=ArtifactAddress(scope_id=group.scope_id, artifact=hit.memory_ref),
                    entry_id=hit.entry_id,
                    entry_version_id=hit.entry_version_id,
                )
            ): hit.text
            for group in memory_candidates
            for hit in group.hits
        }
        selected = _pack_selection_candidates(
            candidates,
            request.max_bytes,
            weights,
            sentences_by_entry,
            text_by_entry,
            matches_by_entry,
        )
        if not selected:
            return PreparedContextBuild(context=self.empty(), origins=())
        content = _render_fragments(selected)
        return PreparedContextBuild(
            context=PreparedContext(status="ready", content=content, content_bytes=len(content.encode("utf-8"))),
            origins=tuple(fragment.address for fragment in selected),
            fragments=tuple(selected),
        )

    def probe_budget(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_candidates: Sequence[PreparedExperienceCandidates] = (),
        profile_candidates: Sequence[PreparedProfileCandidate] = (),
    ) -> RecallBudgetView:
        """Report what the byte budget does to one candidate set, without delivering it.

        This is the RFC 1560 *budget probe*: one pass of this Builder's own pure selection and
        rendering code, keeping only the counters and discarding the rendered output. It lets
        the gate tell budget-limited thinness from recall-limited thinness.

        Pure and side-effect free: no I/O, no persistence, nothing reported to any sink, and
        the same inputs as :meth:`build_scopes_result` are guaranteed to yield the same
        counters because both go through :meth:`_select_entries`.
        """

        content, origins, omissions = self._select_entries(
            request=request,
            current_scope_id=current_scope_id,
            memory_candidates=memory_candidates,
            experience_candidates=experience_candidates,
            profile_candidates=profile_candidates,
            topic_memory_hits=topic_memory_hits,
        )
        content_bytes = 0 if content is None else len(content.encode("utf-8"))
        return RecallBudgetView(
            max_bytes=request.max_bytes,
            delivered_items=len(origins),
            truncated_items=omissions.truncated_items,
            dropped_items=omissions.dropped_items,
            unused_bytes=max(0, request.max_bytes - content_bytes),
        )

    def _select_entries(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates],
        topic_memory_hits: Sequence[TopicMemorySearchHit],
        experience_candidates: Sequence[PreparedExperienceCandidates],
        profile_candidates: Sequence[PreparedProfileCandidate],
    ) -> tuple[str | None, tuple[PreparedContextOrigin, ...], PreparedContextOmissions]:
        """Run one complete pure selection pass and render it.

        Returns ``(content, origins, omissions)``; ``content`` is ``None`` exactly when nothing
        was selected, which is the empty-result case for both paths. This is the single
        selection path shared by :meth:`build_scopes_result` and :meth:`probe_budget`, so the
        probe's counters always describe the selection the build would actually perform.
        """

        if request.assembly is not None:
            return self._select_text(
                request,
                current_scope_id=current_scope_id,
                memory_candidates=memory_candidates,
                experience_candidates=experience_candidates,
                profile_candidates=profile_candidates,
                topic_memory_hits=topic_memory_hits,
            )

        memory_entries = _interleave_groups(
            tuple(
                self._memory_entries(
                    candidates.memory_ref,
                    candidates.hits,
                    scope_id=None if candidates.scope_id == current_scope_id else candidates.scope_id or None,
                )
                for candidates in memory_candidates
            )
        )
        topic_memory_entries = self._topic_memory_entries(topic_memory_hits)
        experience_entries = _interleave_groups(
            tuple(
                self._experience_entries(
                    candidates.hits,
                    scope_id=None if candidates.scope_id == current_scope_id else candidates.scope_id or None,
                )
                for candidates in experience_candidates
            )
        )[: self.experience_entry_limit]
        entries, omissions = self._fit_entries(request, memory_entries, topic_memory_entries, experience_entries)
        if not entries:
            return None, (), omissions
        return _render(entries), tuple(entry.origin for entry in entries), omissions

    def _select_text(
        self,
        request: PrepareContextRequest,
        *,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates],
        experience_candidates: Sequence[PreparedExperienceCandidates],
        profile_candidates: Sequence[PreparedProfileCandidate],
        topic_memory_hits: Sequence[TopicMemorySearchHit],
    ) -> tuple[str | None, tuple[PreparedContextOrigin, ...], PreparedContextOmissions]:
        assembly = request.assembly
        if assembly is None:
            raise PreparedContextInvariantError("text-assembly-missing")
        included: list[ContextTextItem] = []
        origins: list[PreparedContextOrigin] = []
        truncated_items = 0
        dropped_below_min_bytes = 0
        dropped_no_fitting_truncation = 0
        for section in assembly.sections:
            # Unfilled earlier sections leave their capacity available to later ones.
            limit = (
                section.limit
                if self._text_entry_limit is None
                else min(section.limit, self._text_entry_limit - len(included))
            )
            if section.family == "profile":
                entries = self._profile_entries(profile_candidates)
            elif section.family == "topic-memory":
                entries = self._topic_memory_entries(topic_memory_hits, scope_id=current_scope_id)
            else:
                groups = (
                    tuple(
                        tuple(
                            self._memory_entries(group.memory_ref, (hit,), scope_id=group.scope_id)
                            for hit in group.hits
                        )
                        for group in memory_candidates
                    )
                    if section.family == "memory"
                    else tuple(
                        tuple(self._experience_entries((hit,), scope_id=group.scope_id) for hit in group.hits)
                        for group in experience_candidates
                    )
                )
                entries = tuple(chain.from_iterable(_interleave_groups(groups)))
            seen: set[tuple[str, str, str, int, str | None, str | None]] = set()
            rank = 0
            selected_count = 0
            for entry in entries:
                if selected_count >= limit:
                    break
                item = _text_item(entry)
                artifact = item.artifact
                identity = (
                    artifact.scope_id,
                    artifact.artifact.family,
                    artifact.artifact.artifact_id,
                    artifact.artifact.revision,
                    item.entry_id,
                    item.entry_version_id,
                )
                if identity in seen or not item.content.strip():
                    continue
                seen.add(identity)
                rank += 1
                outcome = fit_context_text_item(included, replace(item, recall_rank=rank), assembly, request.max_bytes)
                if outcome.item is None:
                    dropped_below_min_bytes += int(outcome.dropped_below_min_bytes)
                    dropped_no_fitting_truncation += int(outcome.dropped_no_fitting_truncation)
                    continue
                fitted = outcome.item
                included.append(fitted)
                origins.append(entry.origin)
                selected_count += 1
                truncated_items += int(fitted.truncated)
        omissions = PreparedContextOmissions(
            truncated_items=truncated_items,
            dropped_items=dropped_below_min_bytes + dropped_no_fitting_truncation,
            dropped_below_min_bytes=dropped_below_min_bytes,
            dropped_no_fitting_truncation=dropped_no_fitting_truncation,
        )
        if not included:
            return None, (), omissions
        content = render_context_text(included, assembly)
        return content, tuple(origins), omissions

    def _profile_entries(self, candidates: Sequence[PreparedProfileCandidate]) -> tuple[_PreparedContextEntry, ...]:
        entries = []
        for candidate in candidates:
            origin = ArtifactAddress(scope_id=candidate.scope_id, artifact=candidate.profile.as_ref())
            entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="profile",
                    citation={"artifact": origin.model_dump(mode="json")},
                    content=candidate.profile.content.content,
                    truncated=False,
                )
            )
        return tuple(entries)

    def _memory_entries(
        self,
        memory_ref: ArtifactRef | None,
        hits: Sequence[MemoryHit],
        *,
        scope_id: str | None = None,
    ) -> tuple[_PreparedContextEntry, ...]:
        if hits and memory_ref is None:
            raise PreparedContextInvariantError("memory-ref-missing")
        memory_entries: list[_PreparedContextEntry] = []
        seen: set[tuple[str, str]] = set()
        for hit in hits:
            if hit.memory_ref != memory_ref:
                raise PreparedContextInvariantError("memory-ref-mismatch")

            citation_key = (hit.entry_id, hit.entry_version_id)
            if citation_key in seen:
                continue
            seen.add(citation_key)
            if not hit.entry_id.strip() or not hit.entry_version_id.strip() or not hit.text.strip():
                continue
            citation = MemoryCitation(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
            origin: PreparedContextOrigin = citation
            rendered_citation = citation.model_dump(mode="json")
            if scope_id is not None:
                memory = ArtifactAddress(scope_id=scope_id, artifact=hit.memory_ref)
                origin = MemoryEntryAddress(
                    memory=memory,
                    entry_id=hit.entry_id,
                    entry_version_id=hit.entry_version_id,
                )
                rendered_citation = {
                    "memory": memory.model_dump(mode="json"),
                    "entry_id": hit.entry_id,
                    "entry_version_id": hit.entry_version_id,
                }
            memory_entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="memory",
                    citation=rendered_citation,
                    content=hit.text,
                    truncated=False,
                )
            )
        return tuple(memory_entries)

    def _topic_memory_entries(
        self,
        hits: Sequence[TopicMemorySearchHit],
        *,
        scope_id: str | None = None,
    ) -> tuple[_PreparedContextEntry, ...]:
        topic_entries: list[_PreparedContextEntry] = []
        seen_topics: set[tuple[str, int]] = set()
        for hit in hits:
            if hit.artifact_ref.family != TopicMemory.family:
                raise PreparedContextInvariantError("topic-memory-family-mismatch")
            identity = (hit.artifact_ref.artifact_id, hit.artifact_ref.revision)
            if identity in seen_topics:
                continue
            seen_topics.add(identity)
            if len(topic_entries) >= self.topic_memory_entry_limit:
                break
            content = {"title": hit.title, "summary": hit.summary}
            if hit.snippet is not None:
                content["snippet"] = hit.snippet
            origin: PreparedContextOrigin = hit.artifact_ref
            citation = {"artifact_ref": hit.artifact_ref.model_dump(mode="json")}
            body = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
            if scope_id is not None:
                origin = ArtifactAddress(scope_id=scope_id, artifact=hit.artifact_ref)
                citation = {"artifact": origin.model_dump(mode="json")}
                body = "\n\n".join(f"{label.capitalize()}: {value}" for label, value in content.items())
            topic_entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="topic-memory",
                    citation=citation,
                    content=body,
                    truncated=False,
                )
            )
        return tuple(topic_entries)

    def _experience_entries(
        self,
        hits: Sequence[ExperienceSearchHit],
        *,
        scope_id: str | None = None,
    ) -> tuple[_PreparedContextEntry, ...]:
        experience_entries: list[_PreparedContextEntry] = []
        seen_experiences: set[tuple[str, int]] = set()
        for hit in hits:
            if hit.artifact_ref.family != Experience.family:
                raise PreparedContextInvariantError("experience-family-mismatch")
            identity = (hit.artifact_ref.artifact_id, hit.artifact_ref.revision)
            if identity in seen_experiences:
                continue
            seen_experiences.add(identity)
            origin: PreparedContextOrigin = hit.artifact_ref
            rendered_citation: dict[str, object] = {"artifact_ref": hit.artifact_ref.model_dump(mode="json")}
            if scope_id is not None:
                origin = ArtifactAddress(scope_id=scope_id, artifact=hit.artifact_ref)
                rendered_citation = {"artifact": origin.model_dump(mode="json")}
            experience_entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="experience",
                    citation=rendered_citation,
                    content=render_experience(hit.content),
                    truncated=False,
                )
            )
        return tuple(experience_entries)

    def _fit_entries(
        self,
        request: PrepareContextRequest,
        memory_entries: Sequence[_PreparedContextEntry],
        topic_memory_entries: Sequence[_PreparedContextEntry],
        experience_entries: Sequence[_PreparedContextEntry],
    ) -> tuple[tuple[_PreparedContextEntry, ...], PreparedContextOmissions]:
        entries: list[_PreparedContextEntry] = []
        truncated_items = 0
        dropped_below_min_bytes = 0
        dropped_no_fitting_truncation = 0
        for candidate in _interleave(memory_entries, topic_memory_entries, experience_entries):
            if len(entries) >= self.entry_limit:
                break
            fit = self._fit_entry(
                entries,
                origin=candidate.origin,
                kind=candidate.kind,
                citation=candidate.citation,
                text=candidate.content,
                max_bytes=request.max_bytes,
            )
            if fit.entry is None:
                dropped_below_min_bytes += int(fit.dropped_below_min_bytes)
                dropped_no_fitting_truncation += int(fit.dropped_no_fitting_truncation)
                continue
            entries.append(fit.entry)
            if fit.entry.truncated:
                truncated_items += 1
        return tuple(entries), PreparedContextOmissions(
            truncated_items=truncated_items,
            dropped_items=dropped_below_min_bytes + dropped_no_fitting_truncation,
            dropped_below_min_bytes=dropped_below_min_bytes,
            dropped_no_fitting_truncation=dropped_no_fitting_truncation,
        )

    def _fit_entry(
        self,
        entries: Sequence[_PreparedContextEntry],
        *,
        origin: PreparedContextOrigin,
        kind: str,
        citation: dict[str, object],
        text: str,
        max_bytes: int,
    ) -> _EntryFit:
        """Fit one entry into the remaining budget, reporting why it was dropped if it was.

        The two ``None`` paths of the original code are kept apart: ``below-min-bytes`` means a
        shorter rendering exists and fits but is too short to deliver honestly, while
        ``no-fitting-truncation`` means no shortened rendering fitted at all.
        """

        source_bytes = len(text.encode("utf-8"))
        entry_budget = min(source_bytes, self.max_entry_content_bytes)
        candidate = _PreparedContextEntry(
            origin=origin,
            kind=kind,
            citation=citation,
            content=text if source_bytes <= entry_budget else _truncate_utf8(text, entry_budget),
            truncated=source_bytes > entry_budget,
        )
        if _rendered_bytes((*entries, candidate)) <= max_bytes:
            return _EntryFit(entry=candidate)
        if source_bytes < _MIN_TRUNCATED_CONTENT_BYTES:
            return _EntryFit(dropped_below_min_bytes=True)

        lower = _MIN_TRUNCATED_CONTENT_BYTES
        upper = min(entry_budget, source_bytes - 1)
        best: _PreparedContextEntry | None = None
        while lower <= upper:
            byte_budget = (lower + upper) // 2
            candidate = _PreparedContextEntry(
                origin=origin,
                kind=kind,
                citation=citation,
                content=_truncate_utf8(text, byte_budget),
                truncated=True,
            )
            if len(candidate.content.encode("utf-8")) < _MIN_TRUNCATED_CONTENT_BYTES:
                lower = byte_budget + 1
                continue
            if _rendered_bytes((*entries, candidate)) <= max_bytes:
                best = candidate
                lower = byte_budget + 1
            else:
                upper = byte_budget - 1
        if best is None:
            return _EntryFit(dropped_no_fitting_truncation=True)
        return _EntryFit(entry=best)


def _interleave(
    memory: Sequence[_PreparedContextEntry],
    topics: Sequence[_PreparedContextEntry],
    experiences: Sequence[_PreparedContextEntry],
) -> tuple[_PreparedContextEntry, ...]:
    """Preserve each family's rank while alternating Memory, Topic, Experience."""

    ordered: list[_PreparedContextEntry] = []
    for index in range(max(len(memory), len(topics), len(experiences))):
        if index < len(memory):
            ordered.append(memory[index])
        if index < len(topics):
            ordered.append(topics[index])
        if index < len(experiences):
            ordered.append(experiences[index])
    return tuple(ordered)


def _interleave_groups(groups: Sequence[Sequence[_Item]]) -> tuple[_Item, ...]:
    ordered: list[_Item] = []
    for index in range(max((len(group) for group in groups), default=0)):
        ordered.extend(group[index] for group in groups if index < len(group))
    return tuple(ordered)


def _render(entries: Sequence[_PreparedContextEntry]) -> str:
    envelope = {
        "trust": "untrusted_history",
        "items": [_render_entry(entry) for entry in entries],
    }
    encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    return "\n\n".join((TRUST_POLICY, f"{_BEGIN_MARKER}\n{encoded}\n{_END_MARKER}"))


def _render_fragments(fragments: Sequence[PreparedMemoryFragment]) -> str:
    envelope = {
        "trust": "untrusted_history",
        "items": [
            {
                "citation": {
                    "scope_id": fragment.address.memory.scope_id,
                    "memory_ref": fragment.address.memory.artifact.model_dump(mode="json"),
                    "entry_id": fragment.address.entry_id,
                    "entry_version_id": fragment.address.entry_version_id,
                },
                "content": fragment.text,
                "start": fragment.start,
                "end": fragment.end,
            }
            for fragment in fragments
        ],
    }
    encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    return "\n\n".join((TRUST_POLICY, f"{_BEGIN_MARKER}\n{encoded}\n{_END_MARKER}"))


def _text_item(entry: _PreparedContextEntry) -> ContextTextItem:
    origin = entry.origin
    if isinstance(origin, MemoryEntryAddress):
        return ContextTextItem(
            artifact=origin.memory,
            content=entry.content,
            recall_rank=0,
            entry_id=origin.entry_id,
            entry_version_id=origin.entry_version_id,
        )
    if isinstance(origin, ArtifactAddress):
        return ContextTextItem(artifact=origin, content=entry.content, recall_rank=0)
    raise PreparedContextInvariantError("text-scope-missing")


def _render_entry(entry: _PreparedContextEntry) -> dict[str, object]:
    rendered: dict[str, object] = {
        "citation": entry.citation,
        "content": entry.content,
        "truncated": entry.truncated,
    }
    if entry.kind != "memory":
        rendered["kind"] = entry.kind
    return rendered


def _rendered_bytes(entries: Sequence[_PreparedContextEntry]) -> int:
    return len(_render(entries).encode("utf-8"))


def _truncate_utf8(text: str, byte_budget: int) -> str:
    prefix_budget = byte_budget - len(_ELLIPSIS.encode("utf-8"))
    encoded_prefix = text.encode("utf-8")[:prefix_budget]
    return f"{encoded_prefix.decode('utf-8', errors='ignore')}{_ELLIPSIS}"
