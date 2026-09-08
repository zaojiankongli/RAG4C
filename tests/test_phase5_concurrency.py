"""阶段 5：把关键路径上**没有依赖关系的串行等待**拆掉。

## 这些测试在防什么

默认配置下一次查询要走 9~10 次串行往返，其中好几次是纯粹的写法开销——
不是"必须先算出 A 才能算 B"，只是循环恰好一条一条地发。这类开销有个共同
特点：**它不会让任何测试变红**。功能全对，结果全对，只是慢，而慢多少取决
于模型端点当天的心情，看板上分不出来。所以只能靠专门的测试把"发了几次"
和"并发还是串行"钉住。

四个点各自的回归条件：

1. **路由示例向量**（``retrieval/router.py``）：三路示例必须合成**一次**
   ``embed_texts``；并发冷启动时必须只算**一次**（缺锁的话是每路并发各算
   一遍，正好发生在进程刚起来最没余量的时刻）。
2. **事后引用指派**（``verify/verifier.py``）：声明与证据合成一次嵌入。
3. **查询端增强**（``retrieval/pipeline.py``）：HyDE / 子查询 / 后退提问的
   三次 LLM 生成必须并发发出——它们都只吃同一个 ``search_query``，彼此没有
   数据依赖。
4. **子查询扇出**：池宽可配；单路卡死不能把整条 HTTP 请求永久挂住。

1 和 2 都是"多次调用合并成一次批量"，而合并引入了一个新的失败模式：
**切分点错位**。分开调用时某一批少返回几条只会少配几对；合成一批之后少一
条，后半段整体前移——路由会静默指向错误的分支，声明会被指派到错误的
chunk 并且带着 ``status="ok"``。所以每一处合并都配了一条"少返回"的用例，
它们比"快了多少"重要得多。
"""
from __future__ import annotations

import hashlib
import threading
import time
from datetime import datetime, timezone

import pytest

from config.settings import PipelineSettings
from models.schemas import Chunk, RetrievedChunk, RouteDecision
from retrieval.pipeline import RetrievalPipeline
from retrieval.router import EmbeddingRouter

_NOW = datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# 桩
# --------------------------------------------------------------------------- #
class CountingEmbedder:
    """记录每一次 ``embed_texts`` 的入参，用来数"发了几次、每次带了什么"。"""

    def __init__(self, dim: int = 256, short_by: int = 0) -> None:
        self.dim = dim
        self.short_by = short_by          # 刻意少返回几条，用来测错位防护
        self.batches: list[list[str]] = []
        self.lock = threading.Lock()

    def _vec(self, text: str) -> list[float]:
        """文本 -> 稳定且各不相同的单位向量（不同文本的余弦相似度恒为 0）。

        用 md5 而不是内置 ``hash()``：后者对字符串带每进程随机盐，两段不同的
        文本会时不时落进同一个桶，于是"按相似度配对"的用例在个别进程里凭并
        列取胜负——测试因此会**偶发**变红，而变红的原因与被测代码无关。这类
        用例最后往往被当成 flaky 加上重试，真缺陷也就跟着被重试掩盖了。
        维度取得远大于用例里的文本数，撞桶概率可忽略；万一撞了也是每次都撞，
        当场就能看见，不会是偶发。
        """
        index = int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16) % self.dim
        vec = [0.0] * self.dim
        vec[index] = 1.0
        return vec

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        with self.lock:
            self.batches.append(list(texts))
        out = [self._vec(t) for t in texts]
        return out[: len(out) - self.short_by] if self.short_by else out

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)

    @property
    def calls(self) -> int:
        return len(self.batches)


class SlowEmbedder(CountingEmbedder):
    """嵌入要花时间——只有这样"并发还是串行"才在墙钟上分得出来。"""

    def __init__(self, delay_s: float = 0.05, **kw) -> None:
        super().__init__(**kw)
        self.delay_s = delay_s

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        time.sleep(self.delay_s)
        return super().embed_texts(texts)


