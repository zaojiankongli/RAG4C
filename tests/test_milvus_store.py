"""Milvus 向量库封装的测试（默认全部离线，集成用例需显式开关）。

离线部分钉住"不需要服务端也能验证"的四件事：环境变量怎么读、Schema/索引
怎么定义、检索参数怎么兜底、异常怎么说人话。
集成部分只有在 ``RAG4C_MILVUS_RUN_INTEGRATION=1`` 且配了
``RAG4C_MILVUS_URI`` 时才跑——虚拟机关了也不该让 CI 变红。
"""
from __future__ import annotations

import os

import pytest

from eval.retrieval_eval.milvus_store import (
    MilvusStoreConfig,
    MilvusStoreError,
    MilvusVectorStore,
    build_index_params,
    build_schema,
    build_search_params,
    _batched,
    _normalize,
)

_INTEGRATION = os.environ.get("RAG4C_MILVUS_RUN_INTEGRATION") == "1"
_INTEGRATION_URI = os.environ.get("RAG4C_MILVUS_URI", "")


def _env(monkeypatch, **kv):
    for key in ("URI", "TOKEN", "DB", "HOST", "PORT", "COLLECTION", "DIM", "INDEX_TYPE",
                "METRIC_TYPE", "M", "EF_CONSTRUCTION", "NLIST", "EF", "NPROBE",
                "CONSISTENCY", "BATCH_SIZE", "TIMEOUT", "ENABLE_BM25", "BM25_ANALYZER",
                "TEXT_MAX_LENGTH", "DB_NAME"):
        monkeypatch.delenv(f"RAG4C_MILVUS_{key}", raising=False)
        monkeypatch.delenv(f"MILVUS_{key}", raising=False)
    for key, value in kv.items():
        monkeypatch.setenv(f"RAG4C_MILVUS_{key}", str(value))


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


def test_defaults_when_nothing_is_configured(monkeypatch):
    """环境变量都没配时的兜底顺序：先退回项目配置，再退回本机默认端口。"""
    # 顺序要紧：settings 首次加载会把 .env 里的键写回 os.environ，
    # 所以必须先碰 settings、再清环境变量，否则清了个寂寞。
    from config.settings import get_settings

    monkeypatch.setattr(get_settings().milvus, "uri", "")
    _env(monkeypatch)
    cfg = MilvusStoreConfig.from_env()
    assert cfg.uri == "http://127.0.0.1:19530"
    assert cfg.token == ""
    assert cfg.db_name == ""  # 留空 = 用服务端默认库，不调 use_database
    assert cfg.collection == "rag4c_eval_chunks"  # 绝不默认指向生产集合
    assert cfg.dim == 1024
    assert cfg.index_type == "HNSW"
    assert cfg.metric_type == "COSINE"


def test_uri_from_env_and_host_port_fallback(monkeypatch):
    _env(monkeypatch, URI="http://192.168.100.128:19530")
    assert MilvusStoreConfig.from_env().uri == "http://192.168.100.128:19530"

    _env(monkeypatch, HOST="10.0.0.5", PORT=20000)
    assert MilvusStoreConfig.from_env().uri == "http://10.0.0.5:20000"


def test_generic_milvus_prefix_is_accepted(monkeypatch):
    """没有 RAG4C_ 前缀时退回通用 MILVUS_*，方便在别的部署里直接用。"""
    _env(monkeypatch)
    monkeypatch.setenv("MILVUS_URI", "http://milvus.internal:19530")
    assert MilvusStoreConfig.from_env().uri == "http://milvus.internal:19530"


def test_bad_int_falls_back_to_default_instead_of_crashing(monkeypatch):
    _env(monkeypatch, DIM="not-a-number", BATCH_SIZE="0")
    cfg = MilvusStoreConfig.from_env()
    assert cfg.dim == 1024
    assert cfg.batch_size == 0  # 0 是合法整数（调用方按 1 兜底），不擅自改语义


def test_token_is_masked_in_snapshot(monkeypatch):
    _env(monkeypatch, TOKEN="root:Milvus")
    snapshot = MilvusStoreConfig.from_env().as_dict()
    assert snapshot["token"] == "***"


# ---------------------------------------------------------------------------
# Schema / 索引 / 检索参数（不需要连服务端）
# ---------------------------------------------------------------------------


def _field_names(cfg: MilvusStoreConfig) -> list[str]:
    schema = build_schema(cfg)
    fields = getattr(schema, "fields", None)
    if fields is None:
        return [f.get("name") for f in schema]
    return [getattr(f, "name", None) or f.get("name") for f in fields]


