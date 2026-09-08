"""阶段 7：用量记账——让配额真的拦得住。

## 这些测试在防什么

用量记账的失效方式和阶段 6 的图检索是同一类：**没有任何一步会报错**。文档照
样入库，界面照样显示用量，配额照样能配。只是配额永远拦不住，因为它读的那个数
字压根不涨；或者反过来，删掉的东西不退还，数字只涨不落，最后把配额烧穿——库里
空空如也，界面上写着"已用满"。

这里的根本问题不是"某个调用点漏了"，而是**记账被设计成了调用方的义务**：
``bump_counts`` 是个公开函数，谁写入谁记得调。状态机记得，源同步不记得，
``--reset`` 不记得，删除路径压根没这个概念。一个靠自觉维持的不变量，等于没有
不变量。

所以下面的断言分两层：

1. **不变量层**：聚合计数必须等于 ``documents`` 表里的真账（这是
   ``recount_usage`` 返回空列表的含义）。不管走哪条路径，走完之后账都得平。
2. **路径层**：逐条压那些历史上不记账的路径——源同步入库、源同步清理、
   ``--reset`` 清库、空文档、替换式重入库。

第 2 层单独存在是因为第 1 层的"平"有个廉价的假象：如果某条路径既不写
``documents`` 也不写聚合，账面上依然是平的——两边一起漏，差值仍是 0。所以必须
另有断言盯住"这篇文档到底有没有被记进真账"。
"""
from __future__ import annotations

import logging
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest


@contextmanager
def capture_warning_logger(caplog, name: str):
    logger = logging.getLogger(name)
    previous = (logger.level, logger.disabled, logger.propagate)
    logger.disabled = False
    logger.propagate = True
    try:
        with caplog.at_level("WARNING", logger=name):
            yield
    finally:
        logger.setLevel(previous[0])
        logger.disabled = previous[1]
        logger.propagate = previous[2]


@pytest.fixture()
def cat(monkeypatch):
    """每个用例一套独立的 SQLite catalog，跑完即弃。

    ``get_settings`` 带 ``lru_cache(maxsize=1)``，而 ``catalog._resolve_db_url``
    是从它里面读库地址的——只 setenv 不清缓存，环境变量对第二个用例起就完全
    不起作用了：所有用例共用第一个用例的库，第二次插 ``d1`` 直接撞主键。更糟
    的是，如果第一次解析发生在 setenv 之前，那个被固化下来的地址就是 ``.env``
    里的真实 MySQL——测试数据会写进生产目录库。所以顺序是：先 setenv，再清
    settings 缓存，最后 reset_engine。
    """
    tmp = tempfile.mkdtemp(prefix="rag4c-phase7-")
    monkeypatch.setenv("RAG4C_CATALOG_DB_PATH", str(Path(tmp) / "t.db"))
    # 隔离：.env 里配的是真实 MySQL，漏进来会把测试写进生产目录库。
    monkeypatch.setenv("RAG4C_CATALOG_DB_URL", "")
    monkeypatch.setenv("RAG4C_CATALOG_SCHEMA_MODE", "legacy")
    from config.settings import get_settings

    get_settings.cache_clear()
    from core import catalog

    catalog.reset_engine()

    # 隔离没生效的话，后面每一条断言都在读别的用例（甚至生产库）的数字，
    # 红绿都不作数。所以先当场证明这库是空的。
    url, _ = catalog._resolve_db_url()  # noqa: SLF001
    assert url.startswith("sqlite:///"), f"隔离失效，连到了 {url.split('://')[0]}"
    assert catalog.list_documents("ds1") == [], "临时库不是空的，用例之间在互相污染"

    catalog.ensure_tenant("t1", "测试租户")
    catalog.ensure_dataset("t1", "ds1", "测试库")
    yield catalog
    catalog.reset_engine()
    get_settings.cache_clear()


def _usage(catalog, tenant="t1", dataset="ds1") -> tuple[int, int, int, int]:
    """返回 (租户文档数, 租户 chunk 数, 库文档数, 库 chunk 数)。"""
    from models.orm import Dataset, Tenant

    with catalog._session() as s:  # noqa: SLF001
        t = s.get(Tenant, tenant)
        d = s.get(Dataset, dataset)
        return (int(t.doc_count), int(t.chunk_count),
                int(d.doc_count), int(d.chunk_count))