def make_chunk(chunk_id: str, text: str, score: float = 0.9) -> RetrievedChunk:
    chunk = Chunk(
        chunk_id=chunk_id,
        doc_id="doc-1",
        text=text,
        text_hash=f"hash-{chunk_id}",
        created_at=_NOW,
        updated_at=_NOW,
    )
    return RetrievedChunk(chunk=chunk, score=score, rank=0, branch="hybrid")


# --------------------------------------------------------------------------- #
# 1. 路由示例向量：一次批量、只算一次、错位要挡住
# --------------------------------------------------------------------------- #
def test_exemplars_are_embedded_in_a_single_batch() -> None:
    """三路示例 = 三次串行嵌入往返，而它们之间没有任何数据依赖。"""
    embedder = CountingEmbedder()
    router = EmbeddingRouter(embedder=embedder)
    router.route("公司的报销流程是什么")

    exemplar_batches = [b for b in embedder.batches if len(b) > 1]
    assert len(exemplar_batches) == 1, (
        f"示例嵌入被拆成了 {len(exemplar_batches)} 次请求：{embedder.batches}"
    )
    flat = [t for texts in router.exemplars.values() for t in texts]
    assert exemplar_batches[0] == flat, "批量里的文本必须是全部示例，顺序即切分依据"


def test_exemplar_vectors_stay_matched_to_their_route() -> None:
    """合并成一批之后，切分点错位会让 A 路由拿到 B 路由的向量。"""
    embedder = CountingEmbedder()
    router = EmbeddingRouter(embedder=embedder)
    vectors = router._ensure_exemplar_vectors()  # noqa: SLF001

    for route, texts in router.exemplars.items():
        assert vectors[route] == [embedder._vec(t) for t in texts], (  # noqa: SLF001
            f"{route} 拿到的不是自己的示例向量——切分点错位"
        )


def test_short_embed_result_is_not_cached_as_a_half_baked_mapping() -> None:
    """少返回一条就会让后半段整体前移。宁可这次不缓存，下次重算。"""
    embedder = CountingEmbedder(short_by=1)
    router = EmbeddingRouter(embedder=embedder)

    vectors = router._ensure_exemplar_vectors()  # noqa: SLF001
    assert all(v == [] for v in vectors.values()), "数量不符时不许硬切，会错位"
    assert router._exemplar_vectors is None, "半成品不能进缓存，否则路由被永久钉歪"  # noqa: SLF001

    # 而且降级方向是对的：各路皆空 -> 置信度 0 -> degraded -> 交给 LLM 兜底
    decision = router.route("随便问点什么")
    assert decision.degraded is True


def test_concurrent_cold_start_embeds_exemplars_only_once() -> None:
    """缺锁时，冷启动瞬间涌进来的 N 路并发会各自把示例嵌入一遍。"""
    embedder = SlowEmbedder(delay_s=0.05)
    router = EmbeddingRouter(embedder=embedder)
    start = threading.Barrier(8)

    def worker() -> None:
        start.wait(timeout=5)
        router.route("A和B是什么关系")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    exemplar_batches = [b for b in embedder.batches if len(b) > 1]
    assert len(exemplar_batches) == 1, (
        f"8 路并发把示例算了 {len(exemplar_batches)} 遍——缺的是那把锁"
    )


def test_prewarm_moves_the_cost_off_the_first_query() -> None:
    embedder = CountingEmbedder()
    router = EmbeddingRouter(embedder=embedder)

    assert router.prewarm() is True
    before = embedder.calls
    router.route("什么是Milvus")
    # 首次查询只该剩下"嵌入这条查询"本身（走 embed_query，不计入 batches）
    assert embedder.calls == before, "预热之后首次查询不该再算一遍示例"


