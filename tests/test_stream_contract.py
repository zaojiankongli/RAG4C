from __future__ import annotations

import inspect
from datetime import datetime, timezone

import pytest

from models.schemas import Chunk, RetrievedChunk
from retrieval.pipeline import RetrievalResult
from retrieval.router import RouteDecision
from verify.verifier import VerificationResult

# 这个文件守的是流式端点的一个纯机械缺陷，但后果是整条 SSE 链路 100% 断掉。
#
#   generated, verification, traces = yield from _stream_generate_verify(...)
#
# `yield from` 求值的是子生成器的**返回值**（StopIteration.value），不是它
# yield 出来的最后一个东西。而 _stream_generate_verify 从前一个带值 `return`
# 都没有——两处结果都是 `yield` 出去的。于是每次调用都会：
#
#   1. 先把那个三元组当成一个 SSE 事件转发给前端（前端等的是 {"type": ...} dict）；
#   2. 然后拿 None 去解包三个变量 -> TypeError -> 流当场断在生成阶段。
#
# 这类错误类型检查本该拦住，但签名写的是 `Iterator[...]`——Iterator 表达不出
# 返回值类型，所以它放过了。现在签名是 Generator[Yield, Send, Return]。

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_chunk(cid: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk=Chunk(
            chunk_id=cid,
            doc_id="doc-1",
            text=f"正文 {cid}",
            text_hash=f"hash-{cid}",
            created_at=_NOW,
            updated_at=_NOW,
        ),
        score=0.9,
        rank=1,
        branch="hybrid",
    )


class FakeRetrieval:
    def __init__(self, chunks, *, error: BaseException | None = None):
        self._chunks = chunks
        self._error = error

    def run(self, query, acl=None, tenant_id=None, dataset_id=None):
        if self._error is not None:
            raise self._error
        return RetrievalResult(
            query=query,
            chunks=list(self._chunks),
            route=RouteDecision(target="hybrid", confidence=1.0),
            traces=["fake 检索"],
            reranked=True,  # 分数与阈值同尺，检索闸正常参与判定
        )


class FakeGenerated:
    def __init__(self, answer: str):
        self.answer = answer


class FakeGenerator:
    def __init__(self, tokens, fail: bool = False):
        self.tokens = tokens
        self.fail = fail

    def generate_stream(self, query, chunks):
        for t in self.tokens:
            yield t
        if self.fail:
            raise RuntimeError("桩：生成中途炸了")

    def _dedup_chunks(self, chunks):
        return chunks

    def postprocess_stream(self, text, evidence_count):
        return FakeGenerated(text)


class FakeVerifier:
    def __init__(self, result: VerificationResult | None = None):
        self.result = result or VerificationResult(
            supported=True, entailment_scores={"c": 1.0}, entailment_evaluated=True
        )

    def verify(self, answer, chunks, strict=None, question=""):
        # strict/question 是 CitationVerifier 的既有契约参数，桩必须一并接受
        del strict, question
        return self.result


@pytest.fixture
def stream(monkeypatch):
    """把 rag.get_pipeline 换成桩，驱动真实的 answer_query_stream。"""
    import rag
    from verify.abstention import AbstentionGate

    def build(
        generator,
        verifier=None,
        chunks=None,
        retry=False,
        *,
        query_id=None,
        observer=None,
        retrieval_error=None,
    ):
        comp = {
            # 注意 `chunks if chunks is not None else [...]`：空列表是**有意义的输入**
            # （测"一条都没召回"），用 `chunks or [...]` 会把它当成"没传"而悄悄替换掉。
            "retrieval": FakeRetrieval(
                chunks if chunks is not None else [make_chunk("c1")],
                error=retrieval_error,
            ),
            "generator": generator,
            "verifier": verifier or FakeVerifier(),
            "gate": AbstentionGate(retrieval_threshold=0.3, entailment_threshold=0.6),
        }
        monkeypatch.setattr(rag, "get_pipeline", lambda settings=None: comp)
        from rag_stream import answer_query_stream

        observability = {}
        if query_id is not None:
            observability["query_id"] = query_id
        if observer is not None:
            observability["observer"] = observer
        return list(answer_query_stream("报销流程", retry=retry, **observability))

    return build


# --------------------------------------------------------------------------- #