def _ingest(catalog, doc_id: str, chunks: int) -> None:
    """走状态机那条路把一篇文档记成 completed。"""
    catalog.create_document("t1", "ds1", doc_id, doc_id=doc_id)
    for st in ("parsing", "splitting", "indexing"):
        catalog.set_document_status(doc_id, st)
    catalog.set_document_status(doc_id, "completed", chunk_count=chunks)


# --------------------------------------------------------------------------- #
# 1. 不变量：走完任何一条路径，账都得平
# --------------------------------------------------------------------------- #
def test_a_completed_ingest_lands_in_both_books(cat) -> None:
    _ingest(cat, "d1", 12)
    assert _usage(cat) == (1, 12, 1, 12)
    assert cat.recount_usage("t1") == [], "入库之后账就不平了"


def test_recount_is_the_definition_of_balanced(cat) -> None:
    """``recount_usage`` 返回空列表 = 账是平的。这条测的是这个工具本身可信：
    人为把聚合改错，它必须报出来并纠正；不然它只是个会返回 [] 的摆设。"""
    _ingest(cat, "d1", 10)
    cat.bump_counts("t1", "ds1", 0, 500)      # 人为制造 +500 的虚高
    assert _usage(cat)[1] == 510

    drift = cat.recount_usage("t1")
    assert drift, "账已经偏了 500，对账却说没问题"
    assert _usage(cat) == (1, 10, 1, 10)
    assert cat.recount_usage("t1") == [], "纠正之后应当彻底平了"


# --------------------------------------------------------------------------- #
# 2. 记账跟着事实走同一个事务
# --------------------------------------------------------------------------- #
def test_chunk_delta_is_computed_from_the_live_row_not_a_snapshot(cat) -> None:
    """差量必须在事务内用当前行算。

    ``indexing/reindex.py`` 从前是这么写的：函数开头取一份文档快照，末尾用
    ``总数 - 快照.chunk_count`` 调 bump_counts——而上一行 set_document_status
    刚把库里的值改成了总数。快照与库不同步时（并发重建、或中途另有写入），
    这个差就是错的，且错得无声无息。
    """
    _ingest(cat, "d1", 10)
    # 模拟"快照之后、记账之前，有别的写入把 chunk 数改了"
    cat.set_document_status("d1", "completed", chunk_count=30)
    assert _usage(cat) == (1, 30, 1, 30), "差量没有按当前行算"
    assert cat.recount_usage("t1") == []


def test_shrinking_to_zero_refunds_the_old_chunks(cat) -> None:
    """状态机的"空文档"早退分支写 ``chunk_count=0``。

    从前它不 bump——于是一篇本来 200 个 chunk 的文档被改空之后，那 200 个永远
    挂在租户头上。现在它和别的写入走同一条路，差量自然是 -200。
    """
    _ingest(cat, "d1", 200)
    cat.set_document_status("d1", "completed", chunk_count=0)
    assert _usage(cat) == (1, 0, 1, 0)
    assert cat.recount_usage("t1") == []


# --------------------------------------------------------------------------- #
# 3. 删除必须退还
# --------------------------------------------------------------------------- #
def test_removing_a_document_refunds_its_quota(cat) -> None:
    _ingest(cat, "d1", 40)
    _ingest(cat, "d2", 60)
    assert _usage(cat) == (2, 100, 2, 100)

    info = cat.remove_document("d1")
    assert info["chunk_count"] == 40
    assert _usage(cat) == (1, 60, 1, 60), "删除没有退还用量"
    assert cat.recount_usage("t1") == []


def test_removing_a_missing_document_is_a_no_op(cat) -> None:
    """幂等：重复删除、或删一篇从未登记过的文档，都不该报错、更不该扣错账。"""
    _ingest(cat, "d1", 40)
    assert cat.remove_document("不存在的文档") is None
    assert cat.remove_document("d1")["chunk_count"] == 40
    assert cat.remove_document("d1") is None, "重复删除不该再扣一次"
    assert _usage(cat) == (0, 0, 0, 0)


def test_purging_a_dataset_zeroes_it(cat) -> None:
    """``ingest_source.py --reset`` 把整个 dataset 从 Milvus 抹掉，此前完全不
    碰关系库——清完之后用量还是清空前的数字，等于用一条运维命令把这块配额永久
    烧掉。"""
    _ingest(cat, "d1", 40)
    _ingest(cat, "d2", 60)
    purged = cat.purge_dataset("t1", "ds1")
    assert purged == {"removed_documents": 2, "removed_chunks": 100}
    assert _usage(cat) == (0, 0, 0, 0)
    assert cat.list_documents("ds1") == []