def test_prewarm_failure_is_not_fatal() -> None:
    """预热是锦上添花：嵌入服务此刻没起来，只该回到惰性计算那条路。"""

    class Broken:
        def embed_texts(self, texts):
            raise RuntimeError("桩：嵌入服务未就绪")

        def embed_query(self, text):
            raise RuntimeError("桩：嵌入服务未就绪")

    assert EmbeddingRouter(embedder=Broken()).prewarm() is False


# --------------------------------------------------------------------------- #
# 2. 事后引用指派：一次批量、错位要挡住
# --------------------------------------------------------------------------- #
def _verifier(embedder):
    from verify.verifier import CitationVerifier

    return CitationVerifier(milvus=None, judge_llm=None, embedder=embedder)


def test_posthoc_assignment_uses_one_embedding_request() -> None:
    """这条路径在 LLM 一个引用标记都没吐出来时触发——答案已经比平时更糟的
    那一次，不该在关键路径上再叠一次多余的嵌入往返。"""
    embedder = CountingEmbedder()
    claims = ["报销需要发票", "审批由主管完成"]
    evidence = [make_chunk("c1", "报销需要发票"), make_chunk("c2", "审批由主管完成")]
    notes: list[str] = []

    _verifier(embedder)._assign_posthoc(claims, evidence, {}, notes)  # noqa: SLF001

    assert embedder.calls == 1, f"声明与证据被分成了 {embedder.calls} 次请求"
    assert embedder.batches[0] == claims + [rc.chunk.text for rc in evidence]


def test_posthoc_assignment_matches_each_claim_to_its_own_evidence() -> None:
    """合并之后切分点一错，每条声明都会被指派到错误的 chunk——而且是带着
    ``status="ok"`` 指派的，下游看不出任何异常。"""
    embedder = CountingEmbedder()
    claims = ["报销需要发票", "审批由主管完成"]
    evidence = [make_chunk("c1", "审批由主管完成"), make_chunk("c2", "报销需要发票")]
    citations: dict[str, list] = {}

    _verifier(embedder)._assign_posthoc(claims, evidence, citations, [])  # noqa: SLF001

    # 证据顺序与声明顺序**故意错开**：只有真正按相似度配对才会得到 c2 / c1
    assert citations["报销需要发票"][0].chunk_id == "c2"
    assert citations["审批由主管完成"][0].chunk_id == "c1"


def test_posthoc_assignment_refuses_a_misaligned_batch() -> None:
    """少返回一条时，切分点前移会让**每条**声明都指向 chunk_vecs[0]。

    两条声明 + 两条证据的组合专门用来把这件事挑明：错位之后"审批由主管完
    成"会被指派到讲报销发票的 c1，而且带着 ``status="ok"``。宁可整段跳过。
    """
    embedder = CountingEmbedder(short_by=1)
    claims = ["报销需要发票", "审批由主管完成"]
    evidence = [make_chunk("c1", "报销需要发票"), make_chunk("c2", "审批由主管完成")]
    citations: dict[str, list] = {}
    notes: list[str] = []

    _verifier(embedder)._assign_posthoc(claims, evidence, citations, notes)  # noqa: SLF001

    assert citations == {}, (
        f"数量对不上还硬切，等于凭空捏造 status=ok 的引用："
        f"{ {k: v[0].chunk_id for k, v in citations.items()} }"
    )
    assert any("数量不符" in n for n in notes), f"跳过了却不说为什么：{notes}"


# --------------------------------------------------------------------------- #
# 3. 查询端增强：三次 LLM 生成必须并发发出
# --------------------------------------------------------------------------- #
class SlowGenerator:
    """一次生成花 ``delay_s``；记录被调用的时刻，用来判断是否重叠。"""

    def __init__(self, value, delay_s: float = 0.15, boom: bool = False) -> None:
        self.value = value
        self.delay_s = delay_s
        self.boom = boom
        self.queries: list[str] = []
        self.windows: list[tuple[float, float]] = []

    def generate(self, query: str):
        t0 = time.perf_counter()
        self.queries.append(query)
        time.sleep(self.delay_s)
        self.windows.append((t0, time.perf_counter()))
        if self.boom:
            raise RuntimeError("桩：生成失败")
        return self.value


