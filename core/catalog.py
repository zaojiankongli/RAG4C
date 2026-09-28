"""多租户目录服务（Catalog）：SQLAlchemy 引擎管理 + 关系 CRUD。

- 数据文件：data/rag4c.db（默认，可由 RAG4C_CATALOG_DB_PATH 覆盖）；
- 引擎为进程级单例，首次使用时自动建表（幂等 create_all）；
- DAO 均为线程安全（每操作独立 Session，sqlite 引擎用 check_same_thread=False）；

## 用量记账的单一真账

``documents`` 表是**唯一真账**：一篇文档在库里存在，当且仅当它有一行
``Document``；它占了多少 chunk，以 ``documents.chunk_count`` 为准。

``tenants`` / ``datasets`` 上的 ``doc_count`` / ``chunk_count`` 是这本真账的
**缓存**，存在的唯一理由是让配额闸门能一条 UPDATE 原子占用（见
:func:`create_document`），而不必先读后写——后者是 TOCTOU，多进程并发下拦不住。

缓存靠两条规则维持，不靠调用方自觉：

1. 任何改动 ``documents.chunk_count`` 的写入都走 :func:`set_document_status`，
   它在**同一个事务里**把差量记进聚合列；
2. 任何删除文档的操作都走 :func:`remove_document` / :func:`purge_dataset`。

这两条是针对一类具体事故定的：此前记账是"调用方的义务"——状态机记得调
``bump_counts``，源同步（``sources/runner.py``）不记得，于是源同步入库的文档
一篇都不计数。缓存一旦偏了，配额就永远拦不住，而没有任何报错。

缓存偏了怎么办：:func:`recount_usage` 从真账重算，:func:`bump_counts` 在下限
被钳到 0 时会打 warning（那正是"欠账"的现场）。

## 两道配额闸门

- **文档数**：:func:`create_document` / :func:`register_synced_document` 里的
  条件 UPDATE（``WHERE doc_count < quota_documents``），原子占用，无 TOCTOU；
- **chunk 数**：:func:`check_quota` 读用量后拒绝，落地在
  ``indexing.ingest.IngestPipeline._enforce_chunk_quota``（嵌入之前）。
  这条是 check-then-act，会有小幅超额；chunk 数在解析完才知道，没法预先占用。

本模块惰性导入 sqlalchemy（未安装时仅在使用时报错，不影响系统其余部分）。
"""

from __future__ import annotations

import re
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

from core.document_sorts import (
    DocumentSortSpec,
    cursor_capable_sorts,
    register_document_sort,
    resolve_document_sort,
)
from core.observability import get_logger

_logger = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

_engine_lock = threading.Lock()
_engine: Any = None
_engine_key: str = ""
_db_path: Optional[Path] = None
_catalog_write_lock = threading.RLock()


class CatalogQuotaError(RuntimeError):
    """Raised when an atomic catalog write would exceed tenant quota."""


class DocumentCatalogCapabilityError(RuntimeError):
    """Raised when a catalog query cannot be executed safely on the active schema."""


def new_id(prefix: str) -> str:
    """生成带前缀的短 ID（关系表主键）。"""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _resolve_db_url() -> tuple[str, Optional[Path]]:
    """按配置解析 SQLAlchemy URL（db_url 非空优先，否则 SQLite 文件路径）。

    Returns:
        (sqlalchemy_url, sqlite_path_or_none)
    """
    url = ""
    raw = "data/rag4c.db"
    try:
        from config.settings import get_settings

        cfg = get_settings().catalog
        url = str(getattr(cfg, "db_url", "") or "").strip()
        raw = cfg.db_path
    except Exception:  # noqa: BLE001 - 配置不可用时回退默认
        pass
    if url:
        return url, None
    path = Path(raw)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return f"sqlite:///{path.as_posix()}", path


def _catalog_schema_mode() -> str:
    """返回目录库 schema 策略；配置不可用时保持旧版启动行为。"""
    try:
        from config.settings import get_settings

        return str(get_settings().catalog.schema_mode)
    except Exception:  # noqa: BLE001 - 配置失败时保留 legacy 兼容路径
        return "legacy"


def _connect_args_for(url: str, timeout_s: float) -> dict[str, Any]:
    """把"连接等待上限"翻译成**对应驱动**认的 connect_args。

    这里是两个驱动的差异点，踩过一次：psycopg2 只接受**整数**秒
    （传 5.0 会直接报 ``invalid integer value "5.0"``），而 pymysql 接受浮点。
    方言差异必须落在一处，不能让调用方各写各的。
    """
    if timeout_s <= 0:
        return {}
    lowered = (url or "").lower()
    if lowered.startswith("postgres"):
        return {"connect_timeout": max(1, int(round(timeout_s)))}
    if lowered.startswith("sqlite"):
        # sqlite3 的 Connection 不认 connect_timeout（它自己的参数是 timeout=），
        # 透传下去会直接 TypeError，于是 verify 模式下任何 sqlite 目录库都起不来。
        return {}
    return {"connect_timeout": float(timeout_s)}


def _remote_engine_kwargs(url: str = "") -> dict[str, Any]:
    """远程库（MySQL / PG 等）的 ``create_engine`` 参数。

    单独抽出来的两个理由：
    1. 可测——不需要真的建引擎就能断言连接超时被下发；
    2. 只有一处决定"等连接多久"，不会出现某个调用点漏配、于是悄悄等满 10 秒。
    """
    timeout = 10.0
    resolved_url = url
    try:
        # 与本文件其他位置一样惰性导入：core.catalog 要能在没装配配置时被 import
        from config.settings import get_settings

        cfg = get_settings().catalog
        timeout = float(cfg.connect_timeout_s)
        resolved_url = url or str(cfg.db_url)
    except Exception:  # noqa: BLE001 - 配置读不到就用驱动默认，不因此起不来
        timeout = 10.0
    kwargs: dict[str, Any] = {
        "pool_pre_ping": True,
        "pool_recycle": 3600,
        "pool_size": 5,
        "max_overflow": 10,
    }
    kwargs["connect_args"] = _connect_args_for(resolved_url, timeout)
    return kwargs


def get_engine() -> Any:
    """进程级 SQLAlchemy Engine 单例，并按配置创建或验证 schema。

    - legacy：保留旧部署的 create_all 启动行为；
    - verify：只读验证 Alembic revision，绝不自动创建或升级表。
    """
    global _engine, _engine_key, _db_path
    with _engine_lock:
        url, sqlite_path = _resolve_db_url()
        schema_mode = _catalog_schema_mode()
        engine_key = f"{url}|schema={schema_mode}"
        if _engine is None or _engine_key != engine_key:
            try:
                from sqlalchemy import create_engine
            except ImportError as exc:  # pragma: no cover - 依赖缺失路径
                raise RuntimeError("sqlalchemy 未安装。请执行 pip install sqlalchemy>=2.0") from exc
            if sqlite_path is not None:
                sqlite_path.parent.mkdir(parents=True, exist_ok=True)
                _engine = create_engine(
                    url,
                    connect_args={"check_same_thread": False, "timeout": 30},
                )
                from sqlalchemy import event

                @event.listens_for(_engine, "connect")
                def _configure_sqlite(dbapi_connection: Any, _: Any) -> None:
                    cursor = dbapi_connection.cursor()
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA foreign_keys=ON")
                    cursor.execute("PRAGMA busy_timeout=30000")
                    cursor.close()
            else:
                _engine = create_engine(url, **_remote_engine_kwargs(url))
            from core.catalog_schema import safe_database_label

            try:
                if schema_mode == "verify":
                    from core.catalog_schema import verify_catalog_schema

                    verify_catalog_schema(_engine)
                else:
                    from models.orm import Base

                    Base.metadata.create_all(_engine)
            except Exception:
                _engine.dispose()
                _engine = None
                raise
            _engine_key = engine_key
            _db_path = sqlite_path
            _logger.info(
                "Catalog 已初始化: database=%s schema_mode=%s",
                safe_database_label(url),
                schema_mode,
            )
        return _engine


def _session():
    """创建独立 Session（调用方负责 with 块管理）。"""
    from sqlalchemy.orm import Session

    return Session(get_engine(), expire_on_commit=False)


def _insert_if_absent(session: Any, model: Any, values: dict[str, Any]) -> None:
    """Insert one primary-key row without failing on a concurrent creator."""
    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert

        statement = insert(model).values(**values)
        session.execute(statement.on_conflict_do_nothing(index_elements=["id"]))
        return
    if dialect in {"mysql", "mariadb"}:
        from sqlalchemy.dialects.mysql import insert

        statement = insert(model).values(**values)
        session.execute(statement.on_duplicate_key_update(id=statement.inserted.id))
        return
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert

        statement = insert(model).values(**values)
        session.execute(statement.on_conflict_do_nothing(index_elements=["id"]))
        return

    # Savepoint fallback keeps the surrounding document transaction usable
    # when another process wins the primary-key race.
    from sqlalchemy.exc import IntegrityError

    try:
        with session.begin_nested():
            session.add(model(**values))
            session.flush()
    except IntegrityError:
        pass


def _ensure_tenant_row(
    session: Any,
    tenant_id: str,
    *,
    name: str = "",
    plan: str = "free",
) -> Any:
    from models.orm import Tenant

    _insert_if_absent(
        session,
        Tenant,
        {"id": tenant_id, "name": name or tenant_id, "plan": plan},
    )
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:  # pragma: no cover - indicates a broken DB transaction
        raise RuntimeError(f"租户创建后不可见: {tenant_id}")
    from sqlalchemy import inspect

    if "tenant_release_channels" in set(inspect(session.connection()).get_table_names()):
        from core.enterprise_release_channels import ensure_default_release_channels_in_session

        ensure_default_release_channels_in_session(
            session,
            tenant_id=tenant_id,
            actor_id="system:catalog-tenant-provisioner",
        )
    return tenant