# --------------------------------------------------------------------------- #
# 4. 源同步：历史上完全不记账的那条路
# --------------------------------------------------------------------------- #
def test_a_synced_document_is_registered_in_the_real_book(cat) -> None:
    """光看聚合计数平不平是不够的：源同步从前既不写 documents 也不写聚合，
    两边一起漏，差值照样是 0，账面上"平得很"。所以这里盯的是真账里到底有没有
    这一行。"""
    cat.register_synced_document(
        tenant_id="t1", dataset_id="ds1", doc_id="s1",
        name="手册/报销.md", chunk_count=25,
    )
    docs = cat.list_documents("ds1")
    assert [d["id"] for d in docs] == ["s1"], "源同步的文档没进真账"
    assert docs[0]["status"] == "completed"
    assert _usage(cat) == (1, 25, 1, 25)
    assert cat.recount_usage("t1") == []


def test_resyncing_the_same_document_does_not_double_count(cat) -> None:
    """同一篇文档内容变了重新同步：占用的名额还是一个，chunk 按差量走。"""
    cat.register_synced_document(
        tenant_id="t1", dataset_id="ds1", doc_id="s1", name="a.md", chunk_count=25
    )
    r = cat.register_synced_document(
        tenant_id="t1", dataset_id="ds1", doc_id="s1", name="a.md", chunk_count=30
    )
    assert r["created"] is False
    assert _usage(cat) == (1, 30, 1, 30), "重新同步把文档数重复计了"
    assert cat.recount_usage("t1") == []


def test_synced_documents_are_subject_to_the_document_quota(cat) -> None:
    """这是整件事的要害：源同步这条路上，配额从前**从来没生效过**——它读的
    doc_count 永远是 0，因为没有任何一条源同步路径写过它。想灌多少灌多少。"""
    from sqlalchemy import update
    from models.orm import Tenant

    with cat._session() as s:  # noqa: SLF001
        s.execute(update(Tenant).where(Tenant.id == "t1").values(quota_documents=2))
        s.commit()

    for i in range(2):
        cat.register_synced_document(
            tenant_id="t1", dataset_id="ds1", doc_id=f"s{i}", name=f"{i}.md", chunk_count=1
        )
    with pytest.raises(cat.CatalogQuotaError):
        cat.register_synced_document(
            tenant_id="t1", dataset_id="ds1", doc_id="s9", name="9.md", chunk_count=1
        )
    assert _usage(cat)[0] == 2, "被配额拒掉的文档不该留下计数"
    assert cat.recount_usage("t1") == [], "配额拒绝之后账不该偏"


# --------------------------------------------------------------------------- #
# 5. bump_counts 这个低层原语的两个哑掉的信号
# --------------------------------------------------------------------------- #
def test_a_bump_against_an_unknown_tenant_is_logged(cat, caplog) -> None:
    """记账写去了不存在的租户 = 这笔账彻底丢了。此前静默返回，于是"配额永远
    是 0"和"租户 id 拼错了"长得一模一样。"""
    with capture_warning_logger(caplog, "rag4c.core.catalog"):
        cat.bump_counts("查无此租户", "ds1", 1, 10)
    assert any("未命中租户" in r.getMessage() for r in caplog.records), "账丢了却没有任何信号"


def test_clamping_to_zero_is_logged_because_it_means_a_path_forgot(cat, caplog) -> None:
    """下限钳 0 本身是对的（计数不该是负数），但"钳到了"是**欠账的现场**：
    说明有人删掉的比记上的多，也就是某条入库路径没记账。此前这个钳是静默的，
    于是唯一能证明记账漏了的信号被当场吞掉。"""
    _ingest(cat, "d1", 10)
    with capture_warning_logger(caplog, "rag4c.core.catalog"):
        cat.bump_counts("t1", "ds1", 0, -50)   # 只记了 10，却退 50
    assert _usage(cat)[1] == 0, "计数不该变成负数"
    assert any("钳到 0" in r.getMessage() for r in caplog.records), "欠账被静默吞掉了"


