from __future__ import annotations

from datetime import datetime, timezone

from config.settings import MilvusSettings
from core.milvus_client import RagMilvusClient
from models.schemas import Chunk


def test_milvus_row_round_trip_preserves_document_and_content_revisions() -> None:
    client = RagMilvusClient(MilvusSettings(uri="./unused.db", dim=2))
    chunk = Chunk(
        chunk_id="chunk-1",
        doc_id="doc-1",
        text="content",
        text_hash="hash",
        created_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_revision=3,
        content_revision=2,
    )

    row = client._chunk_to_row(chunk, [0.1, 0.2])
    restored = client._row_to_chunk(row)

    assert row["document_revision"] == 3
    assert row["content_revision"] == 2
    assert restored.document_revision == 3
    assert restored.content_revision == 2
    assert "document_revision" in client._OUTPUT_FIELDS
    assert "content_revision" in client._OUTPUT_FIELDS