def _ensure_dataset_row(
    session: Any,
    tenant_id: str,
    dataset_id: str,
    *,
    name: str = "",
    description: str = "",
) -> Any:
    from models.orm import Dataset

    _insert_if_absent(
        session,
        Dataset,
        {
            "id": dataset_id,
            "tenant_id": tenant_id,
            "name": name or dataset_id,
            "description": description,
        },
    )
    dataset = session.get(Dataset, dataset_id)
    if dataset is None:  # pragma: no cover - indicates a broken DB transaction
        raise RuntimeError(f"知识库创建后不可见: {dataset_id}")
    if dataset.tenant_id != tenant_id:
        raise ValueError(
            f"知识库 {dataset_id!r} 属于租户 {dataset.tenant_id!r}，不能以租户 {tenant_id!r} 写入"
        )
    return dataset


def reset_engine() -> None:
    """关闭并重置引擎（测试用）。"""
    global _engine, _engine_key, _db_path
    with _engine_lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _engine_key = ""
        _db_path = None


# ---------------------------------------------------------------------------
# 租户 / 数据集 / 文档 DAO
# ---------------------------------------------------------------------------


def ensure_tenant(tenant_id: str, name: str = "", plan: str = "free") -> dict[str, Any]:
    """按 ID 取租户；不存在则创建（幂等）。"""
    with _catalog_write_lock, _session() as s:
        t = _ensure_tenant_row(s, tenant_id, name=name, plan=plan)
        s.commit()
        return {"id": t.id, "name": t.name, "plan": t.plan, "status": t.status}


def ensure_dataset(
    tenant_id: str, dataset_id: str, name: str = "", description: str = ""
) -> dict[str, Any]:
    """按 ID 取知识库；不存在则创建（幂等）。"""
    with _catalog_write_lock, _session() as s:
        _ensure_tenant_row(s, tenant_id)
        d = _ensure_dataset_row(
            s,
            tenant_id,
            dataset_id,
            name=name,
            description=description,
        )
        s.commit()
        return {
            "id": d.id,
            "tenant_id": d.tenant_id,
            "name": d.name,
            "doc_count": d.doc_count,
            "chunk_count": d.chunk_count,
        }


def list_datasets(tenant_id: str) -> list[dict[str, Any]]:
    """列出租户下的知识库。"""
    from sqlalchemy import select

    from models.orm import Dataset

    with _session() as s:
        rows = s.scalars(
            select(Dataset).where(Dataset.tenant_id == tenant_id).order_by(Dataset.created_at)
        ).all()
        return [
            {
                "id": d.id,
                "name": d.name,
                "description": d.description,
                "doc_count": d.doc_count,
                "chunk_count": d.chunk_count,
                "status": d.status,
            }
            for d in rows
        ]


def check_quota(tenant_id: str, add_chunks: int = 0) -> tuple[bool, str]:
    """chunk 配额检查：当前用量 + 增量是否超出租户配额。

    Returns:
        (ok, reason)：ok=False 时 reason 为可读说明。

    这里**只管 chunk，不管文档数**。曾经有过一个 ``add_documents`` 形参，全仓
    零生产调用点，评审计划把它记成"缺一个调用点"——那个判断是错的，不该补调用
    点，该删。文档数配额早已由 :func:`create_document` 的条件 UPDATE 原子占用，
    那是严格更强的机制：先读后写在多进程并发下拦不住（两个进程同时读到"还差
    一篇"，然后各写各的），而 ``WHERE doc_count < quota_documents`` 的
    rowcount 判定不会。留着一个更弱的同名闸门，只会等着被误用。

    chunk 侧没这个待遇：chunk 数要解析完才知道，没法在建文档时预先占用，所以
    只能 check-then-act，代价是并发入库时可能小幅超额。这是能力差异，不是疏忽。
    """
    from models.orm import Tenant

    with _session() as s:
        t = s.get(Tenant, tenant_id)
        if t is None:
            return True, ""
        if add_chunks > 0 and t.quota_chunks > 0:
            if t.chunk_count + add_chunks > t.quota_chunks:
                return False, (f"chunk 配额不足：已用 {t.chunk_count}，上限 {t.quota_chunks}")
        return True, ""


def bump_counts(tenant_id: str, dataset_id: str, doc_delta: int, chunk_delta: int) -> None:
    """调整租户 / 知识库计数（doc_count / chunk_count，可为负）。

    低层原语。**正常路径不该直接调它**——记账应当跟着事实走同一个事务，见
    :func:`set_document_status` 与 :func:`remove_document`。这里保留它是给
    :func:`recount_usage` 之外的修补场景用的。

    下限钳 0：计数不该是负数。但"钳到了"本身是**欠账的现场**——说明有人删掉的
    比记上的多，也就是某条入库路径没记账。此前这个钳是静默的，于是唯一能证明
    记账漏了的信号被当场吞掉。现在它打 warning。
    """
    from sqlalchemy import case, update
    from models.orm import Dataset, Tenant

    if not doc_delta and not chunk_delta:
        return

    with _session() as s:
        tenant_row = s.get(Tenant, tenant_id)
        if tenant_row is not None:
            _warn_on_clamp("租户", tenant_id, tenant_row, doc_delta, chunk_delta)
        result = s.execute(
            update(Tenant)
            .where(Tenant.id == tenant_id)
            .values(
                doc_count=case(
                    (Tenant.doc_count + doc_delta < 0, 0),
                    else_=Tenant.doc_count + doc_delta,
                ),
                chunk_count=case(
                    (Tenant.chunk_count + chunk_delta < 0, 0),
                    else_=Tenant.chunk_count + chunk_delta,
                ),
            )
        )
        if result.rowcount != 1:
            # 记账写去了不存在的租户 = 这笔账彻底丢了。此前静默返回，于是
            # "配额永远是 0" 和 "租户 id 拼错了" 长得一模一样。
            _logger.warning(
                "用量记账未命中租户 %r（doc%+d chunk%+d），这笔账已丢失",
                tenant_id,
                doc_delta,
                chunk_delta,
            )
        if dataset_id:
            dataset_row = s.get(Dataset, dataset_id)
            if dataset_row is not None:
                _warn_on_clamp("知识库", dataset_id, dataset_row, doc_delta, chunk_delta)
            s.execute(
                update(Dataset)
                # 带上 tenant_id：dataset_id 由调用方传入，跨租户重名时不带这个
                # 条件就会把账记到别人库上。
                .where(Dataset.id == dataset_id, Dataset.tenant_id == tenant_id)
                .values(
                    doc_count=case(
                        (Dataset.doc_count + doc_delta < 0, 0),
                        else_=Dataset.doc_count + doc_delta,
                    ),
                    chunk_count=case(
                        (Dataset.chunk_count + chunk_delta < 0, 0),
                        else_=Dataset.chunk_count + chunk_delta,
                    ),
                )
            )
        s.commit()


def _warn_on_clamp(kind: str, ident: str, row: Any, doc_delta: int, chunk_delta: int) -> None:
    """计数将被钳到 0 时打一条 warning（欠账的唯一可见信号）。"""
    if row.doc_count + doc_delta < 0:
        _logger.warning(
            "%s %r 的文档计数将被钳到 0（%d%+d）——说明有入库路径没记账",
            kind,
            ident,
            row.doc_count,
            doc_delta,
        )
    if row.chunk_count + chunk_delta < 0:
        _logger.warning(
            "%s %r 的 chunk 计数将被钳到 0（%d%+d）——说明有入库路径没记账",
            kind,
            ident,
            row.chunk_count,
            chunk_delta,
        )


def _apply_usage_delta(
    session: Any, tenant_id: str, dataset_id: str, doc_delta: int, chunk_delta: int
) -> None:
    """在**调用方的事务里**把差量记进聚合列。

    与 :func:`bump_counts` 的区别只有一个，但那是全部要害：它不开自己的事务。
    记账必须和"被记的那件事"同生共死——两个事务意味着中间有个窗口，进程在那儿
    崩掉就是永久性偏账，而且没有任何痕迹。
    """
    from sqlalchemy import case, update
    from models.orm import Dataset, Tenant

    if not doc_delta and not chunk_delta:
        return
    session.execute(
        update(Tenant)
        .where(Tenant.id == tenant_id)
        .values(
            doc_count=case(
                (Tenant.doc_count + doc_delta < 0, 0),
                else_=Tenant.doc_count + doc_delta,
            ),
            chunk_count=case(
                (Tenant.chunk_count + chunk_delta < 0, 0),
                else_=Tenant.chunk_count + chunk_delta,
            ),
        )
    )
    if dataset_id:
        session.execute(
            update(Dataset)
            .where(Dataset.id == dataset_id, Dataset.tenant_id == tenant_id)
            .values(
                doc_count=case(
                    (Dataset.doc_count + doc_delta < 0, 0),
                    else_=Dataset.doc_count + doc_delta,
                ),
                chunk_count=case(
                    (Dataset.chunk_count + chunk_delta < 0, 0),
                    else_=Dataset.chunk_count + chunk_delta,
                ),
            )
        )


# ---------------------------------------------------------------------------
# 文档状态机 DAO
# ---------------------------------------------------------------------------


