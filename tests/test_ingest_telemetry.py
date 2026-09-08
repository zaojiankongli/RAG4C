from __future__ import annotations

from indexing.ingest import IngestPipeline
from server.document_metrics import aggregate_document_metrics


class Embedder:
    def embed_texts(self, texts):
        return [[0.1, 0.2] for _ in texts]


class Milvus:
    def upsert_chunks(self, chunks, dense_vectors=None):
        return [chunk.chunk_id for chunk in chunks]


def test_ingest_meta_contains_stage_timings_and_counts(tmp_path):
    path = tmp_path / "guide.md"
    path.write_text("# Guide\n\n" + "knowledge base content " * 40, encoding="utf-8")
    pipeline = IngestPipeline(embedder=Embedder(), milvus=Milvus())

    result = pipeline.add_file(str(path), doc_id="doc-guide")
    meta = pipeline.ingest_meta(result)

    assert meta["parse_ms"] >= 0
    assert meta["total_ms"] >= meta["parse_ms"]
    assert set(meta["stage_ms"]) >= {"parse", "clean", "split", "contextual", "embed", "insert", "graph"}
    assert meta["text_chars"] > 100
    assert meta["segment_count"] >= 1
    assert meta["chunk_count"] == result.chunk_count


def test_document_metrics_aggregates_new_and_legacy_metadata():
    documents = [
        {"id": "a", "name": "a.md", "status": "completed", "chunk_count": 4, "doc_type": "markdown", "parser_meta": {"parse_ms": 2.5, "total_ms": 1200, "engine": "fast", "stage_ms": {"embed": 900}}},
        {"id": "b", "name": "b.pdf", "status": "error", "chunk_count": 0, "doc_type": "pdf", "error_message": "ocr failed", "parser_meta": {}},
        {"id": "c", "name": "c.pdf", "status": "parsing", "chunk_count": 0, "doc_type": "pdf", "parser_meta": {"parse_ms": 100}},
    ]

    payload = aggregate_document_metrics(documents)

    assert payload["summary"] == {"total": 3, "completed": 1, "failed": 1, "processing": 1, "chunks": 4}
    assert payload["legacy_metadata_count"] == 1
    assert payload["latency_ms"]["parse"]["p50"] == 51.25
    assert payload["engine_distribution"] == [{"name": "fast", "value": 1}]
    assert payload["recent_failures"][0]["document_id"] == "b"