class _Milvus:
    def __init__(self) -> None:
        self.searches = 0
        self.lock = threading.Lock()

    def hybrid_search(self, query_dense, top_k: int, **kw) -> list[RetrievedChunk]:
        with self.lock:
            self.searches += 1
        return [make_chunk("c1", "命中正文")]

    @classmethod
    def build_filters(cls, *a, **kw):
        return None


class _Rewriter:
    def rewrite(self, query: str) -> tuple[str, bool]:
        return query, False


class _Router:
    def route(self, query: str) -> RouteDecision:
        return RouteDecision(target="hybrid", confidence=1.0)


def _enhanced_pipeline(hyde, subqueries, stepback, **overrides) -> RetrievalPipeline:
    settings = PipelineSettings(
        complexity_gate_on=False,
        rerank_on=False,
        source_diversity="off",
        hyde_on=hyde is not None,
        subqueries_on=subqueries is not None,
        stepback_on=stepback is not None,
        **overrides,
    )
    return RetrievalPipeline(
        embedder=CountingEmbedder(),
        milvus=_Milvus(),
        reranker=None,
        rewriter=_Rewriter(),
        router=_Router(),
        settings=settings,
        hyde=hyde,
        subqueries=subqueries,
        stepback=stepback,
    )


def test_three_enhancers_generate_concurrently() -> None:
    """三次生成都只吃同一个 search_query，串行执行纯粹是写法留下的。"""
    delay = 0.15
    hyde = SlowGenerator("假设文档", delay)
    subs = SlowGenerator(["子查询甲", "子查询乙"], delay)
    stepback = SlowGenerator("后退问题", delay)
    pipeline = _enhanced_pipeline(hyde, subs, stepback)

    t0 = time.perf_counter()
    try:
        pipeline.run("一个需要拆解的复杂问题")
    finally:
        pipeline.shutdown()
    elapsed = time.perf_counter() - t0

    assert elapsed < delay * 2.2, (
        f"三次生成耗时 {elapsed:.3f}s，接近 3×{delay}s——说明还是一条接一条发的"
    )
    # 墙钟之外再直接验重叠：任意两个生成窗口必须相交
    windows = [g.windows[0] for g in (hyde, subs, stepback)]
    for i in range(len(windows)):
        for j in range(i + 1, len(windows)):
            (a0, a1), (b0, b1) = windows[i], windows[j]
            assert a0 < b1 and b0 < a1, "两次生成的时间窗不相交 = 它们是串行的"


def test_each_enhancer_still_sees_the_same_search_query() -> None:
    hyde = SlowGenerator("假设文档", 0.0)
    subs = SlowGenerator([], 0.0)
    stepback = SlowGenerator(None, 0.0)
    pipeline = _enhanced_pipeline(hyde, subs, stepback)
    try:
        pipeline.run("报销流程")
    finally:
        pipeline.shutdown()

    assert hyde.queries == subs.queries == stepback.queries == ["报销流程"]


def test_one_failing_enhancer_does_not_take_down_the_others() -> None:
    """三者互不影响是原本就有的语义（各自 try / 各自降级）。放进线程池以后
    必须保持——否则一个增强器的失败会顺着 Future 冒出来带走整条查询。"""
    hyde = SlowGenerator(None, 0.0, boom=True)
    subs = SlowGenerator(["子查询甲"], 0.0)
    stepback = SlowGenerator("后退问题", 0.0)
    pipeline = _enhanced_pipeline(hyde, subs, stepback)
    try:
        result = pipeline.run("报销流程")
    finally:
        pipeline.shutdown()

    assert result.chunks, "一个增强器炸了不该把结果清空"
    joined = " ".join(result.traces)
    assert "hyde 生成失败" in joined, f"失败没被如实记录：{result.traces}"
    assert subs.queries and stepback.queries, "另外两个增强器被连累了"


