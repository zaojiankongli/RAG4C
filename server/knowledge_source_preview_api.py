"""Authenticated, tenant-scoped view of a document's original bytes.

The operator surface has had chunk text, revisions and parse metadata for a long time, but
never the file itself: ``ParsedContextPane`` literally renders an Alert admitting there is no
preview. This is the controlled endpoint that Alert asks for.

Three things are deliberately not possible here:

* **Reading anything that is not this document's own recorded path.** The row is loaded with
  ``tenant_id + dataset_id + id`` (the same triple the chunk API uses), and the path comes
  from the row — never from a request parameter.
* **Reading it wherever the process can reach.** ``locate_preview_source`` refuses unless the
  resolved path sits inside a configured ``catalog.source_preview_roots`` entry, and the
  feature is off when that list is empty (see ``core/source_preview_access``).
* **Choosing the content type at request time.** The media type and the inline/download
  disposition come from ``core/source_previews``. An unlisted suffix is refused rather than
  served as ``application/octet-stream``, and ``?disposition=attachment`` can only downgrade
  an inline-able type, never upgrade one.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from fastapi.security import HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from core import catalog
from core.knowledge_permissions import KNOWLEDGE_READ
from core.source_preview_access import SourcePreviewRefused, locate_preview_source
from models.orm import Document
from server.knowledge_auth import KnowledgeActor, require_knowledge_permission, resolve_path_dataset

_OPENAPI_BEARER = HTTPBearer(
    auto_error=False,
    scheme_name="KnowledgeBearerAuth",
    description="Actor-bound signed KnowledgeOps bearer token.",
)
router = APIRouter(
    prefix="/api/knowledge-bases/{dataset_id}/documents/{doc_id}/source-preview",
    tags=["knowledge-source-preview"],
    dependencies=[Depends(_OPENAPI_BEARER)],
)
_SAFE_ID = r"^[^\x00-\x1f\x7f]+$"
DatasetId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]
DocumentId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]
_READ = require_knowledge_permission(KNOWLEDGE_READ, resolve_path_dataset("dataset_id"))
ReadActor = Annotated[KnowledgeActor, Depends(_READ)]

#: A filename goes into a response header. Anything that could break out of the quoted
#: string (or split the header) is replaced before encoding.
_FILENAME_MAX = 120


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "knowledge_resource_not_found", "message": "资源不存在"},
    )


def _refused(code: str, message: str, status: int = 409) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _configured_preview_roots() -> list[str]:
    """Directories that hold knowledge-base originals. Absent or broken config means *no*
    roots, which the accessor reports as ``source_preview_disabled`` — a missing
    configuration must never widen what the process is allowed to read."""
    try:
        from config.settings import get_settings

        return list(get_settings().catalog.source_preview_roots)
    except Exception:  # noqa: BLE001 - no config means the feature stays off
        return []


def _scoped_document(engine: Any, actor: KnowledgeActor, dataset_id: str, doc_id: str) -> Document:
    with Session(engine, expire_on_commit=False) as session:
        document = session.scalar(
            select(Document).where(
                Document.id == doc_id,
                Document.tenant_id == actor.tenant_id,
                Document.dataset_id == dataset_id,
            )
        )
        if document is None:
            raise _not_found()
        return document


def _disposition_filename(name: Any, fallback: str) -> str:
    raw = str(name or "").strip() or fallback
    cleaned = "".join(char if char >= " " and char != "\x7f" else "_" for char in raw)
    cleaned = cleaned.replace('"', "_").replace("\\", "_").strip() or fallback
    return cleaned[:_FILENAME_MAX]


def _content_disposition(filename: str, inline: bool) -> str:
    """RFC 5987 only: the ASCII ``filename=`` form is a downgraded copy, so a CJK name never
    has to be smuggled through quotes."""
    kind = "inline" if inline else "attachment"
    ascii_name = filename.encode("ascii", "ignore").decode("ascii") or "document"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


@router.get("")
def read_source_preview(
    dataset_id: DatasetId,
    doc_id: DocumentId,
    actor: ReadActor,
    disposition: Annotated[Literal["inline", "attachment"], Query(pattern="^(inline|attachment)$")] = "inline",
) -> Response:
    """Hand back the recorded original file for this document, as the declared content type."""
    engine = catalog.get_engine()
    document = _scoped_document(engine, actor, dataset_id, doc_id)
    try:
        source = locate_preview_source(document.file_path, roots=_configured_preview_roots())
    except SourcePreviewRefused as exc:
        raise _refused(exc.code, exc.message, status=_refused_status(exc.code)) from exc
    try:
        payload = source.path.read_bytes()
    except OSError as exc:
        raise _refused(
            "source_preview_unavailable", "原文读取失败"
        ) from exc
    if len(payload) != source.size:
        # 尺寸和放行判定不一致 = 文件在两次系统调用之间被换掉了。宁可不给，也不发一份
        # 没人核对过的字节。
        raise _refused(
            "source_preview_unavailable", "原文在读取期间发生变化，未返回任何内容"
        )
    inline = source.spec.inline_renderable and disposition != "attachment"
    filename = _disposition_filename(document.name, source.path.name)
    etag = f'"{int(document.content_revision or 0)}-{(document.file_hash or "")[:12]}"'
    return Response(
        content=payload,
        media_type=source.spec.media_type,
        headers={
            "Content-Disposition": _content_disposition(filename, inline),
            "X-Content-Type-Options": "nosniff",
            # 原文是可变的本地文件：不缓存，也不让浏览器自己猜类型。
            "Cache-Control": "private, must-revalidate",
            "ETag": etag,
            "Content-Security-Policy": "sandbox",
            "Referrer-Policy": "no-referrer",
        },
    )


def _refused_status(code: str) -> int:
    """404 只用于"这份文档没有可看的原文"；配置与策略类拒绝是 409/503，好让操作员能分辨
    "这份没有" 与 "这台机器没开这个能力"。"""
    if code == "source_preview_missing":
        return 404
    if code == "source_preview_disabled":
        return 503
    return 409