def test_stream_observability_inputs_are_keyword_only() -> None:
    from rag_stream import answer_query_stream

    signature = inspect.signature(answer_query_stream)

    assert signature.parameters["query_id"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["observer"].kind is inspect.Parameter.KEYWORD_ONLY


def test_observer_does_not_change_legacy_event_order_or_payloads(
    stream, monkeypatch: pytest.MonkeyPatch
) -> None:
    import rag_stream

    class NoOpObserver:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    class ThrowingObserver:
        def __getattr__(self, _name):
            def fail(*args, **kwargs):
                raise RuntimeError("observer failure")

            return fail

    monkeypatch.setattr(rag_stream.time, "perf_counter", lambda: 1.0)
    baseline = stream(FakeGenerator(["答案"]), query_id="query-contract")
    observed = stream(
        FakeGenerator(["答案"]),
        query_id="query-contract",
        observer=NoOpObserver(),
    )
    throwing = stream(
        FakeGenerator(["答案"]),
        query_id="query-contract",
        observer=ThrowingObserver(),
    )

    assert observed == baseline
    assert throwing == baseline
    assert [event["type"] for event in observed] == [
        "phase",
        "phase",
        "phase",
        "token",
        "phase",
        "done",
    ]


def test_retrieval_failure_observer_does_not_change_phase_and_done_abstention(
    stream, monkeypatch: pytest.MonkeyPatch
) -> None:
    import rag_stream

    class NoOpObserver:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    class ThrowingObserver:
        def __getattr__(self, _name):
            def fail(*args, **kwargs):
                raise RuntimeError("observer failure")

            return fail

    monkeypatch.setattr(rag_stream.time, "perf_counter", lambda: 1.0)
    retrieval_error = RuntimeError("retrieval unavailable")
    baseline = stream(
        FakeGenerator(["unused"]),
        query_id="query-retrieval-failure-contract",
        retrieval_error=retrieval_error,
    )
    observed = stream(
        FakeGenerator(["unused"]),
        query_id="query-retrieval-failure-contract",
        observer=NoOpObserver(),
        retrieval_error=retrieval_error,
    )
    throwing = stream(
        FakeGenerator(["unused"]),
        query_id="query-retrieval-failure-contract",
        observer=ThrowingObserver(),
        retrieval_error=retrieval_error,
    )

    assert observed == baseline
    assert throwing == baseline
    assert [event["type"] for event in baseline] == ["phase", "done"]
    assert baseline[-1]["result"]["abstained"] is True


def test_stream_completes_without_unpacking_none(stream) -> None:
    # 从前这里是 TypeError: cannot unpack non-iterable NoneType object。
    events = stream(FakeGenerator(["报销", "流程", "如下"]))

    assert events[-1]["type"] == "done"
    assert events[-1]["result"]["answer"] == "报销流程如下"
    assert events[-1]["result"]["abstained"] is False


def test_every_event_is_a_typed_dict(stream) -> None:
    # 子生成器的返回值绝不能混进事件流：前端按 event["type"] 分发，
    # 收到一个裸元组会直接崩在解析上。
    events = stream(FakeGenerator(["a", "b"]))

    for i, ev in enumerate(events):
        assert isinstance(ev, dict), f"第 {i} 个事件不是 dict：{ev!r}"
        assert "type" in ev, f"第 {i} 个事件没有 type 字段：{ev!r}"
    assert [e["type"] for e in events].count("done") == 1


def test_tokens_are_forwarded_in_order(stream) -> None:
    events = stream(FakeGenerator(["报", "销", "流", "程"]))
    tokens = [e["text"] for e in events if e["type"] == "token"]

    assert tokens == ["报", "销", "流", "程"]


def test_generation_failure_abstains_instead_of_breaking_the_stream(stream) -> None:
    # 生成中途失败走的是另一条 return 路径（从前同样是 yield + 裸 return）。
    events = stream(FakeGenerator(["半句"], fail=True))

    assert events[-1]["type"] == "done"
    assert events[-1]["result"]["abstained"] is True
    assert any("生成失败" in t for t in events[-1]["result"]["traces"])


def test_verifier_failure_does_not_break_the_stream(stream) -> None:
    class BoomVerifier:
        def verify(self, answer, chunks, strict=None, question=""):
            del strict, question
            raise RuntimeError("桩：验证层炸了")

    events = stream(FakeGenerator(["答案"]), verifier=BoomVerifier())

    assert events[-1]["type"] == "done"
    assert any("验证失败" in t for t in events[-1]["result"]["traces"])


def test_phase_events_bracket_the_generation(stream) -> None:
    events = stream(FakeGenerator(["x"]))
    phases = [e["phase"] for e in events if e["type"] == "phase"]

    assert "retrieving" in phases
    assert "generating" in phases
    assert "verifying" in phases


def test_empty_retrieval_abstains_before_generating(stream) -> None:
    # 检索为空时压根不该进生成阶段（省一次 LLM 调用）。
    events = stream(FakeGenerator(["不该出现"]), chunks=[])

    assert events[-1]["result"]["abstained"] is True
    assert not [e for e in events if e["type"] == "token"]