def _optional_identity(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _source_uri_hash(source_uri: str | None) -> str | None:
    normalized = _optional_identity(source_uri)
    if normalized is None:
        return None
    import hashlib

    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def create_document(
    tenant_id: str,
    dataset_id: str,
    name: str,
    file_path: str = "",
    file_hash: str = "",
    doc_type: str = "",
    doc_id: str | None = None,
    source_uri: str | None = None,
    external_id: str | None = None,
    logical_folder_path: str | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
) -> dict[str, Any]:
    """原子确保父级并登记文档，稳定来源身份不依赖本机缓存路径。"""
    from sqlalchemy import or_, update
    from models.orm import Dataset, Document, Tenant

    did = doc_id or new_id("doc")
    with _catalog_write_lock:
        with _session() as session:
            _ensure_tenant_row(session, tenant_id)
            _ensure_dataset_row(session, tenant_id, dataset_id)
            document = Document(
                id=did,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                name=name,
                file_path=file_path,
                file_hash=file_hash,
                doc_type=doc_type,
                source_uri=_optional_identity(source_uri),
                source_uri_hash=_source_uri_hash(source_uri),
                external_id=_optional_identity(external_id),
                logical_folder_path=_optional_identity(logical_folder_path),
                source_type=_optional_identity(source_type),
                source_id=_optional_identity(source_id),
            )
            session.add(document)
            session.flush()
            tenant_update = session.execute(
                update(Tenant)
                .where(
                    Tenant.id == tenant_id,
                    or_(
                        Tenant.quota_documents <= 0,
                        Tenant.doc_count < Tenant.quota_documents,
                    ),
                )
                .values(doc_count=Tenant.doc_count + 1)
            )
            if tenant_update.rowcount != 1:
                raise CatalogQuotaError(f"文档配额不足：租户 {tenant_id!r} 已达到文档数上限")
            session.execute(
                update(Dataset)
                .where(Dataset.id == dataset_id)
                .values(doc_count=Dataset.doc_count + 1)
            )
            result = {
                "id": document.id,
                "status": document.status,
                "progress": document.progress,
                "dataset_id": document.dataset_id,
            }
            session.commit()
            return result


#: 错误文案里的绝对路径（Windows ``D:\a\b.md`` / ``D:\\a\\b.md`` 与 POSIX
#: ``/a/b.md`` 都算）。用于把服务端路径压成文件名。
_ABS_PATH_RE = re.compile(r"""(?:[A-Za-z]:[\\/]|/)(?:[^\s'"）)]*[\\/])*[^\s'"）)]*""")

#: 错误文案尾巴上的 ``（doc_id='doc-xxx'）`` 片段。
_DOC_ID_TAIL_RE = re.compile(r"[（(]\s*doc_id=['\"]?[^）)]*['\"]?\s*[）)]")


def sanitize_error_message(raw: str) -> str:
    """把内部异常文本收拾成能给用户看的样子。

    在此之前，页面上原样显示的是 Python 的 repr：

        解析器不支持该文件类型: 'D:\\\\program_project\\\\python_project\\\\RAG4C
        \\\\data\\\\test_kb.md'（doc_id='doc-efeaae642df9'）

    三个毛病。**双反斜杠**是 ``{path!r}`` 的转义，用户看着像乱码；**绝对路径**
    把服务端的目录结构摊给了所有能打开这个页面的人，属于不必要的信息暴露；
    **doc_id** 在表格里本来就有独立一列，重复一遍纯属噪音。

    放在 ``set_document_status`` 这一个写入口做，而不是让三处调用方各自
    ``str(exc)`` 之前先清洗一遍——调用方总会有人忘，写入口只有一个。
    原始异常仍完整落在日志里，排查不受影响：这里收拾的只是**展示**。
    """
    if not raw:
        return raw
    text = raw.replace("\\\\", "\\")
    text = _DOC_ID_TAIL_RE.sub("", text)
    text = _ABS_PATH_RE.sub(lambda m: Path(m.group(0).replace("\\", "/")).name, text)
    return text.strip().strip("：: ")


def set_document_status(
    doc_id: str,
    status: str,
    detail: str = "",
    progress: float | None = None,
    error_message: str = "",
    chunk_count: int | None = None,
    file_hash: str | None = None,
    parser_meta: dict[str, Any] | None = None,
    content_revision: int | None = None,
    desired_index_revision: int | None = None,
    indexed_revision: int | None = None,
    graph_revision: int | None = None,
) -> dict[str, Any]:
    """更新文档状态（含合法迁移校验）、进度与计数。

    Args:
        parser_meta: 入库可观测元信息（解析引擎 / 页数 / 切分方式 /
            图谱统计等）。传入时整体覆盖旧值；None 表示不改动。

    传了 ``chunk_count`` 时，租户 / 知识库的聚合计数**在同一个事务里**按差量
    跟着改。这不是顺手加的功能，是把记账从"调用方的义务"改成"写入的一部分"：

    - 此前状态机在这个函数之后另起一个事务调 ``bump_counts``。两个事务之间崩
      一次，就是一笔永久对不上的账，且无痕迹。
    - 此前差量由调用方拿函数开头的快照算（``indexing/reindex.py`` 就是这么写
      的），中途有并发写就算错。现在差量在事务内用**当前行**算。
    - 此前状态机的"空文档"早退分支写 ``chunk_count=0`` 却不 bump，于是一篇
      本来有 200 个 chunk 的文档被改空之后，租户头上那 200 个永远挂着。现在
      走同一条路，差量自然是 -200。
    """
    from models.orm import Document, valid_transition

    with _session() as s:
        d = s.get(Document, doc_id)
        if d is None:
            raise KeyError(f"文档不存在: {doc_id}")
        if status != d.status and not valid_transition(d.status, status):
            raise ValueError(f"非法状态迁移: {d.status} -> {status}（文档 {doc_id}）")
        d.status = status
        if detail:
            d.status_detail = detail
        if progress is not None:
            d.progress = max(0.0, min(1.0, float(progress)))
        if error_message:
            d.error_message = sanitize_error_message(error_message)
        chunk_delta = 0
        if chunk_count is not None:
            chunk_delta = int(chunk_count) - int(d.chunk_count or 0)
            d.chunk_count = chunk_count
        if file_hash is not None:
            d.file_hash = file_hash
        if parser_meta is not None:
            d.parser_meta = parser_meta
        if content_revision is not None:
            d.content_revision = max(0, int(content_revision))
        if desired_index_revision is not None:
            d.desired_index_revision = max(0, int(desired_index_revision))
        if indexed_revision is not None:
            d.indexed_revision = max(0, int(indexed_revision))
        if graph_revision is not None:
            d.graph_revision = max(0, int(graph_revision))
        if chunk_delta:
            s.flush()
            _apply_usage_delta(s, d.tenant_id, d.dataset_id, 0, chunk_delta)
        s.commit()
        return {"id": d.id, "status": d.status, "progress": d.progress}


def _normalize_document_tags(values: list[str] | None) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        tag = str(raw).strip()
        if not tag or tag in seen:
            continue
        if len(tag) > 32:
            tag = tag[:32]
        seen.add(tag)
        tags.append(tag)
        if len(tags) >= 20:
            break
    return tags


def _document_management_projection(document: Any) -> dict[str, Any]:
    meta = document.parser_meta if isinstance(document.parser_meta, dict) else {}
    management = meta.get("management") if isinstance(meta.get("management"), dict) else {}
    return {
        "id": document.id,
        "logical_folder_path": document.logical_folder_path or "",
        "tags": _normalize_document_tags(management.get("tags")),
        "parser_meta": meta,
    }


def update_document_management(
    doc_id: str,
    *,
    logical_folder_path: str | None = None,
    tags: list[str] | None = None,
    add_tags: list[str] | None = None,
    remove_tags: list[str] | None = None,
) -> dict[str, Any]:
    """Persist operator-facing category and tags without touching index state."""
    from models.orm import Document

    with _catalog_write_lock, _session() as session:
        document = session.get(Document, doc_id)
        if document is None:
            raise KeyError(f"文档不存在: {doc_id}")
        if logical_folder_path is not None:
            normalized_folder = str(logical_folder_path).strip().strip("/").strip()
            document.logical_folder_path = normalized_folder or None
        meta = dict(document.parser_meta or {})
        management = dict(meta.get("management") or {})
        current = _normalize_document_tags(management.get("tags"))
        if tags is not None:
            current = _normalize_document_tags(tags)
        else:
            removed = set(_normalize_document_tags(remove_tags))
            current = [tag for tag in current if tag not in removed]
            current = _normalize_document_tags(current + _normalize_document_tags(add_tags))
        management["tags"] = current
        meta["management"] = management
        document.parser_meta = meta
        session.commit()
        return _document_management_projection(document)


def remove_document(doc_id: str) -> dict[str, Any] | None:
    """删除文档记录，并在同一事务里退还它占的用量。

    幂等：文档不存在时返回 None（重复删除、或删一篇从未登记过的源同步文档，
    都不该报错）。

    此前**根本没有这个函数**——``IngestPipeline.delete_document`` 只删 Milvus
    里的 chunk，关系库那边文档行照留、用量照挂。于是源同步每清理一篇上游已删
    的文档，租户的 chunk_count 就永久虚高一截，一直涨到配额把新文档全拦掉，
    而界面上显示的用量是"真的"——只是从来没减过。
    """
    from sqlalchemy import delete, select

    from models.orm import (
        ChunkHead,
        ChunkRevision,
        Document,
        DocumentIngestAttempt,
        DocumentIngestSpan,
        DocumentSegment,
        IndexDeadLetter,
        IndexOperation,
    )

    with _catalog_write_lock, _session() as s:
        d = s.get(Document, doc_id)
        if d is None:
            return None
        info = {
            "id": d.id,
            "tenant_id": d.tenant_id,
            "dataset_id": d.dataset_id,
            "chunk_count": int(d.chunk_count or 0),
        }
        attempt_ids = select(DocumentIngestAttempt.id).where(
            DocumentIngestAttempt.document_id == doc_id
        )
        chunk_ids = select(ChunkHead.id).where(ChunkHead.document_id == doc_id)
        for statement in (
            delete(IndexDeadLetter).where(IndexDeadLetter.document_id == doc_id),
            delete(IndexOperation).where(IndexOperation.document_id == doc_id),
            delete(DocumentIngestSpan).where(DocumentIngestSpan.attempt_id.in_(attempt_ids)),
            delete(DocumentIngestAttempt).where(DocumentIngestAttempt.document_id == doc_id),
            delete(ChunkRevision).where(ChunkRevision.chunk_id.in_(chunk_ids)),
            delete(ChunkHead).where(ChunkHead.document_id == doc_id),
            delete(DocumentSegment).where(DocumentSegment.document_id == doc_id),
            delete(Document).where(Document.id == doc_id),
        ):
            s.execute(statement.execution_options(synchronize_session=False))
        s.flush()
        _apply_usage_delta(s, info["tenant_id"], info["dataset_id"], -1, -info["chunk_count"])
        s.commit()
        return info


def purge_dataset(tenant_id: str, dataset_id: str) -> dict[str, Any]:
    """清空一个知识库的全部文档记录并归零它的用量。

    给 ``scripts/ingest_source.py --reset`` 用：那条路径会 ``delete_by_expr``
    把整个 dataset 从 Milvus 里抹掉，此前完全不碰关系库，清完之后用量还是清空
    前的数字——等于用一条运维命令把配额永久烧掉一块。
    """
    from sqlalchemy import delete, func, select
    from models.orm import Dataset, Document

    with _catalog_write_lock, _session() as s:
        agg = s.execute(
            select(func.count(Document.id), func.coalesce(func.sum(Document.chunk_count), 0)).where(
                Document.tenant_id == tenant_id, Document.dataset_id == dataset_id
            )
        ).one()
        docs, chunks = int(agg[0] or 0), int(agg[1] or 0)
        s.execute(
            delete(Document).where(
                Document.tenant_id == tenant_id, Document.dataset_id == dataset_id
            )
        )
        s.flush()
        _apply_usage_delta(s, tenant_id, dataset_id, -docs, -chunks)
        # 知识库自己的两个计数直接归零：它名下已经一篇文档都没有了，用差量去
        # 减反而会把此前的偏账留在里面。
        ds = s.get(Dataset, dataset_id)
        if ds is not None and ds.tenant_id == tenant_id:
            ds.doc_count = 0
            ds.chunk_count = 0
        s.commit()
        return {"removed_documents": docs, "removed_chunks": chunks}


def register_synced_document(
    tenant_id: str,
    dataset_id: str,
    doc_id: str,
    name: str,
    chunk_count: int,
    file_path: str = "",
    file_hash: str = "",
    doc_type: str = "",
    source_uri: str | None = None,
    external_id: str | None = None,
    logical_folder_path: str | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
) -> dict[str, Any]:
    """按稳定来源身份幂等登记源同步文档，并同步聚合用量。"""
    from sqlalchemy import or_, select, update
    from models.orm import Dataset, Document, Tenant

    chunks = max(0, int(chunk_count))
    normalized_uri = _optional_identity(source_uri)
    uri_hash = _source_uri_hash(normalized_uri)
    normalized_external_id = _optional_identity(external_id)
    normalized_source_id = _optional_identity(source_id)
    with _catalog_write_lock, _session() as session:
        _ensure_tenant_row(session, tenant_id)
        _ensure_dataset_row(session, tenant_id, dataset_id)
        document = None
        if uri_hash is not None:
            document = session.scalar(
                select(Document).where(
                    Document.dataset_id == dataset_id,
                    Document.source_uri_hash == uri_hash,
                )
            )
            if document is not None and document.source_uri != normalized_uri:
                raise ValueError("source URI hash collision")
        if document is None and normalized_source_id and normalized_external_id:
            document = session.scalar(
                select(Document).where(
                    Document.dataset_id == dataset_id,
                    Document.source_id == normalized_source_id,
                    Document.external_id == normalized_external_id,
                )
            )
        if document is None:
            document = session.get(Document, doc_id)
        if document is not None and (
            document.tenant_id != tenant_id or document.dataset_id != dataset_id
        ):
            raise ValueError("source document scope mismatch")

        if document is None:
            document = Document(
                id=doc_id,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                name=name,
                file_path=file_path,
                file_hash=file_hash,
                doc_type=doc_type,
                status="completed",
                status_detail="源同步入库",
                progress=1.0,
                chunk_count=chunks,
                source_uri=normalized_uri,
                source_uri_hash=uri_hash,
                external_id=normalized_external_id,
                logical_folder_path=_optional_identity(logical_folder_path),
                source_type=_optional_identity(source_type),
                source_id=normalized_source_id,
            )
            session.add(document)
            session.flush()
            occupied = session.execute(
                update(Tenant)
                .where(
                    Tenant.id == tenant_id,
                    or_(
                        Tenant.quota_documents <= 0,
                        Tenant.doc_count < Tenant.quota_documents,
                    ),
                )
                .values(doc_count=Tenant.doc_count + 1)
            )
            if occupied.rowcount != 1:
                raise CatalogQuotaError(f"文档配额不足：租户 {tenant_id!r} 已达到文档数上限")
            session.execute(
                update(Dataset)
                .where(Dataset.id == dataset_id, Dataset.tenant_id == tenant_id)
                .values(doc_count=Dataset.doc_count + 1)
            )
            _apply_usage_delta(session, tenant_id, dataset_id, 0, chunks)
            created = True
        else:
            delta = chunks - int(document.chunk_count or 0)
            document.name = name or document.name
            document.status = "completed"
            document.status_detail = "源同步入库"
            document.progress = 1.0
            document.chunk_count = chunks
            if file_hash:
                document.file_hash = file_hash
            if file_path:
                document.file_path = file_path
            if normalized_uri is not None:
                document.source_uri = normalized_uri
                document.source_uri_hash = uri_hash
            if normalized_external_id is not None:
                document.external_id = normalized_external_id
            if logical_folder_path is not None:
                document.logical_folder_path = _optional_identity(logical_folder_path)
            if source_type is not None:
                document.source_type = _optional_identity(source_type)
            if normalized_source_id is not None:
                document.source_id = normalized_source_id
            session.flush()
            _apply_usage_delta(session, document.tenant_id, document.dataset_id, 0, delta)
            created = False
        session.commit()
        return {"id": document.id, "created": created, "chunk_count": chunks}


def recount_usage(tenant_id: str = "") -> list[dict[str, Any]]:
    """从真账（``documents`` 表）重算聚合计数，返回被纠正的偏差。

    Returns:
        每项形如 ``{"scope": "tenant"|"dataset", "id":…, "field":…,
        "before":…, "after":…}``。**空列表 = 账是平的**，这正是它作为对账工具
        的用法：跑一次，输出为空才算过。

    此前全仓没有任何对账入口。聚合计数一旦偏了（而它一定会偏——见模块 docstring
    里那几条），就再也回不来，只能手动改库。
    """
    from sqlalchemy import func, select
    from models.orm import Dataset, Document, Tenant

    drift: list[dict[str, Any]] = []
    with _catalog_write_lock, _session() as s:
        tenants = s.scalars(
            select(Tenant).where(Tenant.id == tenant_id) if tenant_id else select(Tenant)
        ).all()
        for t in tenants:
            rows = s.execute(
                select(
                    Document.dataset_id,
                    func.count(Document.id),
                    func.coalesce(func.sum(Document.chunk_count), 0),
                )
                .where(Document.tenant_id == t.id)
                .group_by(Document.dataset_id)
            ).all()
            by_dataset = {r[0]: (int(r[1] or 0), int(r[2] or 0)) for r in rows}

            for ds in s.scalars(select(Dataset).where(Dataset.tenant_id == t.id)).all():
                docs, chunks = by_dataset.get(ds.id, (0, 0))
                for field, before, after in (
                    ("doc_count", int(ds.doc_count or 0), docs),
                    ("chunk_count", int(ds.chunk_count or 0), chunks),
                ):
                    if before != after:
                        drift.append(
                            {
                                "scope": "dataset",
                                "id": ds.id,
                                "field": field,
                                "before": before,
                                "after": after,
                            }
                        )
                ds.doc_count, ds.chunk_count = docs, chunks

            t_docs = sum(v[0] for v in by_dataset.values())
            t_chunks = sum(v[1] for v in by_dataset.values())
            for field, before, after in (
                ("doc_count", int(t.doc_count or 0), t_docs),
                ("chunk_count", int(t.chunk_count or 0), t_chunks),
            ):
                if before != after:
                    drift.append(
                        {
                            "scope": "tenant",
                            "id": t.id,
                            "field": field,
                            "before": before,
                            "after": after,
                        }
                    )
            t.doc_count, t.chunk_count = t_docs, t_chunks
        s.commit()
    if drift:
        _logger.warning("用量对账纠正了 %d 处偏差：%s", len(drift), drift[:8])
    return drift


def reset_document(doc_id: str, detail: str = "重置（重索引）") -> dict[str, Any]:
    """把 completed / error 状态的文档重置回 waiting（增量重索引专用入口）。

    普通状态机迁移禁止 completed -> 任意阶段；重索引是显式授权流程，
    经此函数重置后即可重新走 waiting -> parsing -> ... -> completed。
    """
    from models.orm import Document

    with _session() as s:
        d = s.get(Document, doc_id)
        if d is None:
            raise KeyError(f"文档不存在: {doc_id}")
        if d.status not in ("completed", "error"):
            raise ValueError(f"仅 completed / error 状态可重置（当前 {d.status}，文档 {doc_id}）")
        d.status = "waiting"
        d.status_detail = detail
        d.progress = 0.0
        d.error_message = ""
        s.commit()
        return {"id": d.id, "status": d.status, "progress": d.progress}


_DOCUMENT_PROCESSING_STATUSES = ("waiting", "parsing", "splitting", "indexing")
_DOCUMENT_CATALOG_MAX_OFFSET = 1_000_000
_DOCUMENT_CATALOG_TAG_FACET_SCAN_LIMIT = 1_000
_DOCUMENT_CURSOR_PREFIX = "dc1_"
_DOCUMENT_CURSOR_VERSION = 1


# These names are public enough for API/diagnostic code to report the hard limits
# without duplicating magic numbers, while keeping the existing module surface small.
DOCUMENT_CATALOG_MAX_OFFSET = _DOCUMENT_CATALOG_MAX_OFFSET
DOCUMENT_TAG_FACET_SCAN_LIMIT = _DOCUMENT_CATALOG_TAG_FACET_SCAN_LIMIT


def _normalized_catalog_value(value: str | None, *, default: str = "all") -> str:
    normalized = str(value or "").strip()
    return normalized if normalized else default


def _coerce_document_parser_meta(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes)):
        import json

        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _document_management_values(
    logical_folder_path: Any,
    parser_meta: Any,
) -> tuple[str, list[str], dict[str, Any]]:
    meta = _coerce_document_parser_meta(parser_meta)
    management = meta.get("management") if isinstance(meta.get("management"), dict) else {}
    return (
        str(logical_folder_path or ""),
        _normalize_document_tags(management.get("tags")),
        meta,
    )