def test_schema_has_primary_key_vector_and_scalars():
    cfg = MilvusStoreConfig()
    names = _field_names(cfg)
    assert names[0] == "chunk_id"  # 主键
    for required in ("dense_vector", "text", "doc_id", "tenant_id", "dataset_id", "metadata"):
        assert required in names


def test_bm25_enabled_adds_sparse_field_and_chinese_analyzer():
    cfg = MilvusStoreConfig(enable_bm25=True)
    assert "sparse_vector" in _field_names(cfg)
    schema = build_schema(cfg)
    text_field = next(f for f in schema.fields if f.name == "text")
    # analyzer_params 在 params 里是 JSON 字符串（Milvus 内部形态），按字符串比对
    assert '"type":"chinese"' in str(text_field.params.get("analyzer_params"))
    assert text_field.params.get("enable_analyzer") is True


def test_bm25_disabled_drops_sparse_field():
    names = _field_names(MilvusStoreConfig(enable_bm25=False))
    assert "sparse_vector" not in names


def test_index_params_by_type():
    def params_for(index_type: str):
        ip = build_index_params(MilvusStoreConfig(index_type=index_type))
        # IndexParams 迭代出来的是 IndexParam 对象（不是 dict），转成 dict 再断言
        return {item.field_name: item.to_dict() for item in ip}

    hnsw = params_for("HNSW")["dense_vector"]
    assert hnsw["index_type"] == "HNSW"
    assert hnsw["metric_type"] == "COSINE"
    # 索引参数（M / efConstruction / nlist）摊平在 dict 顶层
    assert hnsw["M"] == 16 and hnsw["efConstruction"] == 200

    ivf = params_for("IVF_FLAT")["dense_vector"]
    assert ivf["nlist"] == 1024

    auto = params_for("AUTOINDEX")["dense_vector"]
    assert auto["index_type"] == "AUTOINDEX"  # AUTOINDEX 不塞无关参数，否则会被拒


def test_search_params_keep_ef_at_or_above_limit():
    """Milvus 硬约束：ef 必须 >= limit，否则检索直接报错。"""
    cfg = MilvusStoreConfig(ef=32)
    assert build_search_params(cfg, 10)["params"]["ef"] == 32
    assert build_search_params(cfg, 64)["params"]["ef"] == 64
    assert build_search_params(MilvusStoreConfig(index_type="IVF_FLAT"), 10)["params"]["nprobe"] == 16


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def test_batched_splits_without_losing_items():
    rows = [{"i": i} for i in range(7)]
    assert [len(batch) for batch in _batched(rows, 3)] == [3, 3, 1]
    assert [len(batch) for batch in _batched([], 3)] == []


def test_normalize_flattens_and_keeps_bigger_is_better_direction():
    res = [[{"id": "a", "distance": 0.9, "entity": {"chunk_id": "a"}},
            {"id": "b", "distance": 0.2, "entity": {"chunk_id": "b"}}]]
    hits = _normalize(res, metric="COSINE")
    assert [h["entity"]["chunk_id"] for h in hits] == ["a", "b"]
    assert hits[0]["score"] == 0.9

    # L2 是"越小越近"：转成 1/(1+d)，方向与其他度量一致
    l2 = _normalize([[{"id": "x", "distance": 0.0}, {"id": "y", "distance": 3.0}]], metric="L2")
    assert l2[0]["score"] == 1.0 and l2[1]["score"] == 0.25


# ---------------------------------------------------------------------------
# 异常与资源释放
# ---------------------------------------------------------------------------


def test_connect_failure_is_translated_with_actionable_hint(monkeypatch):
    """连接失败要说清"下一步查什么"，而不是甩一个 gRPC 错误码。"""
    import eval.retrieval_eval.milvus_store as mod

    class _Boom:
        def __init__(self, **kwargs):
            raise RuntimeError("server unavailable")

    monkeypatch.setattr(mod, "_LOG", mod._LOG)
    original_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "pymilvus":
            class _M:
                MilvusClient = _Boom
            return _M
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    store = MilvusVectorStore(MilvusStoreConfig(uri="http://127.0.0.1:1"))
    with pytest.raises(MilvusStoreError, match="连接 Milvus 失败"):
        store.connect()


def test_delete_without_condition_is_refused():
    """不给条件的删除等于删全表，必须显式拒绝（fail-closed）。"""
    store = MilvusVectorStore(MilvusStoreConfig())
    with pytest.raises(MilvusStoreError, match="必须给 expr 或 ids"):
        store.delete()


def test_search_rejects_wrong_dimension():
    store = MilvusVectorStore(MilvusStoreConfig(dim=8))
    with pytest.raises(MilvusStoreError, match="维度"):
        store.search([0.1, 0.2])


def test_close_is_idempotent():
    store = MilvusVectorStore(MilvusStoreConfig())
    store.close()  # 没连过：不能抛
    store.close()


