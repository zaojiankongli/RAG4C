"""Privacy-safe answer facts and evidence refs repository."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from models.orm import TenantKnowledgeAnswerEvidenceRef, TenantKnowledgeAnswerFact

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_LONG_DIGITS = re.compile(r"\d{9,}")
_SECRETISH = re.compile(
    r"(?i)((?:sk-|api[_-]?key|token|password|secret)\s*[=:]\s*\S+|sk-[A-Za-z0-9_-]{6,})"
)


def digest_sha256(*parts: str | None) -> str:
    material = "\n".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def safe_preview(question: str | None, limit: int = 160) -> str:
    text = _EMAIL.sub("[email]", str(question or ""))
    text = _SECRETISH.sub("[redacted]", text)
    text = _LONG_DIGITS.sub("[num]", text)
    text = " ".join(text.split())
    return text[:limit]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def _qa_revision_from_meta(meta: Any) -> int | None:
    if not isinstance(meta, dict):
        return None
    raw = meta.get("qa_revision")
    if raw is None:
        nested = meta.get("metadata")
        if isinstance(nested, dict):
            raw = nested.get("qa_revision")
    try:
        if raw is None:
            return None
        value = int(raw)
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def _evidence_ref_payload(ref: Any) -> dict[str, Any]:
    chunk_id = ref.chunk_id
    document_id = ref.document_id
    qa_id = None
    for candidate in (chunk_id, document_id):
        raw = str(candidate or "")
        if raw.startswith("qa::") and len(raw) > 4:
            qa_id = raw[4:]
            break
    qa_revision = None
    revision_id = str(ref.chunk_revision_id or "")
    if revision_id.startswith("qa-rev:"):
        try:
            qa_revision = int(revision_id[len("qa-rev:") :])
        except ValueError:
            qa_revision = None
    return {
        "id": ref.id,
        "seq": ref.seq,
        "chunk_id": chunk_id,
        "chunk_revision_id": ref.chunk_revision_id,
        "document_id": document_id,
        "citation_status": ref.citation_status,
        "evidence_digest": getattr(ref, "evidence_digest", None),
        "source_kind": "qa" if qa_id else "document",
        "qa_id": qa_id,
        "qa_revision": qa_revision if qa_id else None,
    }


def fact_payload(fact: TenantKnowledgeAnswerFact, refs: list[Any]) -> dict[str, Any]:
    payloads = [_evidence_ref_payload(ref) for ref in refs]
    return {
        "id": fact.id,
        "tenant_id": fact.tenant_id,
        "dataset_id": fact.dataset_id,
        "run_id": fact.run_id,
        "request_id_digest": fact.request_id_digest,
        "query_digest": fact.query_digest,
        "answer_digest": fact.answer_digest,
        "evidence_chain_digest": fact.evidence_chain_digest,
        "fact_digest": fact.fact_digest,
        "safe_query_preview": fact.safe_query_preview,
        "outcome_code": fact.outcome_code,
        "route_code": fact.route_code,
        "citation_count": fact.citation_count,
        "evidence_count": fact.evidence_count,
        "observed_at": fact.observed_at,
        "evidence_refs": payloads,
        "qa_evidence_count": sum(1 for item in payloads if item.get("source_kind") == "qa"),
    }


class AnswerEvidenceRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    def record_answer_fact(
        self,
        *,
        tenant_id: str,
        dataset_id: str = "default",
        run_id: str | None = None,
        request_id: str | None = None,
        question: str | None = None,
        answer: str | None = None,
        route: str | None = None,
        outcome: str = "answered",
        citations: list[dict[str, Any]] | None = None,
        evidence: list[dict[str, Any]] | None = None,
        include_preview: bool = True,
    ) -> str:
        citations = list(citations or [])
        evidence = list(evidence or [])
        # map evidence by chunk_id for document/revision enrichment
        evidence_by_chunk = {
            str(item.get("chunk_id") or ""): item
            for item in evidence
            if str(item.get("chunk_id") or "")
        }
        outcome = (outcome or "answered").strip() or "answered"
        if outcome not in {
            "answered",
            "abstained",
            "cancelled",
            "failed",
            "cached",
        }:
            outcome = "answered" if not str(answer or "").strip() else "failed"
        if not citations and str(answer or "").strip() and outcome == "answered":
            # answered without citations is still a fact
            pass
        if str(answer or "").strip() == "" and outcome == "answered":
            outcome = "abstained"
        route_code = (route or "rag").strip()[:32] or "rag"
        chain_parts = []
        evidence_rows: list[dict[str, Any]] = []
        for index, cite in enumerate(citations):
            chunk_id = str(cite.get("chunk_id") or "") or None
            status = str(cite.get("status") or "ok")[:32] or "ok"
            meta = evidence_by_chunk.get(chunk_id or "")
            document_id = (
                str(cite.get("document_id") or (meta or {}).get("doc_id") or "") or None
            )
            revision_id = str(cite.get("chunk_revision_id") or (meta or {}).get("revision") or "") or None
            if chunk_id and str(chunk_id).startswith("qa::"):
                qa_rev = cite.get("qa_revision")
                if qa_rev is None:
                    qa_rev = _qa_revision_from_meta(meta)
                try:
                    if qa_rev is not None:
                        revision_id = f"qa-rev:{int(qa_rev)}"
                except (TypeError, ValueError):
                    pass
            evidence_rows.append(
                {
                    "seq": index,
                    "chunk_id": chunk_id,
                    "document_id": document_id,
                    "chunk_revision_id": revision_id,
                    "citation_status": status,
                    "evidence_digest": digest_sha256(chunk_id, status, document_id),
                }
            )
            chain_parts.append(f"{chunk_id}:{status}")
        if not evidence_rows and evidence:
            for index, item in enumerate(evidence[:20]):
                chunk_id = str(item.get("chunk_id") or "") or None
                document_id = str(item.get("doc_id") or "") or None
                revision_id = None
                if chunk_id and str(chunk_id).startswith("qa::"):
                    qa_rev = _qa_revision_from_meta(item)
                    if qa_rev is not None:
                        revision_id = f"qa-rev:{qa_rev}"
                evidence_rows.append(
                    {
                        "seq": index,
                        "chunk_id": chunk_id,
                        "document_id": document_id,
                        "chunk_revision_id": revision_id,
                        "citation_status": "ok",
                        "evidence_digest": digest_sha256(chunk_id, "ok", document_id),
                    }
                )
                chain_parts.append(f"{chunk_id}:ok")

        query_digest = digest_sha256(question)
        answer_digest = digest_sha256(answer)
        chain_digest = digest_sha256("|".join(chain_parts))
        fact_id = _new_id("af")
        fact_digest = digest_sha256(
            tenant_id,
            dataset_id,
            run_id,
            query_digest,
            answer_digest,
            chain_digest,
            outcome,
            route_code,
        )
        preview = safe_preview(question) if include_preview else None
        with Session(self.engine, expire_on_commit=False) as session:
            session.add(
                TenantKnowledgeAnswerFact(
                    id=fact_id,
                    tenant_id=tenant_id,
                    dataset_id=dataset_id or "default",
                    run_id=(run_id or None) and str(run_id)[:64],
                    request_id_digest=digest_sha256(request_id)[:64],
                    query_digest=query_digest[:64],
                    answer_digest=answer_digest[:64],
                    evidence_chain_digest=chain_digest[:64],
                    fact_digest=fact_digest[:64],
                    safe_query_preview=preview,
                    outcome_code=outcome,
                    route_code=route_code,
                    citation_count=len(citations),
                    evidence_count=len(evidence_rows),
                    observed_at=_utc_now(),
                )
            )
            for row in evidence_rows:
                session.add(
                    TenantKnowledgeAnswerEvidenceRef(
                        id=_new_id("aer"),
                        tenant_id=tenant_id,
                        answer_fact_id=fact_id,
                        seq=int(row["seq"]),
                        chunk_id=row["chunk_id"],
                        chunk_revision_id=row["chunk_revision_id"],
                        document_id=row["document_id"],
                        citation_status=row["citation_status"],
                        evidence_digest=row["evidence_digest"][:64],
                        created_at=_utc_now(),
                    )
                )
            session.commit()
        return fact_id

    def _refs(self, session: Session, tenant_id: str, fact_id: str) -> list[Any]:
        return list(
            session.scalars(
                select(TenantKnowledgeAnswerEvidenceRef)
                .where(
                    TenantKnowledgeAnswerEvidenceRef.tenant_id == tenant_id,
                    TenantKnowledgeAnswerEvidenceRef.answer_fact_id == fact_id,
                )
                .order_by(TenantKnowledgeAnswerEvidenceRef.seq)
            )
        )

    def get_answer_fact(
        self, tenant_id: str, fact_id: str, *, dataset_id: str | None = None
    ) -> dict[str, Any] | None:
        with Session(self.engine, expire_on_commit=False) as session:
            query = select(TenantKnowledgeAnswerFact).where(
                TenantKnowledgeAnswerFact.tenant_id == tenant_id,
                TenantKnowledgeAnswerFact.id == fact_id,
            )
            if dataset_id:
                query = query.where(TenantKnowledgeAnswerFact.dataset_id == dataset_id)
            fact = session.scalar(query)
            if fact is None:
                return None
            return fact_payload(fact, self._refs(session, tenant_id, fact_id))

    def get_by_run(
        self, tenant_id: str, run_id: str, *, dataset_id: str | None = None
    ) -> dict[str, Any] | None:
        with Session(self.engine, expire_on_commit=False) as session:
            query = select(TenantKnowledgeAnswerFact).where(
                TenantKnowledgeAnswerFact.tenant_id == tenant_id,
                TenantKnowledgeAnswerFact.run_id == run_id,
            )
            if dataset_id:
                query = query.where(TenantKnowledgeAnswerFact.dataset_id == dataset_id)
            fact = session.scalar(
                query.order_by(TenantKnowledgeAnswerFact.observed_at.desc()).limit(1)
            )
            if fact is None:
                return None
            return fact_payload(fact, self._refs(session, tenant_id, fact.id))

    def list_answer_facts(
        self,
        tenant_id: str,
        *,
        dataset_id: str | None = None,
        outcome: str | None = None,
        run_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit or 50), 200))
        with Session(self.engine, expire_on_commit=False) as session:
            query = select(TenantKnowledgeAnswerFact).where(
                TenantKnowledgeAnswerFact.tenant_id == tenant_id
            )
            if dataset_id:
                query = query.where(TenantKnowledgeAnswerFact.dataset_id == dataset_id)
            if outcome:
                query = query.where(TenantKnowledgeAnswerFact.outcome_code == outcome)
            if run_id:
                query = query.where(TenantKnowledgeAnswerFact.run_id == run_id)
            rows = list(
                session.scalars(
                    query.order_by(TenantKnowledgeAnswerFact.observed_at.desc()).limit(limit)
                )
            )
            items = [
                fact_payload(row, self._refs(session, tenant_id, row.id)) for row in rows
            ]
            return {"items": items, "count": len(items)}