def _document_list_projection(document: Any) -> dict[str, Any]:
    """Project one document consistently for legacy and enterprise listings."""
    management = _document_management_projection(document)
    return {
        "id": document.id,
        "tenant_id": document.tenant_id,
        "dataset_id": document.dataset_id,
        "name": document.name,
        "status": document.status,
        "status_detail": document.status_detail,
        "progress": document.progress,
        "chunk_count": int(document.chunk_count or 0),
        "doc_type": document.doc_type,
        "error_message": document.error_message,
        "logical_folder_path": management["logical_folder_path"],
        "tags": management["tags"],
        "source_uri": document.source_uri,
        "source_type": document.source_type,
        "source_id": document.source_id,
        "external_id": document.external_id,
        "mutation_generation": int(document.mutation_generation or 0),
        "lifecycle_state": document.lifecycle_state,
        "retrieval_enabled": bool(document.retrieval_enabled),
        "active_delete_operation_id": document.active_delete_operation_id,
        "parser_meta": management["parser_meta"],
        "updated_at": document.updated_at.isoformat() if document.updated_at else None,
    }


def _document_list_projection_from_row(row: Any) -> dict[str, Any]:
    """Project a bounded SQL row without constructing a Document ORM instance."""
    value = row.get
    folder, tags, parser_meta = _document_management_values(
        value("logical_folder_path"), value("parser_meta")
    )
    updated_at = value("updated_at")
    return {
        "id": value("id"),
        "tenant_id": value("tenant_id"),
        "dataset_id": value("dataset_id"),
        "name": value("name") or "",
        "status": value("status") or "waiting",
        "status_detail": value("status_detail") or "",
        "progress": float(value("progress") or 0.0),
        "chunk_count": int(value("chunk_count") or 0),
        "doc_type": value("doc_type") or "",
        "error_message": value("error_message") or "",
        "logical_folder_path": folder,
        "tags": tags,
        "source_uri": value("source_uri"),
        "source_type": value("source_type"),
        "source_id": value("source_id"),
        "external_id": value("external_id"),
        "mutation_generation": int(value("mutation_generation") or 0),
        "lifecycle_state": value("lifecycle_state") or "active",
        "retrieval_enabled": bool(
            True if value("retrieval_enabled") is None else value("retrieval_enabled")
        ),
        "active_delete_operation_id": value("active_delete_operation_id"),
        "parser_meta": parser_meta,
        "updated_at": updated_at.isoformat() if updated_at else None,
    }