def test_a_dataset_bump_does_not_cross_tenants(cat) -> None:
    """``bump_counts`` 更新 datasets 时从前不带 tenant_id 条件。

    最初这条测试想构造「两个租户各有一个同名 ds1」——那个前提是错的：
    ``dataset_id`` 是全局主键，``ensure_dataset`` 会当场拒绝跨租户复用
    （"知识库 'ds1' 属于租户 't1'"）。所以重名这条路本来就走不通。

    真正走得通、也真正发生过的，是**调用方传了一对不匹配的 (租户, 库)**：
    dataset_id 由调用方给，没人校验它归不归这个租户。少了 tenant_id 条件时，
    ``bump_counts('t2', 'ds1', …)`` 会老老实实把 chunk 记到 t1 的 ds1 上——
    t2 的租户计数涨了，t1 的库计数也跟着涨，两本账同时错，而且错得对不上。
    """
    cat.ensure_tenant("t2", "另一个租户")
    cat.ensure_dataset("t2", "ds2", "t2 自己的库")
    before = _usage(cat)

    cat.bump_counts("t2", "ds1", 1, 100)   # ds1 是 t1 的，不该被 t2 的记账碰到

    assert _usage(cat) == before, "别的租户的记账落到了 t1 的库上"


# --------------------------------------------------------------------------- #
# 5b. 源同步器**确实调了**登记——而不只是 catalog 里躺着一个能登记的函数
# --------------------------------------------------------------------------- #
class _FakePipeline:
    """只实现 SourceSyncer 用到的三个方法。"""

    def __init__(self, chunk_count: int = 25) -> None:
        self.chunk_count = chunk_count
        self.deleted: list[tuple[str, bool]] = []

    def ensure_collection(self) -> None:
        pass

    def delete_document(self, doc_id: str, unregister: bool = True) -> int:
        self.deleted.append((doc_id, unregister))
        return 0

    def add_file(self, path, **kwargs):
        class _R:
            chunk_count = self.chunk_count

        return _R()


def _fetched(tmp_path, rel_path="手册/报销.md"):
    from sources.base import FetchedDocument

    local = tmp_path / "f.md"
    local.write_text("正文", encoding="utf-8")
    return FetchedDocument(
        uri=f"github://demo/{rel_path}", rel_path=rel_path,
        local_path=local, content_hash="h1",
    )


def _syncer(pipeline, tmp_path):
    from sources.runner import SourceSyncer

    return SourceSyncer(pipeline, tmp_path / "cache", None)


def _spec(tenant="t1"):
    from sources.runner import SourceSpec

    return SourceSpec(name="demo", type="local_dir", dataset_id="ds1", tenant_id=tenant)


def test_source_sync_actually_registers_the_document(cat, tmp_path) -> None:
    """这一条盯的是**调用点**，不是能力。

    上面那几条源同步用例直接调 ``catalog.register_synced_document``，它们全绿
    也证明不了源同步这条路会记账——``bump_counts`` 当年就是这样：函数好好地
    躺在 catalog 里，源同步压根没调它。所以必须从 ``_ingest_one`` 进去，走真
    正的生产调用链。
    """
    pipe = _FakePipeline(chunk_count=25)
    count = _syncer(pipe, tmp_path)._ingest_one(  # noqa: SLF001
        _spec(), _fetched(tmp_path), "s1", replace=False
    )
    assert count == 25
    assert [d["id"] for d in cat.list_documents("ds1")] == ["s1"], "源同步没有登记进关系库"
    assert _usage(cat) == (1, 25, 1, 25)
    assert cat.recount_usage("t1") == []


def test_source_sync_replace_keeps_the_quota_slot(cat, tmp_path) -> None:
    """替换路径必须传 ``unregister=False``，否则先退名额再重占，中间被别人挤
    掉就是一次「更新失败在配额上」的怪事。"""
    pipe = _FakePipeline(chunk_count=30)
    _syncer(pipe, tmp_path)._ingest_one(  # noqa: SLF001
        _spec(), _fetched(tmp_path), "s1", replace=True
    )
    assert pipe.deleted == [], "替换路径不应绕过 durable generation fence 同步删除"


def test_source_sync_without_a_tenant_touches_no_books(cat, tmp_path) -> None:
    """没配 tenant_id 就没有配额边界，也没有租户行可挂账——此时不该硬塞一行
    进关系库，更不该让入库因为记账失败而失败。"""
    pipe = _FakePipeline(chunk_count=9)
    assert _syncer(pipe, tmp_path)._ingest_one(  # noqa: SLF001
        _spec(tenant=""), _fetched(tmp_path), "s1", replace=False
    ) == 9
    assert cat.list_documents("ds1") == []
    assert _usage(cat) == (0, 0, 0, 0)