def test_single_enhancer_needs_no_thread_pool() -> None:
    """只开一个时并发无从谈起，不该白建一个池。"""
    pipeline = _enhanced_pipeline(SlowGenerator("假设文档", 0.0), None, None)
    try:
        pipeline.run("报销流程")
        assert pipeline._enhance_executor is None  # noqa: SLF001
    finally:
        pipeline.shutdown()


def test_enhancer_spans_report_work_not_idle_waiting() -> None:
    """span 要报"这一步花了多少"，不是"主线程在这里坐了多久"。

    并发之后主线程可能在某个阶段一秒不等（生成早已完成）。若 span 只量本地
    墙钟，同一份工作量会在两次查询里报出完全不同的数，trace 就没法用来定位
    慢在哪了。
    """
    hyde = SlowGenerator("假设文档", 0.20)      # 最慢，其余两个都在它之下完成
    subs = SlowGenerator([], 0.05)
    stepback = SlowGenerator(None, 0.05)
    pipeline = _enhanced_pipeline(hyde, subs, stepback)
    try:
        result = pipeline.run("报销流程")
    finally:
        pipeline.shutdown()

    spans = dict(
        (s.split(":", 1)[0], float(s.split(":", 1)[1].rstrip("ms")))
        for s in result.traces
        if ":" in s and s.rstrip().endswith("ms")
    )
    assert spans["subqueries"] >= 40.0, (
        f"subqueries span 报了 {spans['subqueries']:.1f}ms，"
        "生成那 50ms 被并发藏掉了"
    )
    assert spans["stepback"] >= 40.0, f"stepback span 同理：{spans['stepback']:.1f}ms"


# --------------------------------------------------------------------------- #
# 4. 子查询扇出：池宽可配 + 单路卡死不能挂住整条请求
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("workers", [1, 4, 16])
def test_fanout_pool_width_is_configurable(workers: int) -> None:
    """这个池是进程级单例，由所有并发查询共用：宽度不够时"并发扇出"就退化
    成排队，而排队的时间一分不少地算在用户等待里。它取决于部署，不该是个
    写死的字面量。"""
    pipeline = _enhanced_pipeline(None, None, None, subquery_fanout_workers=workers)
    try:
        assert pipeline._ensure_fanout_executor()._max_workers == workers  # noqa: SLF001
    finally:
        pipeline.shutdown()


def test_a_hung_subquery_search_does_not_hang_the_whole_query() -> None:
    """不带超时的 ``future.result()`` 会让一路卡死的检索把整条 HTTP 请求永久
    挂住，而那个请求还占着一个准入名额——几路下来服务对外就是整体不可用。"""
    release = threading.Event()

    class HangingMilvus(_Milvus):
        def hybrid_search(self, query_dense, top_k: int, **kw):
            if kw.get("query_text") != "报销流程":   # 只卡子查询那几路
                release.wait(timeout=8)
            return [make_chunk("c1", "命中正文")]

    pipeline = _enhanced_pipeline(
        None, SlowGenerator(["子查询甲"], 0.0), None,
        subquery_fanout_timeout_s=0.2,
    )
    pipeline.milvus = HangingMilvus()
    try:
        t0 = time.perf_counter()
        result = pipeline.run("报销流程")
        elapsed = time.perf_counter() - t0
    finally:
        release.set()
        pipeline.shutdown()

    assert elapsed < 5.0, f"整条查询被一路卡死的子查询拖了 {elapsed:.1f}s"
    assert result.chunks, "超时应该带着主检索结果照常返回，而不是空手而归"
    assert any("超时" in t for t in result.traces), f"降级了却没留痕：{result.traces}"