def _document_catalog_available_columns(session: Any) -> set[str]:
    from sqlalchemy import inspect

    table_name = "documents"
    try:
        return {
            str(column["name"]) for column in inspect(session.get_bind()).get_columns(table_name)
        }
    except Exception as exc:  # noqa: BLE001 - a broken inspector is a capability failure
        raise DocumentCatalogCapabilityError(
            "document catalog schema could not be inspected"
        ) from exc


def _document_catalog_require_columns(
    available: set[str],
    *names: str,
) -> None:
    missing = sorted(set(names).difference(available))
    if missing:
        raise DocumentCatalogCapabilityError(
            "document catalog requires columns: " + ", ".join(missing)
        )


def _document_catalog_select_column(
    table: Any,
    available: set[str],
    name: str,
    default: Any,
) -> Any:
    from sqlalchemy import literal

    if name in available:
        return table.c[name].label(name)
    return literal(default).label(name)


def _document_catalog_select_columns(session: Any, available: set[str]) -> tuple[Any, ...]:
    from models.orm import Document

    table = Document.__table__
    _document_catalog_require_columns(
        available,
        "id",
        "tenant_id",
        "dataset_id",
        "name",
        "status",
        "chunk_count",
        "doc_type",
    )
    return (
        _document_catalog_select_column(table, available, "id", None),
        _document_catalog_select_column(table, available, "tenant_id", ""),
        _document_catalog_select_column(table, available, "dataset_id", ""),
        _document_catalog_select_column(table, available, "name", ""),
        _document_catalog_select_column(table, available, "status", "waiting"),
        _document_catalog_select_column(table, available, "status_detail", ""),
        _document_catalog_select_column(table, available, "progress", 0.0),
        _document_catalog_select_column(table, available, "chunk_count", 0),
        _document_catalog_select_column(table, available, "doc_type", ""),
        _document_catalog_select_column(table, available, "error_message", ""),
        _document_catalog_select_column(table, available, "logical_folder_path", None),
        _document_catalog_select_column(table, available, "source_uri", None),
        _document_catalog_select_column(table, available, "source_type", None),
        _document_catalog_select_column(table, available, "source_id", None),
        _document_catalog_select_column(table, available, "external_id", None),
        _document_catalog_select_column(table, available, "mutation_generation", 0),
        _document_catalog_select_column(table, available, "lifecycle_state", "active"),
        _document_catalog_select_column(table, available, "retrieval_enabled", True),
        _document_catalog_select_column(table, available, "active_delete_operation_id", None),
        _document_catalog_select_column(table, available, "parser_meta", None),
        _document_catalog_select_column(table, available, "created_at", None),
        _document_catalog_select_column(table, available, "updated_at", None),
    )


def _document_catalog_tag_membership_predicate(tag: str, dialect_name: str) -> Any:
    from sqlalchemy import bindparam, text

    if dialect_name == "sqlite":
        return text(
            "EXISTS ("
            "SELECT 1 FROM json_each("
            "COALESCE(json_extract(documents.parser_meta, '$.management.tags'), '[]')"
            ") AS document_tag_values "
            "WHERE document_tag_values.value = :document_catalog_tag"
            ")"
        ).bindparams(bindparam("document_catalog_tag", value=tag))
    if dialect_name in {"mysql", "mariadb"}:
        return text(
            "JSON_CONTAINS("
            "COALESCE(JSON_EXTRACT(documents.parser_meta, '$.management.tags'), JSON_ARRAY()), "
            "JSON_QUOTE(:document_catalog_tag)"
            ") = 1"
        ).bindparams(bindparam("document_catalog_tag", value=tag))
    raise DocumentCatalogCapabilityError(
        "legacy JSON tag authority is supported only on SQLite or MySQL"
    )


def _document_catalog_meta_key_expression(table: Any, key: str) -> Any:
    return table.c.parser_meta[key].as_string()


def _document_catalog_meta_facet_expression(table: Any, available: set[str], key: str) -> Any:
    """parser_meta 上某个字符串键的分组取值：缺列、缺键、空串都归到 ``unknown`` 桶。"""
    from sqlalchemy import func, literal

    if "parser_meta" not in available:
        return literal("unknown")
    raw = _document_catalog_meta_key_expression(table, key)
    return func.coalesce(func.nullif(raw, ""), literal("unknown"))


def _document_catalog_meta_key_criteria(
    table: Any,
    available: set[str],
    *,
    key: str,
    value: str,
    capability: str,
) -> list[Any]:
    """按 parser_meta 的字符串键筛选。

    ``unknown`` 不是一个键值，而是"这列/这键还没写"的桶，所以在没有 parser_meta 的旧库上
    它依然成立（等于不加条件）；筛选具体值时旧库给不出答案，必须显式拒绝而不是静默返回全部。
    """
    from sqlalchemy import or_

    if "parser_meta" not in available:
        if value != "unknown":
            raise DocumentCatalogCapabilityError(capability)
        return []
    expression = _document_catalog_meta_key_expression(table, key)
    if value == "unknown":
        return [
            or_(
                table.c.parser_meta.is_(None),
                expression.is_(None),
                expression == "",
            )
        ]
    return [expression == value]


def _document_catalog_updated_sort_expression(table: Any, available: set[str]) -> Any:
    from sqlalchemy import func

    if "updated_at" in available and "created_at" in available:
        return func.coalesce(table.c.updated_at, table.c.created_at)
    if "updated_at" in available:
        return table.c.updated_at
    if "created_at" in available:
        return table.c.created_at
    raise DocumentCatalogCapabilityError("updated_at_desc requires updated_at or created_at")


def _sort_updated_at(table: Any, available: set[str]) -> Any:
    return _document_catalog_updated_sort_expression(table, available)