# ---------------------------------------------------------------------------
# 集成（需要真实 Milvus）
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not (_INTEGRATION and _INTEGRATION_URI),
    reason="需要 RAG4C_MILVUS_RUN_INTEGRATION=1 与 RAG4C_MILVUS_URI",
)
def test_integration_create_upsert_search_and_cleanup(monkeypatch):
    """端到端：建集合 -> 批量写 -> 检索（含过滤）-> 删集合。"""
    monkeypatch.setenv("RAG4C_MILVUS_COLLECTION", "rag4c_eval_it")
    cfg = MilvusStoreConfig.from_env()
    cfg.collection = "rag4c_eval_it"
    cfg.dim = 8  # 集成用例用小维度，省服务端资源
    cfg.enable_bm25 = False

    with MilvusVectorStore(cfg) as store:
        store.ensure_collection(drop_if_exists=True)
        now_ms = 1_800_000_000_000
        rows = [
            {
                "chunk_id": f"it#{i}",
                "doc_id": f"d{i % 2}",
                "text": f"文档片段 {i}",
                "text_hash": f"h{i}",
                "created_at": now_ms,
                "updated_at": now_ms,
                "source": "it",
                "parent_chunk_id": "",
                "acl": "",
                "tenant_id": "eval",
                "dataset_id": "it",
                "document_revision": 1,
                "content_revision": 1,
                "metadata": {},
                "dense_vector": [1.0 if j == i else 0.0 for j in range(8)],
            }
            for i in range(4)
        ]
        assert store.upsert(rows) == 4
        store.flush()

        hits = store.search([1.0, 0, 0, 0, 0, 0, 0, 0], top_k=2)
        assert hits and hits[0]["entity"]["chunk_id"] == "it#0"

        filtered = store.search(
            [1.0, 0, 0, 0, 0, 0, 0, 0], top_k=4, filter_expr='tenant_id == "nobody"'
        )
        assert filtered == []  # 过滤表达式生效
        store.drop()


# ---------------------------------------------------------------------------
# 生产语料黄金标注（离线校验，不需要连服务端）
# ---------------------------------------------------------------------------


def test_production_gold_is_well_formed():
    from eval.retrieval_eval.gold_production import PRODUCTION_GOLD

    assert len(PRODUCTION_GOLD) >= 10
    answerable = [c for c in PRODUCTION_GOLD if not c.unanswerable]
    unanswerable = [c for c in PRODUCTION_GOLD if c.unanswerable]
    assert len(answerable) >= 10 and len(unanswerable) >= 1
    for case in answerable:
        assert case.gold_chunk_ids, f"{case.case_id} 缺 gold"
        assert all(gid.strip() for gid in case.gold_chunk_ids)
        assert case.notes, f"{case.case_id} 缺标注理由"
    for case in unanswerable:
        assert case.gold_chunk_ids == (), "不可答用例不应有 gold"


def test_production_gold_validation_reports_missing_ids():
    from eval.retrieval_eval.gold_production import PRODUCTION_GOLD, validate_against_ids

    known = {gid for case in PRODUCTION_GOLD for gid in case.gold_chunk_ids}
    assert validate_against_ids(known) == []
    # 抽掉一个 id：必须被报出来（标注失效要早知道，不能让指标悄悄变差）
    broken = known - {next(iter(known))}
    assert len(validate_against_ids(broken)) == 1


def test_degraded_rate_metric_has_a_denominator():
    """降级率必须有分母：只记降级次数，分不清 1/10 还是 1/1000。

    snapshot 只对 ``<scope>.errors`` 计算 error_rate，分母取 ``<scope>.total``；
    名字写错（如 ``<scope>.degraded.errors``）就找不到分母、比率恒为 0。
    """
    from core.metrics import MetricsRegistry

    registry = MetricsRegistry()
    registry.record_outcome("probe_scope", degraded=True)
    registry.record_outcome("probe_scope")
    snap = registry.snapshot()
    assert snap["probe_scope.total"]["count"] == 2
    assert snap["probe_scope.errors"]["count"] == 1
    assert snap["probe_scope.errors"]["error_rate"] == 0.5


def test_uri_falls_back_to_project_settings_when_env_absent(monkeypatch):
    """用 RAG4C_ENV_FILE 指定配置文件时，URI 只在文件里、不进进程环境——
    这层兜底不加，评测就会莫名连到本机默认端口。"""
    from config.settings import get_settings

    _env(monkeypatch)
    monkeypatch.setattr(get_settings().milvus, "uri", "http://192.168.100.128:19530")
    assert MilvusStoreConfig.from_env().uri == "http://192.168.100.128:19530"
