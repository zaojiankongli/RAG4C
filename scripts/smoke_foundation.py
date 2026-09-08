"""RAG4C 基础包冒烟测试。

作用：import 全部模块，用哑配置实例化所有客户端，验证：
- 不安装 pymilvus / FlagEmbedding / openai 也能 import（惰性依赖）
- 不联网、不下载模型、不启动 Milvus
- 退出码 0

运行：
    python scripts/smoke_foundation.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# 保证从任意工作目录运行都能找到 config / models / core
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 隔离 .env：本脚本断言的是**默认配置语义**，必须与生产 .env 解耦。
#
# 原来的做法是逐个 os.environ.setdefault 钉住会被 .env 覆盖的键，但那只挡得住
# 作者当时想到的那几个——生产 .env 里新增任何一项配置，这里就会莫名其妙红一条
# （实测：给 LLM 槽位配了云端 provider 之后，第 69 行的 base_url 断言当场失败，
# 而那明明是一次完全正确的配置变更）。测试不该因为「有人正常配置了项目」而失败。
#
# 改为整体关门：RAG4C_ENV_FILE 指向一个不存在的路径，_load_env_file 会走显式
# 分支并提前返回，从而完全跳过 .env 查找；同时清掉进程里已有的 RAG4C_* ——
# 否则从配了环境变量的 shell 里运行，断言的就不是默认值了。
import os

os.environ["RAG4C_ENV_FILE"] = str(
    Path(__file__).resolve().parent / "_smoke_foundation_no_such.env"
)
for _k in [k for k in os.environ if k.startswith("RAG4C_") and k != "RAG4C_ENV_FILE"]:
    del os.environ[_k]

from datetime import datetime, timezone

from config.settings import (
    Settings,
    MilvusSettings,
    EmbeddingSettings,
    RerankerSettings,
    LlmSlotSettings,
    LlmSlotsSettings,
    PipelineSettings,
    ObservabilitySettings,
    get_settings,
)
from models.schemas import (
    Chunk,
    RetrievedChunk,
    Citation,
    QueryResult,
    RouteDecision,
    JudgeResult,
)
from core.tracing import TraceContext, current_trace, trace_session
from core.llm import LLMClient, LLMError, ParseFallbackError, create_client
from core.embedding import (
    EmbeddingService,
    Bge3LocalEmbedder,
    ApiEmbedder,
    create_embedder,
)
from core.reranker import (
    Reranker,
    BgeReranker,
    LlmReranker,
)
from core.milvus_client import RagMilvusClient, RagMilvusError


def main() -> int:
    # ---------------- 1. 配置 ----------------
    settings = get_settings()
    assert settings.milvus.uri == "./rag4c.db", settings.milvus.uri
    assert settings.milvus.dim == 1024
    assert settings.embedding.model == "BAAI/bge-m3"
    assert settings.reranker.model == "BAAI/bge-reranker-v2-m3"
    assert settings.llm.generation.base_url == "http://localhost:11434/v1"
    assert settings.llm.rewrite.temperature == 0.2
    assert settings.llm.router_llm.temperature == 0.1
    assert settings.llm.judge.temperature == 0.0
    assert settings.pipeline.source_diversity in ("off", "group_only", "group_mmr")
    # 各配置段类可独立实例化（验证暴露的类型契约）
    MilvusSettings(uri="./x.db")
    EmbeddingSettings(provider="api")
    RerankerSettings(model="x")
    LlmSlotSettings(model="x")
    LlmSlotsSettings(rewrite={"model": "y"})
    PipelineSettings(source_diversity="group_mmr")
    ObservabilitySettings(log_level="DEBUG")
    print(f"[1/7] settings ok  collection={settings.milvus.collection_name}")

    # 独立构造一个 Settings（验证嵌套默认值 + 任意覆盖）
    s2 = Settings(milvus={"uri": "http://localhost:19530", "dim": 768})
    assert s2.milvus.uri == "http://localhost:19530"
    assert s2.milvus.dim == 768
    assert s2.milvus.token == ""  # 未被覆盖的字段保留默认
    print("[1/7] settings nested override ok")

    # ---------------- 2. 数据模型 ----------------
    now = datetime.now(timezone.utc)
    chunk = Chunk(
        chunk_id="chunk-001",
        doc_id="doc-001",
        text="Milvus supports BM25 full-text search via built-in Function.",
        text_hash="abc123",
        created_at=now,
        updated_at=now,
        source="pdf",
        metadata={"acl": "fin", "page": 3},
    )
    rc = RetrievedChunk(chunk=chunk, score=0.87, rank=0, branch="hybrid")
    cit = Citation(claim="Milvus 支持 BM25", chunk_id="chunk-001", status="ok", reason="命中原文")
    qr = QueryResult(query="Milvus 支持什么?", answer="...", citations=[cit], abstained=False)
    rd = RouteDecision(target="hybrid", confidence=0.9)
    jr = JudgeResult(score=0.8, rationale="充分支撑", unsupported_claims=[], missing_facts=[])
    assert rc.chunk.chunk_id == "chunk-001"
    assert qr.citations[0].status == "ok"
    assert rd.target == "hybrid" and not rd.degraded
    assert jr.score == 0.8
    print("[2/7] models ok")

    # ---------------- 3. 追踪 ----------------
    with trace_session("trace-001") as trace:
        trace.add_span("embed", 12.3)
        trace.add_span("retrieve", 45.6)
        assert current_trace() is trace
    assert current_trace() is None  # 退出会话后解绑
    t = TraceContext(query_id="q1")
    t.add_span("rerank", 1.0)
    assert t.spans == [("rerank", 1.0)]
    print("[3/7] tracing ok")

    # ---------------- 4. LLM（惰性，不联网） ----------------
    llm = create_client(settings.llm.generation)
    assert isinstance(llm, LLMClient)
    assert llm.config.model == settings.llm.generation.model
    # 不应在此处调用 chat()（会发请求）
    try:
        ParseFallbackError("x")
    except LLMError:
        pass
    print("[4/7] llm ok  model=%s" % llm.config.model)

    # ---------------- 5. 嵌入（惰性，不下载模型） ----------------
    local = Bge3LocalEmbedder(model_name="dummy-model", device="cpu")
    assert local._model is None  # 惰性：未加载
    api = ApiEmbedder(model="dummy-model", base_url="http://localhost:9997/v1", api_key="x")
    assert api._client is None  # 惰性：未创建客户端
    svc = create_embedder(EmbeddingSettings(provider="api", api_base_url="http://localhost:9997/v1"))
    assert isinstance(svc, EmbeddingService)  # 协议兼容检查
    print("[5/7] embedding ok  provider=%s" % settings.embedding.provider)

    # ---------------- 6. 重排序（惰性） ----------------
    bge = BgeReranker(model_name="dummy-model", device="cpu")
    assert bge._model is None
    llm_rerank = LlmReranker(llm=llm, fallback_on_error=False)
    assert isinstance(llm_rerank, LlmReranker)
    assert isinstance(bge, Reranker)  # 协议兼容检查
    # 不调用 rerank()（会触发 LLM 调用）
    print("[6/7] reranker ok")

    # ---------------- 7. Milvus（惰性，不启动） ----------------
    lite = RagMilvusClient(MilvusSettings(uri="./rag4c.db"))
    assert lite.is_lite and not lite.is_server
    assert lite._client is None  # 未连接

    server = RagMilvusClient(MilvusSettings(uri="http://localhost:19530", token="root:Milvus"))
    assert server.is_server and not server.is_lite
    assert server._client is None

    # 错误类型契约
    assert issubclass(RagMilvusError, Exception)

    # ACL 过滤表达式构造。引号由 _quote_expr_str 统一负责，故与下面的 tenant 一样是双引号；
    # 从前 ACL 走单引号、tenant 走双引号，两套转义规则并存正是注入缺陷的温床。
    assert 'acl IN ["fin", "legal"]' == RagMilvusClient.build_acl_filter(["fin", "legal"])
    # 带引号的 ACL 值必须被转义在字面量内部，不能闭合字面量。
    assert 'acl IN ["a\\"b"]' == RagMilvusClient.build_acl_filter(['a"b'])

    # 租户过滤表达式构造：空串 / None -> 不过滤；非空 -> 精确匹配
    assert RagMilvusClient.build_tenant_filter("") is None
    assert RagMilvusClient.build_tenant_filter(None) is None
    assert 'tenant_id == "acme"' == RagMilvusClient.build_tenant_filter("acme")
    # 组合过滤：tenant 无条件 + ACL 按开关
    combo = RagMilvusClient.build_filters("acme", ["fin", "legal"])
    assert 'tenant_id == "acme"' in combo and 'acl IN ["fin", "legal"]' in combo
    assert combo.index("tenant_id") < combo.index("acl")  # AND 拼接，tenant 在前
    assert RagMilvusClient.build_filters("") is None
    assert RagMilvusClient.build_filters("acme", acl_filter_on=False) == 'tenant_id == "acme"'
    print("[7/7] milvus ok  lite=%s server=%s" % (lite.mode, server.mode))

    # ---- 空结果绕坑：hybrid_search 全路无命中时不得当成故障 ----
    # Milvus 在融合结果整体为空时抛 "unsupported ID type"（服务端推不出主键
    # ID 类型）。这是正常业务状态（空知识库 / 新租户 / ACL 全过滤），必须
    # 当成 0 条返回：否则弃权理由是错的，更要命的是每次都记一笔熔断失败，
    # 反复查空库就能打开检索熔断器，让所有租户的检索一起快速失败。
    probe = RagMilvusClient(MilvusSettings(uri="./rag4c.db"))

    class _FakeClient:
        """按需回放：query 返回 rows，用来复核「是否真的一行都没有」。"""

        def __init__(self, rows):
            self.rows = rows
            self.query_calls = 0

        def query(self, **kwargs):
            self.query_calls += 1
            return list(self.rows)

    empty_err = Exception("service internal error: unsupported ID type")
    real_err = Exception("service internal error: some other failure")

    # 特征串命中 + 复核确认无行 -> 判为空结果
    fc = _FakeClient([])
    assert probe._is_empty_result_quirk(fc, empty_err, 'dataset_id == "nope"') is True
    assert fc.query_calls == 1, "必须做一次复核查询，不能只凭错误串下结论"

    # 特征串命中但复核发现有行 -> 是真实故障，不许吞
    fc2 = _FakeClient([{"chunk_id": "c1"}])
    assert probe._is_empty_result_quirk(fc2, empty_err, None) is False

    # 特征串未命中 -> 直接判定为真实故障，且不该浪费一次复核查询
    fc3 = _FakeClient([])
    assert probe._is_empty_result_quirk(fc3, real_err, None) is False
    assert fc3.query_calls == 0

    # 复核查询本身失败时不敢下结论，按真实故障处理
    class _BrokenClient:
        def query(self, **kwargs):
            raise RuntimeError("connection reset")

    assert probe._is_empty_result_quirk(_BrokenClient(), empty_err, None) is False
    print("[7/7] milvus 空结果绕坑 ok")

    # ---- BM25 分词器解析 ----
    # 默认必须是 chinese：漏了 analyzer_params 会让中文 BM25 召回恒为 0，
    # 且不报任何错，混合检索静默退化成纯稠密检索。
    assert probe._resolve_analyzer_params() == {"type": "chinese"}
    assert RagMilvusClient(
        MilvusSettings(uri="./rag4c.db", bm25_analyzer="standard")
    )._resolve_analyzer_params() is None
    assert RagMilvusClient(
        MilvusSettings(uri="./rag4c.db", bm25_analyzer='{"tokenizer": "jieba"}')
    )._resolve_analyzer_params() == {"tokenizer": "jieba"}
    for bad in ("jieba", "[1,2]"):
        try:
            RagMilvusClient(
                MilvusSettings(uri="./rag4c.db", bm25_analyzer=bad)
            )._resolve_analyzer_params()
        except RagMilvusError:
            pass
        else:
            raise AssertionError(f"非法 bm25_analyzer={bad!r} 必须抛错而非静默回退")
    print("[7/7] bm25 分词器解析 ok")

    print("=" * 60)
    print("SMOKE FOUNDATION PASSED (exit 0)")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