def _sort_created_at(table: Any, available: set[str]) -> Any:
    _document_catalog_require_columns(available, "created_at")
    return table.c.created_at


def _sort_name(table: Any, available: set[str]) -> Any:
    from sqlalchemy import func

    _document_catalog_require_columns(available, "name")
    return func.lower(table.c.name)


def _document_catalog_sort_expression(
    table: Any,
    available: set[str],
    sort: str,
) -> Any:
    spec = resolve_document_sort(sort)
    if spec is None:
        raise ValueError("unsupported document sort")
    return spec.expression(table, available)


# 三个内建排序的声明在 _document_cursor_timestamp 之后（那一列就是它的游标取值），
# 见 register_builtin_document_sorts。


def _document_catalog_filter_criteria(
    table: Any,
    available: set[str],
    dialect_name: str,
    tenant_id: str,
    dataset_id: str,
    *,
    query: str,
    status: str,
    doc_type: str,
    engine: str,
    folder: str,
    folder_mode: str,
    tag: str,
    lifecycle_state: str,
    chunking_reason_code: str,
) -> list[Any]:
    from sqlalchemy import func, literal, or_

    _document_catalog_require_columns(available, "tenant_id", "dataset_id", "id")
    criteria: list[Any] = [
        table.c.tenant_id == tenant_id,
        table.c.dataset_id == dataset_id,
    ]
    if query:
        folded = query.casefold()
        searchable = [table.c.name]
        for optional_name in ("source_uri", "external_id"):
            if optional_name in available:
                searchable.append(func.coalesce(table.c[optional_name], literal("")))
        criteria.append(or_(*[field.contains(folded, autoescape=True) for field in searchable]))
    if status != "all":
        _document_catalog_require_columns(available, "status")
        if status == "processing":
            criteria.append(table.c.status.in_(_DOCUMENT_PROCESSING_STATUSES))
        else:
            criteria.append(table.c.status == status)
    if doc_type != "all":
        _document_catalog_require_columns(available, "doc_type")
        criteria.append(table.c.doc_type == doc_type)
    if engine != "all":
        criteria.extend(
            _document_catalog_meta_key_criteria(
                table,
                available,
                key="engine",
                value=engine,
                capability="engine filtering requires parser_meta",
            )
        )
    if chunking_reason_code != "all":
        criteria.extend(
            _document_catalog_meta_key_criteria(
                table,
                available,
                key="chunking_reason_code",
                value=chunking_reason_code,
                capability="chunking_reason_code filtering requires parser_meta",
            )
        )
    if folder != "all":
        _document_catalog_require_columns(available, "logical_folder_path")
        if folder_mode == "subtree":
            criteria.append(
                or_(
                    table.c.logical_folder_path == folder,
                    table.c.logical_folder_path.startswith(f"{folder}/", autoescape=True),
                )
            )
        else:
            criteria.append(table.c.logical_folder_path == folder)
    if lifecycle_state != "all":
        _document_catalog_require_columns(available, "lifecycle_state")
        criteria.append(table.c.lifecycle_state == lifecycle_state)
    if tag != "all":
        _document_catalog_require_columns(available, "parser_meta")
        criteria.append(_document_catalog_tag_membership_predicate(tag, dialect_name))
    return criteria