def test_a_failing_registration_never_fails_the_ingest(cat, tmp_path, caplog) -> None:
    """chunk 已经写进 Milvus 了，此时因为记账抛错，只会让上层把这篇记成
    failed 并在下一轮重灌一遍——内容重复，账还是不平。"""
    import sources.runner as runner_mod
    from core import catalog as catalog_mod

    def boom(**_kw):
        raise RuntimeError("桩：目录库不可用")

    original = catalog_mod.register_synced_document
    catalog_mod.register_synced_document = boom
    try:
        with capture_warning_logger(caplog, "rag4c.sources.runner"):
            count = _syncer(_FakePipeline(7), tmp_path)._ingest_one(  # noqa: SLF001
                _spec(), _fetched(tmp_path), "s1", replace=False
            )
    finally:
        catalog_mod.register_synced_document = original
    assert count == 7, "记账失败把入库一起拖垮了"
    assert any("登记到关系库失败" in r.getMessage() for r in caplog.records), \
        "记账丢了却没有任何信号"
    assert runner_mod is not None


# --------------------------------------------------------------------------- #
# 6. 删除的收口：pipeline.delete_document
# --------------------------------------------------------------------------- #
class _FakeMilvus:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete_by_doc_id(self, doc_id: str) -> int:
        self.deleted.append(doc_id)
        return 7


def _pipeline(milvus):
    """只造出能调 delete_document 的最小 IngestPipeline。"""
    from indexing.ingest import IngestPipeline

    p = object.__new__(IngestPipeline)
    p.milvus = milvus
    return p


def test_pipeline_delete_unregisters_by_default(cat) -> None:
    """删除的唯一收口在 pipeline 里，退还也就放在这里——放到各调用方去，就又变
    成"谁记得谁调"，而源同步正是那个不记得的。"""
    _ingest(cat, "d1", 40)
    milvus = _FakeMilvus()
    assert _pipeline(milvus).delete_document("d1") == 7
    assert milvus.deleted == ["d1"]
    assert _usage(cat) == (0, 0, 0, 0), "删了 chunk 却没退用量"


def test_pipeline_delete_can_keep_the_registration(cat) -> None:
    """替换式重新入库（先删后写同一个 doc_id）必须传 unregister=False：文档还
    在，只是内容换了。注销掉会把配额名额一起退，紧接着重新登记时要重占一次，
    中间挤进别的写入就可能占不回来——一次普通更新会莫名其妙失败在配额上。"""
    _ingest(cat, "d1", 40)
    milvus = _FakeMilvus()
    _pipeline(milvus).delete_document("d1", unregister=False)
    assert milvus.deleted == ["d1"]
    assert _usage(cat) == (1, 40, 1, 40), "替换路径不该退还名额"


def test_a_failing_catalog_never_blocks_the_delete(cat, monkeypatch, caplog) -> None:
    """记账后端抖动不该让删除本身失败：chunk 已经删掉了，抛错只会让调用方以为
    没删而重试。"""
    _ingest(cat, "d1", 40)

    def boom(_doc_id):
        raise RuntimeError("桩：目录库不可用")

    monkeypatch.setattr(cat, "remove_document", boom)
    milvus = _FakeMilvus()
    with capture_warning_logger(caplog, "rag4c.ingest"):
        assert _pipeline(milvus).delete_document("d1") == 7
    assert milvus.deleted == ["d1"]
    assert any("用量退还失败" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------- #
# 7. check_quota 的文档分支：删掉而不是补调用点
# --------------------------------------------------------------------------- #
def test_check_quota_no_longer_offers_a_racy_document_gate() -> None:
    """评审计划把 ``check_quota(add_documents=…)`` 记成"缺一个生产调用点"。
    那个判断是错的：文档数配额早已由 ``create_document`` 的条件 UPDATE 原子占
    用，那是严格更强的机制——先读后写在多进程并发下拦不住（两个进程同时读到
    "还差一篇"然后各写各的），``WHERE doc_count < quota_documents`` 的 rowcount
    判定不会。留着一个更弱的同名闸门，只会等着被误用。
    """
    import inspect

    from core import catalog

    params = inspect.signature(catalog.check_quota).parameters
    assert "add_documents" not in params, "更弱的文档闸门又回来了"
    assert "add_chunks" in params
