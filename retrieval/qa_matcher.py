"""Catalog-authoritative QA retrieval matching (no Milvus projection).

Only QA rows already gated by ``KnowledgeContentRepository.list_effective_qa``
(or an equivalent bundle) may enter this matcher. Pending / rejected / expired
rows must never be passed in as candidates.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Sequence

from models.schemas import Chunk, RetrievedChunk

_WHITESPACE = re.compile(r"\s+")
_TOKEN = re.compile(r"[\w一-鿿]+", re.UNICODE)

MatchedOn = Literal["question", "alternative"]


def normalize_question(value: str | None) -> str:
    text = _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", str(value or ""))).strip().casefold()
    return text


def question_hash(value: str | None) -> str:
    return hashlib.sha256(normalize_question(value).encode("utf-8")).hexdigest()


def _tokens(value: str) -> set[str]:
    return {t for t in _TOKEN.findall(normalize_question(value)) if t}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / len(a | b)


@dataclass(frozen=True)
class QARecord:
    """Normalized QA candidate loaded from the catalog bundle."""

    qa_id: str
    question: str
    answer: str
    revision: int = 1
    origin: str = "manual"
    source_document_id: str | None = None
    tenant_id: str = ""
    dataset_id: str = ""
    alternatives: tuple[str, ...] = ()
    negatives: tuple[str, ...] = ()


@dataclass(frozen=True)
class QAMatch:
    qa_id: str
    question: str
    answer: str
    score: float
    matched_on: MatchedOn
    matched_text: str
    revision: int = 1
    origin: str = "manual"
    source_document_id: str | None = None
    tenant_id: str = ""
    dataset_id: str = ""


def match_qa(
    query: str,
    records: Sequence[QARecord],
    *,
    min_score: float = 0.55,
    top_k: int = 3,
) -> list[QAMatch]:
    """Return top QA matches after negative-question suppression."""
    q_norm = normalize_question(query)
    if not q_norm:
        return []
    q_hash = hashlib.sha256(q_norm.encode("utf-8")).hexdigest()
    q_tokens = _tokens(q_norm)
    min_score = float(min_score)
    top_k = max(1, int(top_k))

    matches: list[QAMatch] = []
    for record in records:
        neg_hashes = {question_hash(n) for n in record.negatives if normalize_question(n)}
        if q_hash in neg_hashes:
            continue
        # Also suppress when the query is contained in a longer negative phrasing
        # or vice versa — operators write negatives as "不要用X问法".
        suppressed = False
        for neg in record.negatives:
            n_norm = normalize_question(neg)
            if not n_norm:
                continue
            if n_norm in q_norm or q_norm in n_norm:
                suppressed = True
                break
        if suppressed:
            continue

        best: QAMatch | None = None
        q_main = normalize_question(record.question)
        if q_main:
            if q_norm == q_main:
                best = QAMatch(
                    qa_id=record.qa_id,
                    question=record.question,
                    answer=record.answer,
                    score=1.0,
                    matched_on="question",
                    matched_text=record.question,
                    revision=record.revision,
                    origin=record.origin,
                    source_document_id=record.source_document_id,
                    tenant_id=record.tenant_id,
                    dataset_id=record.dataset_id,
                )
            else:
                score = jaccard(q_tokens, _tokens(q_main))
                if score >= min_score:
                    best = QAMatch(
                        qa_id=record.qa_id,
                        question=record.question,
                        answer=record.answer,
                        score=score,
                        matched_on="question",
                        matched_text=record.question,
                        revision=record.revision,
                        origin=record.origin,
                        source_document_id=record.source_document_id,
                        tenant_id=record.tenant_id,
                        dataset_id=record.dataset_id,
                    )

        for alt in record.alternatives:
            a_norm = normalize_question(alt)
            if not a_norm:
                continue
            if q_norm == a_norm:
                candidate = QAMatch(
                    qa_id=record.qa_id,
                    question=record.question,
                    answer=record.answer,
                    score=0.95,
                    matched_on="alternative",
                    matched_text=alt,
                    revision=record.revision,
                    origin=record.origin,
                    source_document_id=record.source_document_id,
                    tenant_id=record.tenant_id,
                    dataset_id=record.dataset_id,
                )
            else:
                score = jaccard(q_tokens, _tokens(a_norm))
                if score < min_score:
                    continue
                candidate = QAMatch(
                    qa_id=record.qa_id,
                    question=record.question,
                    answer=record.answer,
                    score=score * 0.95,
                    matched_on="alternative",
                    matched_text=alt,
                    revision=record.revision,
                    origin=record.origin,
                    source_document_id=record.source_document_id,
                    tenant_id=record.tenant_id,
                    dataset_id=record.dataset_id,
                )
            if best is None or candidate.score > best.score:
                best = candidate

        if best is not None:
            matches.append(best)

    matches.sort(key=lambda m: (-m.score, m.qa_id))
    return matches[:top_k]


def qa_chunk_id(qa_id: str) -> str:
    return f"qa::{qa_id}"


def parse_qa_chunk_id(chunk_id: str | None) -> str | None:
    raw = str(chunk_id or "")
    if raw.startswith("qa::"):
        return raw[4:] or None
    return None


def match_to_retrieved_chunk(
    match: QAMatch,
    *,
    rank: int = 1,
) -> RetrievedChunk:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    doc_id = match.source_document_id or qa_chunk_id(match.qa_id)
    metadata: dict[str, Any] = {
        "qa_id": match.qa_id,
        "qa_revision": match.revision,
        "qa_origin": match.origin,
        "qa_matched_on": match.matched_on,
        "qa_matched_text": match.matched_text,
        "source_kind": "qa",
    }
    chunk = Chunk(
        chunk_id=qa_chunk_id(match.qa_id),
        doc_id=doc_id,
        text=f"{match.question}\n{match.answer}",
        text_hash=question_hash(match.question + "\n" + match.answer)[:16],
        created_at=now,
        updated_at=now,
        tenant_id=match.tenant_id,
        dataset_id=match.dataset_id,
        metadata=metadata,
    )
    return RetrievedChunk(chunk=chunk, score=match.score, rank=rank, branch="qa")


def merge_qa_into_chunks(
    existing: Sequence[RetrievedChunk],
    qa_items: Sequence[RetrievedChunk],
) -> list[RetrievedChunk]:
    """Prepend unique QA hits, keep document chunks, dedupe by chunk_id."""
    seen: set[str] = set()
    out: list[RetrievedChunk] = []
    for item in list(qa_items) + list(existing):
        cid = item.chunk.chunk_id
        if cid in seen:
            continue
        seen.add(cid)
        out.append(item)
    # re-rank for stable display order
    return [
        item.model_copy(update={"rank": index + 1}) for index, item in enumerate(out)
    ]


__all__ = [
    "QARecord",
    "QAMatch",
    "normalize_question",
    "question_hash",
    "match_qa",
    "qa_chunk_id",
    "parse_qa_chunk_id",
    "match_to_retrieved_chunk",
    "merge_qa_into_chunks",
]