def _normalise_document_cursor_datetime(value: Any) -> Any:
    from datetime import datetime, timezone

    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("cursor timestamp is invalid") from exc
    if not isinstance(value, datetime):
        raise ValueError("cursor timestamp is invalid")
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _document_cursor_query_hash(
    *,
    tenant_id: str,
    dataset_id: str,
    query: str,
    status: str,
    doc_type: str,
    engine: str,
    folder: str,
    folder_mode: str,
    tag: str,
    lifecycle_state: str,
    chunking_reason_code: str,
    sort: str,
    limit: int,
) -> str:
    import hashlib
    import json

    payload = {
        "tenant": tenant_id,
        "dataset": dataset_id,
        "q": query,
        "status": status,
        "doc_type": doc_type,
        "engine": engine,
        "folder": folder,
        "folder_mode": folder_mode,
        "tag": tag,
        "lifecycle_state": lifecycle_state,
        "chunking_reason_code": chunking_reason_code,
        "sort": sort,
        "limit": limit,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _cursor_capable_refusal(sort: str) -> ValueError:
    """两处"这个排序不能用游标"的同一句话：能用的那一个由表说了算，不写死名字。"""
    capable = cursor_capable_sorts()
    named = capable[0] if len(capable) == 1 else "the declared sort"
    return ValueError(f"cursor is supported only for {named}, not for {sort}")


def _encode_document_cursor(sort: str, timestamp: Any, document_id: Any, query_hash: str) -> str:
    import base64
    import json

    spec = resolve_document_sort(sort)
    if spec is None:
        raise ValueError("unsupported document sort")
    if not spec.keyset_cursor:
        raise _cursor_capable_refusal(sort)
    if timestamp is None or document_id is None:
        raise DocumentCatalogCapabilityError(
            f"{sort} cursor requires a timestamp and document id"
        )
    normalised_timestamp = _normalise_document_cursor_datetime(timestamp)
    payload = {
        "v": _DOCUMENT_CURSOR_VERSION,
        "s": sort,
        "t": normalised_timestamp.isoformat(),
        "i": str(document_id),
        "c": query_hash,
    }
    encoded = (
        base64.urlsafe_b64encode(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )
    return _DOCUMENT_CURSOR_PREFIX + encoded


def _decode_document_cursor(cursor: str) -> dict[str, Any]:
    import base64
    import json

    raw_cursor = str(cursor or "").strip()
    if not raw_cursor.startswith(_DOCUMENT_CURSOR_PREFIX):
        raise ValueError("cursor is invalid")
    encoded = raw_cursor[len(_DOCUMENT_CURSOR_PREFIX) :]
    try:
        padding = "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded + padding).decode("utf-8"))
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("cursor is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("cursor is invalid")
    if payload.get("v") != _DOCUMENT_CURSOR_VERSION:
        raise ValueError("cursor version is unsupported")
    cursor_spec = resolve_document_sort(payload.get("s"))
    if cursor_spec is None or not cursor_spec.keyset_cursor:
        raise ValueError("cursor sort is unsupported")
    document_id = payload.get("i")
    query_hash = payload.get("c")
    if not isinstance(document_id, str) or not document_id:
        raise ValueError("cursor document id is invalid")
    if not isinstance(query_hash, str) or len(query_hash) != 64:
        raise ValueError("cursor query context is invalid")
    return {
        "sort": payload["s"],
        "timestamp": _normalise_document_cursor_datetime(payload.get("t")),
        "document_id": document_id,
        "query_hash": query_hash,
    }


def _document_cursor_timestamp(row: Any) -> Any:
    return row.get("updated_at") or row.get("created_at")


# 三个内建排序各一行声明：名字、方向、表达式，以及"谁能用 keyset 游标 + 游标取哪一列"。
# 声明把 ORDER BY 与游标取值放在同一处书写，但**不保证两者一致**（那是同一个作者写下的两遍）：
# 一致性由实走翻页证明 —— tests/test_document_sort_registry.py 里那条"created 与 updated
# 顺序不一致"的全表走查，会把错配当场照出来。
def register_builtin_document_sorts() -> None:
    """Declare the built-in sorts. Replace rather than refuse, so a second instantiation of
    this module (``importlib.reload`` or a copy-load under another name) re-declares its own
    builtins instead of failing at import."""
    register_document_sort(
        DocumentSortSpec(
            sort="updated_at_desc",
            direction="desc",
            expression=_sort_updated_at,
            keyset_cursor=True,
            cursor_value=_document_cursor_timestamp,
        ),
        replace=True,
    )
    register_document_sort(
        DocumentSortSpec(
            sort="created_at_asc", direction="asc", expression=_sort_created_at
        ),
        replace=True,
    )
    register_document_sort(
        DocumentSortSpec(sort="name_asc", direction="asc", expression=_sort_name),
        replace=True,
    )


register_builtin_document_sorts()


def list_documents_page(
    tenant_id: str,
    dataset_id: str,
    *,
    offset: int = 0,
    limit: int = 20,
    query: str = "",
    status: str = "all",
    doc_type: str = "all",
    engine: str = "all",
    folder: str = "all",
    folder_mode: str = "exact",
    tag: str = "all",
    lifecycle_state: str = "all",
    chunking_reason_code: str = "all",
    sort: str = "updated_at_desc",
    cursor: str | None = None,
    engine_override: Any | None = None,
) -> dict[str, Any]:
    """Return a bounded, tenant-scoped document page using SQL predicates and projections.

    ``offset`` remains supported for compatibility. New callers should prefer the
    opaque ``updated_at_desc`` cursor because it is stable when documents are inserted
    between page requests.
    """
    from sqlalchemy import and_, func, or_, select
    from models.orm import Document

    tenant = str(tenant_id or "").strip()
    dataset = str(dataset_id or "").strip()
    if not tenant or not dataset:
        raise ValueError("tenant_id and dataset_id are required")
    if offset < 0 or offset > _DOCUMENT_CATALOG_MAX_OFFSET:
        raise ValueError("offset must be between 0 and 1000000")
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    if cursor and offset:
        raise ValueError("offset must be zero when cursor is provided")

    normalized_query = str(query or "").strip()
    normalized_status = _normalized_catalog_value(status).casefold()
    normalized_doc_type = _normalized_catalog_value(doc_type).casefold()
    normalized_engine = _normalized_catalog_value(engine).casefold()
    normalized_folder = _normalized_catalog_value(folder)
    if normalized_folder != "all":
        normalized_folder = normalized_folder.strip("/").strip() or "all"
    normalized_folder_mode = _normalized_catalog_value(folder_mode, default="exact").casefold()
    normalized_tag = _normalized_catalog_value(tag)
    normalized_lifecycle = _normalized_catalog_value(lifecycle_state).casefold()
    normalized_chunking_reason_code = _normalized_catalog_value(chunking_reason_code).casefold()
    normalized_sort = _normalized_catalog_value(sort, default="updated_at_desc").casefold()
    if normalized_folder_mode not in {"exact", "subtree"}:
        raise ValueError("folder_mode must be exact or subtree")
    sort_spec = resolve_document_sort(normalized_sort)
    if sort_spec is None:
        raise ValueError("unsupported document sort")
    if cursor and not sort_spec.keyset_cursor:
        raise _cursor_capable_refusal(normalized_sort)

    from sqlalchemy.orm import Session

    session_context = (
        _session() if engine_override is None else Session(engine_override, expire_on_commit=False)
    )
    with session_context as session:
        available = _document_catalog_available_columns(session)
        table = Document.__table__
        dialect_name = session.get_bind().dialect.name
        criteria = _document_catalog_filter_criteria(
            table,
            available,
            dialect_name,
            tenant,
            dataset,
            query=normalized_query,
            status=normalized_status,
            doc_type=normalized_doc_type,
            engine=normalized_engine,
            folder=normalized_folder,
            folder_mode=normalized_folder_mode,
            tag=normalized_tag,
            lifecycle_state=normalized_lifecycle,
            chunking_reason_code=normalized_chunking_reason_code,
        )
        total = int(
            session.execute(
                select(func.count(table.c.id)).select_from(table).where(*criteria)
            ).scalar_one()
            or 0
        )

        sort_expression = _document_catalog_sort_expression(table, available, normalized_sort)
        cursor_query_hash = _document_cursor_query_hash(
            tenant_id=tenant,
            dataset_id=dataset,
            query=normalized_query,
            status=normalized_status,
            doc_type=normalized_doc_type,
            engine=normalized_engine,
            folder=normalized_folder,
            folder_mode=normalized_folder_mode,
            tag=normalized_tag,
            lifecycle_state=normalized_lifecycle,
            chunking_reason_code=normalized_chunking_reason_code,
            sort=normalized_sort,
            limit=limit,
        )
        page_criteria = list(criteria)
        if cursor:
            decoded_cursor = _decode_document_cursor(cursor)
            if decoded_cursor["query_hash"] != cursor_query_hash:
                raise ValueError("cursor does not match query")
            page_criteria.append(
                or_(
                    sort_expression < decoded_cursor["timestamp"],
                    and_(
                        sort_expression == decoded_cursor["timestamp"],
                        table.c.id < decoded_cursor["document_id"],
                    ),
                )
            )

        statement = (
            select(*_document_catalog_select_columns(session, available))
            .select_from(table)
            .where(*page_criteria)
        )
        # 方向来自声明，不是分支顺序：新增加序排序若忘了改这里，旧写法会静默按降序翻页。
        if sort_spec.direction == "asc":
            statement = statement.order_by(sort_expression.asc(), table.c.id.asc())
        else:
            statement = statement.order_by(sort_expression.desc(), table.c.id.desc())
        raw_rows = session.execute(statement.offset(offset).limit(limit + 1)).mappings().all()

    has_next = len(raw_rows) > limit
    rows = raw_rows[:limit]
    next_cursor = None
    if has_next and sort_spec.keyset_cursor and rows:
        next_cursor = _encode_document_cursor(
            normalized_sort,
            # 游标里的值是声明给的取值方式，和 ORDER BY 用的表达式同出一门。
            sort_spec.cursor_value(rows[-1]),  # type: ignore[misc]
            rows[-1].get("id"),
            cursor_query_hash,
        )

    return {
        "items": [_document_list_projection_from_row(row) for row in rows],
        "total": total,
        "offset": offset,
        "limit": limit,
        "next_cursor": next_cursor,
        "tag_authority": "legacy_projection",
    }


def _document_catalog_grouped_counts(
    session: Any,
    table: Any,
    criteria: list[Any],
    expression: Any,
) -> list[dict[str, Any]]:
    from sqlalchemy import func, select

    rows = (
        session.execute(
            select(
                expression.label("value"),
                func.count(table.c.id).label("count"),
            )
            .select_from(table)
            .where(*criteria)
            .group_by(expression)
        )
        .mappings()
        .all()
    )
    return [
        {"value": row.get("value") or "unknown", "count": int(row.get("count") or 0)}
        for row in rows
    ]


def summarize_documents(
    tenant_id: str,
    dataset_id: str,
    *,
    recent_limit: int = 6,
    engine_override: Any | None = None,
) -> dict[str, Any]:
    """Return SQL-aggregated catalog metrics and a bounded recent/tag projection."""
    from datetime import datetime, timezone

    from sqlalchemy import case, func, literal, select
    from models.orm import Document

    tenant = str(tenant_id or "").strip()
    dataset = str(dataset_id or "").strip()
    if not tenant or not dataset:
        raise ValueError("tenant_id and dataset_id are required")
    if recent_limit < 1 or recent_limit > 100:
        raise ValueError("recent_limit must be between 1 and 100")

    from sqlalchemy.orm import Session

    session_context = (
        _session() if engine_override is None else Session(engine_override, expire_on_commit=False)
    )
    with session_context as session:
        available = _document_catalog_available_columns(session)
        table = Document.__table__
        _document_catalog_require_columns(
            available,
            "id",
            "tenant_id",
            "dataset_id",
            "status",
            "chunk_count",
        )
        criteria = [table.c.tenant_id == tenant, table.c.dataset_id == dataset]
        status_expression = table.c.status
        type_expression = (
            func.coalesce(table.c.doc_type, literal("unknown"))
            if "doc_type" in available
            else literal("unknown")
        )
        engine_expression = _document_catalog_meta_facet_expression(table, available, "engine")
        chunking_reason_expression = _document_catalog_meta_facet_expression(
            table, available, "chunking_reason_code"
        )
        folder_expression = (
            func.coalesce(func.nullif(table.c.logical_folder_path, ""), literal("unknown"))
            if "logical_folder_path" in available
            else literal("unknown")
        )
        chunk_expression = table.c.chunk_count
        if "parser_meta" not in available:
            parser_observed_expression = literal(False)
        elif session.get_bind().dialect.name == "sqlite":
            # SQLite exposes JSON1 but not MySQL's JSON_LENGTH. Canonical
            # empty objects/arrays are two bytes ({} / []), so this remains
            # a portable, SQL-only non-empty JSON observation check.
            parser_observed_expression = (
                func.coalesce(func.length(func.json(table.c.parser_meta)), 0) > 2
            )
        elif session.get_bind().dialect.name in {"mysql", "mariadb"}:
            parser_observed_expression = func.coalesce(func.json_length(table.c.parser_meta), 0) > 0
        else:
            parser_observed_expression = literal(False)

        summary_row = (
            session.execute(
                select(
                    func.count(table.c.id).label("total"),
                    func.coalesce(
                        func.sum(case((status_expression == "completed", 1), else_=0)), 0
                    ).label("completed"),
                    func.coalesce(
                        func.sum(
                            case(
                                (status_expression.in_(_DOCUMENT_PROCESSING_STATUSES), 1),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("processing"),
                    func.coalesce(
                        func.sum(case((status_expression == "error", 1), else_=0)), 0
                    ).label("failed"),
                    func.coalesce(func.sum(chunk_expression), 0).label("chunks"),
                    func.coalesce(
                        func.sum(case((parser_observed_expression, 1), else_=0)), 0
                    ).label("parser_observed"),
                )
                .select_from(table)
                .where(*criteria)
            )
            .mappings()
            .one()
        )

        total = int(summary_row.get("total") or 0)
        completed = int(summary_row.get("completed") or 0)
        processing = int(summary_row.get("processing") or 0)
        failed = int(summary_row.get("failed") or 0)
        chunks = int(summary_row.get("chunks") or 0)
        parser_observed = int(summary_row.get("parser_observed") or 0)

        status_expression_for_facet = func.coalesce(status_expression, literal("unknown"))
        status_rows = _document_catalog_grouped_counts(
            session, table, criteria, status_expression_for_facet
        )
        type_rows = _document_catalog_grouped_counts(session, table, criteria, type_expression)
        engine_rows = _document_catalog_grouped_counts(session, table, criteria, engine_expression)
        chunking_reason_rows = _document_catalog_grouped_counts(
            session, table, criteria, chunking_reason_expression
        )

        folder_rows = (
            session.execute(
                select(
                    folder_expression.label("path"),
                    func.count(table.c.id).label("documents"),
                    func.coalesce(func.sum(chunk_expression), 0).label("chunks"),
                )
                .select_from(table)
                .where(*criteria)
                .group_by(folder_expression)
            )
            .mappings()
            .all()
        )

        sort_expression = _document_catalog_sort_expression(table, available, "updated_at_desc")
        recent_statement = (
            select(*_document_catalog_select_columns(session, available))
            .select_from(table)
            .where(*criteria)
            .order_by(sort_expression.desc(), table.c.id.desc())
            .limit(recent_limit)
        )
        recent_rows = session.execute(recent_statement).mappings().all()

        tag_facets_scanned = 0
        tag_facets: dict[str, dict[str, int]] = {}
        if "parser_meta" in available:
            tag_statement = (
                select(
                    table.c.id.label("id"),
                    table.c.chunk_count.label("chunk_count"),
                    table.c.parser_meta.label("parser_meta"),
                )
                .select_from(table)
                .where(*criteria)
                .order_by(table.c.id.asc())
                .limit(_DOCUMENT_CATALOG_TAG_FACET_SCAN_LIMIT)
            )
            tag_rows = session.execute(tag_statement).mappings().all()
            tag_facets_scanned = len(tag_rows)
            for row in tag_rows:
                _, tags, _ = _document_management_values(None, row.get("parser_meta"))
                chunk_count = int(row.get("chunk_count") or 0)
                for tag_name in tags:
                    entry = tag_facets.setdefault(tag_name, {"documents": 0, "chunks": 0})
                    entry["documents"] += 1
                    entry["chunks"] += chunk_count
        tag_facets_complete = (
            "parser_meta" in available and total <= _DOCUMENT_CATALOG_TAG_FACET_SCAN_LIMIT
        )
        tag_facets_truncated = total > _DOCUMENT_CATALOG_TAG_FACET_SCAN_LIMIT

    status_counts = {row["value"]: row["count"] for row in status_rows}
    status_facets: dict[str, int] = {
        "all": total,
        "waiting": int(status_counts.get("waiting", 0)),
        "parsing": int(status_counts.get("parsing", 0)),
        "splitting": int(status_counts.get("splitting", 0)),
        "indexing": int(status_counts.get("indexing", 0)),
        "processing": processing,
        "completed": completed,
        "error": failed,
    }
    for value, count in sorted(status_counts.items()):
        if value not in status_facets:
            status_facets[value] = count

    def sort_facets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(rows, key=lambda item: (-int(item["count"]), str(item["value"])))

    folders = [
        {
            "path": row.get("path") or "unknown",
            "documents": int(row.get("documents") or 0),
            "chunks": int(row.get("chunks") or 0),
        }
        for row in folder_rows
    ]
    folders.sort(key=lambda item: (-item["documents"], item["path"]))
    tags = [
        {"name": name, **stats}
        for name, stats in sorted(
            tag_facets.items(), key=lambda item: (-item[1]["documents"], item[0])
        )
    ]
    coverage = int(round((parser_observed * 100) / total)) if total else 0
    return {
        "dataset_id": dataset,
        "summary": {
            "total": total,
            "completed": completed,
            "processing": processing,
            "failed": failed,
            "chunks": chunks,
            "parser_observed": parser_observed,
            "parser_coverage": coverage,
        },
        "facets": {
            "statuses": status_facets,
            "types": sort_facets(type_rows),
            "engines": sort_facets(engine_rows),
            "chunking_reasons": sort_facets(chunking_reason_rows),
            "folders": folders,
            "tags": tags,
        },
        "recent": [_document_list_projection_from_row(row) for row in recent_rows],
        "tag_authority": "legacy_projection",
        "tag_facets_complete": tag_facets_complete,
        "tag_facets_scan_limit": _DOCUMENT_CATALOG_TAG_FACET_SCAN_LIMIT,
        "tag_facets_scanned": tag_facets_scanned,
        "tag_facets_truncated": tag_facets_truncated,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def get_document(doc_id: str) -> dict[str, Any] | None:
    """读取文档状态（含入库元信息）。

    Note:
        不再统计 ``segments``：``document_segments`` 表在生产入库路径上
        从未被写入过（``upsert_segment`` 只有测试脚本在调），却让每次
        轮询都触发一次关联表懒加载查询。真正有价值的细粒度信息现在放在
        ``parser_meta`` 里。
    """
    from models.orm import Document

    with _session() as s:
        d = s.get(Document, doc_id)
        if d is None:
            return None
        return {
            "id": d.id,
            "tenant_id": d.tenant_id,
            "dataset_id": d.dataset_id,
            "name": d.name,
            "status": d.status,
            "status_detail": d.status_detail,
            "progress": d.progress,
            "chunk_count": d.chunk_count,
            "error_message": d.error_message,
            "file_path": d.file_path,
            "file_hash": d.file_hash,
            "source_uri": d.source_uri,
            "external_id": d.external_id,
            "logical_folder_path": d.logical_folder_path,
            "tags": _document_management_projection(d)["tags"],
            "source_type": d.source_type,
            "source_id": d.source_id,
            "doc_type": d.doc_type,
            "parser_meta": d.parser_meta or {},
            "content_revision": d.content_revision,
            "desired_index_revision": d.desired_index_revision,
            "indexed_revision": d.indexed_revision,
            "graph_revision": d.graph_revision,
            "current_attempt_id": d.current_attempt_id,
            "mutation_generation": int(d.mutation_generation or 0),
            "lifecycle_state": d.lifecycle_state,
            "retrieval_enabled": bool(d.retrieval_enabled),
            "active_delete_operation_id": d.active_delete_operation_id,
            "updated_at": d.updated_at.isoformat() if d.updated_at else None,
        }


def list_documents(dataset_id: str) -> list[dict[str, Any]]:
    """列出知识库下的文档（保留旧响应合同）。

    带上 ``status_detail``：入库进度的子阶段信息（如「构建知识图谱
    37/200」）都在这个字段里，前端轮询的是列表接口，不带它就只能看到
    一个百分比，看不出当前卡在哪一步。
    """
    from sqlalchemy import select

    from models.orm import Document

    with _session() as session:
        rows = session.scalars(
            select(Document).where(Document.dataset_id == dataset_id).order_by(Document.created_at)
        ).all()
        return [_document_list_projection(document) for document in rows]


def find_document_by_source(
    dataset_id: str,
    *,
    source_uri: str | None = None,
    source_id: str | None = None,
    external_id: str | None = None,
) -> dict[str, Any] | None:
    """按稳定来源身份查找文档，不依赖本机缓存路径。"""
    from sqlalchemy import select
    from models.orm import Document

    normalized_uri = _optional_identity(source_uri)
    normalized_source_id = _optional_identity(source_id)
    normalized_external_id = _optional_identity(external_id)
    with _session() as session:
        document = None
        if normalized_uri is not None:
            document = session.scalar(
                select(Document).where(
                    Document.dataset_id == dataset_id,
                    Document.source_uri_hash == _source_uri_hash(normalized_uri),
                )
            )
            if document is not None and document.source_uri != normalized_uri:
                raise ValueError("source URI hash collision")
        if document is None and normalized_source_id and normalized_external_id:
            document = session.scalar(
                select(Document).where(
                    Document.dataset_id == dataset_id,
                    Document.source_id == normalized_source_id,
                    Document.external_id == normalized_external_id,
                )
            )
        if document is None:
            return None
        return {
            "id": document.id,
            "status": document.status,
            "file_path": document.file_path,
            "source_uri": document.source_uri,
            "external_id": document.external_id,
        }


def find_document_by_path(dataset_id: str, file_path: str) -> dict[str, Any] | None:
    """按知识库 + 文件路径查文档（幂等入库去重用）。"""
    from sqlalchemy import select

    from models.orm import Document

    with _session() as s:
        d = s.scalar(
            select(Document).where(
                Document.dataset_id == dataset_id,
                Document.file_path == file_path,
            )
        )
        if d is None:
            return None
        return {"id": d.id, "status": d.status, "file_path": d.file_path}


def upsert_segment(
    document_id: str,
    seq: int,
    status: str,
    progress: float = 0.0,
    chunk_count: int = 0,
    segment_id: str | None = None,
) -> str:
    """登记 / 更新分段进度（按 document_id + seq 幂等）。"""
    from sqlalchemy import select

    from models.orm import DocumentSegment

    with _session() as s:
        seg = s.scalar(
            select(DocumentSegment).where(
                DocumentSegment.document_id == document_id,
                DocumentSegment.seq == seq,
            )
        )
        if seg is None:
            seg = DocumentSegment(
                id=segment_id or new_id("seg"),
                document_id=document_id,
                seq=seq,
            )
            s.add(seg)
        seg.status = status
        seg.progress = max(0.0, min(1.0, float(progress)))
        seg.chunk_count = chunk_count
        s.commit()
        return seg.id


# ---------------------------------------------------------------------------
# 元数据 schema 注册表
# ---------------------------------------------------------------------------


def declare_metadata_field(
    dataset_id: str,
    key: str,
    value_type: str = "string",
    source: str = "manual",
    label: str = "",
) -> dict[str, Any]:
    """声明（或更新）一条元数据字段 schema（幂等）。"""
    from sqlalchemy import select

    from models.orm import MetadataField

    if value_type not in ("string", "number", "bool", "date"):
        raise ValueError(f"非法 value_type: {value_type}（string/number/bool/date）")
    with _session() as s:
        f = s.scalar(
            select(MetadataField).where(
                MetadataField.dataset_id == dataset_id,
                MetadataField.key == key,
            )
        )
        if f is None:
            f = MetadataField(dataset_id=dataset_id, key=key)
            s.add(f)
        f.value_type = value_type
        f.source = source
        f.label = label or key
        s.commit()
        return {"dataset_id": dataset_id, "key": key, "value_type": value_type, "source": source}


def list_metadata_fields(dataset_id: str) -> list[dict[str, Any]]:
    """列出知识库的元数据 schema。"""
    from sqlalchemy import select

    from models.orm import MetadataField

    with _session() as s:
        rows = s.scalars(select(MetadataField).where(MetadataField.dataset_id == dataset_id)).all()
        return [
            {"key": f.key, "value_type": f.value_type, "source": f.source, "label": f.label}
            for f in rows
        ]


__all__ = [
    "CatalogQuotaError",
    "get_engine",
    "reset_engine",
    "new_id",
    "ensure_tenant",
    "ensure_dataset",
    "list_datasets",
    "check_quota",
    "bump_counts",
    "recount_usage",
    "create_document",
    "register_synced_document",
    "set_document_status",
    "update_document_management",
    "remove_document",
    "purge_dataset",
    "reset_document",
    "get_document",
    "list_documents",
    "find_document_by_source",
    "find_document_by_path",
    "upsert_segment",
    "declare_metadata_field",
    "list_metadata_fields",
]
