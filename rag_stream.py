"""RAG4C 流式编排器（SSE 用）。

与 rag 模块的顺序编排语义完全对齐：同弃权门、同二轮检索补救、同
QueryResult 结构与 traces 语义；差异仅在本模块以生成器产出事件流，
供桥服务 /api/query/stream 以 SSE 推送：

- {"type": "phase", "phase": "retrieving" | "retrieved" | "generating" |
  "verifying" | "retrieving_again", ...}：阶段边界事件；
- {"type": "token", "text": "..."}：生成阶段逐 token 文本增量；
- {"type": "done", "result": {...}}：最终结果（与 /api/query 同构 payload）；
- 客户端断开（生成器被关闭）时 GeneratorExit 向上传播，LLM 流随之终止
  ——「停止生成」在服务端真正生效。

失败语义与顺序版一致：检索 / 生成 / 验证失败一律产出弃权结果，不抛异常。
本模块只依赖 rag_common 的共享纯函数与组件字典，不 import rag_graph。
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Any, Generator, Iterator

from config.settings import Settings, get_settings, resolve_tenant
from core.observability import bind_query_id, setup_observability
from core.tracing import current_trace, trace_session
from models.schemas import QueryResult, RetrievedChunk
from rag_common import MAX_ROUNDS, evidence_chunks, merge_chunks
from retrieval.pipeline import dense_cosines_of
from verify.verifier import VerificationResult

if TYPE_CHECKING:
    from core.run_events import RunObserver


_GATE_REASON_CODES = {
    "": "accepted",
    "无检索结果": "no_retrieval_results",
    "知识库无相关内容": "irrelevant_retrieval",
    "证据不足以支撑可靠声明": "insufficient_evidence",
    "生成失败": "generation_failed",
}


def _reason_code(reason: str) -> str:
    return _GATE_REASON_CODES.get(reason, "abstained")


def _notify_observer(observer: RunObserver | None, method: str, *args: Any, **kwargs: Any) -> Any:
    """Emit one best-effort business fact without affecting query behavior."""
    if observer is None:
        return None
    try:
        return getattr(observer, method)(*args, **kwargs)
    except Exception:
        return None


class _RetrievalAttemptObserver:
    """Add the stream round to retrieval search facts emitted by the pipeline."""

    def __init__(self, delegate: RunObserver) -> None:
        self.delegate = delegate
        self.attempt = 1

    def __getattr__(self, method: str) -> Any:
        callback = getattr(self.delegate, method)

        def notify(*args: Any, **kwargs: Any) -> Any:
            if (
                args
                and args[0] == "search"
                and method
                in {
                    "start_node",
                    "complete_node",
                    "fail_node",
                    "cancel_node",
                }
            ):
                kwargs.setdefault("attempt", self.attempt)
                if method == "start_node":
                    kwargs.setdefault("repeatable", True)
            return callback(*args, **kwargs)

        return notify


def _finalize_fact(observer: RunObserver | None, *, abstained: bool) -> None:
    _notify_observer(observer, "start_node", "finalize", attempt=1)
    _notify_observer(
        observer,
        "complete_node",
        "finalize",
        attempt=1,
        attributes={"outcome": "abstained" if abstained else "answered"},
    )


def _result_payload(result: QueryResult) -> dict[str, Any]:
    """QueryResult -> 可 JSON 序列化 dict，并附加证据片段（深链展示用）。

    与 server.app 的证据提取逻辑保持一致（供流式 / 非流式共用）。
    """
    payload = result.model_dump(mode="json")
    evidence: list[dict[str, Any]] = []
    for i, item in enumerate(result.verdict.get("evidence_chunks") or []):
        # 兼容两种形态：rag_common.evidence_chunks 返回 chunk dict（无 score/rank）；
        # 旧路径可能传入 RetrievedChunk 对象（带 .chunk/.score/.rank）。
        if isinstance(item, dict):
            chunk, score, rank = item, 0.0, i + 1
        else:
            chunk, score, rank = item.chunk, float(item.score), item.rank
        cid = chunk.get("chunk_id") if isinstance(chunk, dict) else chunk.chunk_id
        did = chunk.get("doc_id") if isinstance(chunk, dict) else chunk.doc_id
        text = chunk.get("text") if isinstance(chunk, dict) else chunk.text
        src = chunk.get("source") if isinstance(chunk, dict) else chunk.source
        evidence.append(
            {
                "chunk_id": cid,
                "doc_id": did,
                "text": (text or "")[:400] + ("…" if text and len(text) > 400 else ""),
                "score": round(score, 4),
                "rank": rank,
                "source": src,
            }
        )
    payload["evidence"] = evidence
    return payload


def answer_query_stream(
    query: str,
    acl: list[str] | None = None,
    retry: bool = True,
    settings: Settings | None = None,
    tenant_id: str | None = None,
    dataset_id: str | None = None,
    *,
    query_id: str | None = None,
    observer: RunObserver | None = None,
    tenant_resolved: bool = False,
) -> Iterator[dict[str, Any]]:
    """流式回答一次用户查询：逐事件产出（供 SSE 端点包装）。

    Args:
        query / acl / retry / settings / tenant_id / dataset_id:
            与 rag.answer_query 一致（dataset_id 空即全域检索）。
        query_id: 可选的调用方请求 ID；未提供时保持原有自动生成行为。
        observer: 调用方拥有的运行事实观察者；本函数不发送 run 终态。
        tenant_resolved: tenant_id 已由调用方按统一规则解析时跳过重复解析。

    Yields:
        事件 dict（type: phase | token | done）。done 事件的 result 为
        _result_payload 输出（与 /api/query 响应同构，含 evidence）。
    """
    from rag import get_pipeline  # 惰性导入：避免与 rag 的循环依赖

    comp = get_pipeline(settings)
    query_id = query_id or f"query-{uuid.uuid4().hex[:12]}"
    tenant = (
        tenant_id
        if tenant_resolved
        else resolve_tenant(tenant_id, settings or get_settings())
    )
    dataset = (dataset_id or "").strip()
    setup_observability((settings or get_settings()).observability)

    gate = comp["gate"]
    observer = _RetrievalAttemptObserver(observer) if observer is not None else None

    with bind_query_id(query_id), trace_session(query_id, observer=observer) as trace:
        traces: list[str] = []

        # ---------- 第一轮：检索 ----------
        yield {"type": "phase", "phase": "retrieving"}
        try:
            retrieval = comp["retrieval"].run(query, acl, tenant_id=tenant, dataset_id=dataset)
        except Exception as exc:  # noqa: BLE001 - 检索失败降级弃权
            _notify_observer(
                observer,
                "start_node",
                "search",
                attempt=1,
                repeatable=True,
            )
            _notify_observer(
                observer,
                "fail_node",
                "search",
                attempt=1,
                error_type=type(exc).__name__,
                error_code="retrieval_failed",
                recoverable=True,
                attributes={"reason": "retrieval_failed"},
            )
            _notify_observer(
                observer,
                "degraded",
                "search",
                attempt=1,
                attributes={"reason": "retrieval_failed"},
            )
            _notify_observer(
                observer,
                "skip_node",
                "gate.retrieval",
                attempt=1,
                attributes={"reason": "retrieval_failed"},
            )
            _finalize_fact(observer, abstained=True)
            yield {
                "type": "done",
                "result": _result_payload(
                    QueryResult(
                        query=query,
                        answer="",
                        abstained=True,
                        route="hybrid",
                        traces=[f"检索失败（弃权）: {exc}"] + trace.as_list(),
                    )
                ),
            }
            return
        traces.extend(retrieval.traces)
        chunks: list[RetrievedChunk] = list(retrieval.chunks)
        retrieval_scores = [rc.score for rc in chunks]
        # rerank 没跑成时 score 是 RRF 融合分，与 retrieval_score_threshold 不同尺。
        scores_comparable = retrieval.reranked
        # 那种情况下改用真实稠密余弦这把尺子；空列表 = 这一路信号本次也没有。
        dense_cosines = dense_cosines_of(chunks)
        yield {
            "type": "phase",
            "phase": "retrieved",
            "route": retrieval.route.target,
            "chunks": len(chunks),
        }

        # ---------- 弃权门（检索阶段） ----------
        _notify_observer(observer, "start_node", "gate.retrieval", attempt=1)
        abstain, reason = gate.decide(
            retrieval_scores,
            None,
            retrieval_scores_comparable=scores_comparable,
            dense_cosines=dense_cosines,
        )
        _notify_observer(
            observer,
            "complete_node",
            "gate.retrieval",
            attempt=1,
            attributes={"abstained": abstain, "reason": _reason_code(reason)},
        )
        if abstain:
            traces.append(f"弃权: {reason}")
            _finalize_fact(observer, abstained=True)
            yield {
                "type": "done",
                "result": _result_payload(
                    QueryResult(
                        query=query,
                        answer="",
                        abstained=True,
                        route=retrieval.route.target,
                        traces=traces + trace.as_list(),
                    )
                ),
            }
            return

        # ---------- 第一轮：流式生成 + 验证 ----------
        generated, verification, traces = yield from _stream_generate_verify(
            comp, query, chunks, traces, observer=observer, attempt=1
        )

        # ---------- 二轮检索补救（证据不足时；同样流式生成） ----------
        rounds = 1
        retry_stopped = False
        needs_retry = verification.missing_evidence or not verification.supported
        if needs_retry and not retry:
            _notify_observer(
                observer,
                "retry_skipped",
                "search",
                attempt=1,
                attributes={"reason": "disabled", "target_attempt": 2},
            )
        while retry and rounds < MAX_ROUNDS and needs_retry:
            rounds += 1
            retry_attempt = rounds - 1
            _notify_observer(
                observer,
                "retry_started",
                "search",
                attempt=retry_attempt,
                attributes={"target_attempt": rounds},
            )
            if isinstance(observer, _RetrievalAttemptObserver):
                observer.attempt = rounds
            yield {"type": "phase", "phase": "retrieving_again"}
            try:
                retrieval2 = comp["retrieval"].run(query, acl, tenant_id=tenant, dataset_id=dataset)
            except Exception as exc:  # noqa: BLE001 - 补救失败沿用第一轮结果
                _notify_observer(
                    observer,
                    "retry_failed",
                    "search",
                    attempt=retry_attempt,
                    error_type=type(exc).__name__,
                    error_code="retrieval_failed",
                    recoverable=True,
                    attributes={"reason": "retrieval_failed", "target_attempt": rounds},
                )
                traces.append(f"二轮检索失败（沿用第一轮结果）: {exc}")
                retry_stopped = True
                break
            merged = merge_chunks(chunks, retrieval2.chunks)
            added_chunk_count = len(merged) - len(chunks)
            if added_chunk_count == 0:
                _notify_observer(
                    observer,
                    "retry_completed",
                    "search",
                    attempt=retry_attempt,
                    attributes={
                        "reason": "no_new_evidence",
                        "target_attempt": rounds,
                        "added_chunk_count": 0,
                    },
                )
                # 无新增证据：再跑也无益，避免死循环
                traces.append("二轮检索无新增证据，停止补救")
                retry_stopped = True
                break
            chunks = merged
            retrieval_scores = [rc.score for rc in chunks]
            # 合并后混了两轮的分数，任一轮没重排则整体不可比。
            scores_comparable = scores_comparable and retrieval2.reranked
            # 余弦也对合并后的列表现算——第一轮那批 chunk 未必还在 merged 里。
            dense_cosines = dense_cosines_of(chunks)
            traces.extend(retrieval2.traces)
            traces.append(f"二轮检索已执行（第 {rounds} 轮，合并 {len(chunks)} 条证据）")
            generated, verification, traces = yield from _stream_generate_verify(
                comp, query, chunks, traces, observer=observer, attempt=rounds
            )
            needs_retry = verification.missing_evidence or not verification.supported
            _notify_observer(
                observer,
                "retry_completed",
                "search",
                attempt=retry_attempt,
                attributes={
                    "reason": "evidence_insufficient" if needs_retry else "recovered",
                    "target_attempt": rounds,
                    "added_chunk_count": added_chunk_count,
                },
            )

        if retry and needs_retry and rounds >= MAX_ROUNDS and not retry_stopped:
            _notify_observer(
                observer,
                "retry_skipped",
                "search",
                attempt=rounds,
                attributes={"reason": "exhausted", "last_attempt": rounds},
            )

        # ---------- 弃权门（验证阶段） ----------
        entailment_scores = verification.gate_entailment_scores()
        _notify_observer(observer, "start_node", "gate.final", attempt=1)
        if generated is None:
            abstain, reason = True, "生成失败"
        else:
            abstain, reason = gate.decide(
                retrieval_scores,
                entailment_scores,
                retrieval_scores_comparable=scores_comparable,
                dense_cosines=dense_cosines,
            )
        _notify_observer(
            observer,
            "complete_node",
            "gate.final",
            attempt=1,
            attributes={"abstained": abstain, "reason": _reason_code(reason)},
        )
        if abstain:
            traces.append(f"弃权: {reason}")
            _finalize_fact(observer, abstained=True)
            yield {
                "type": "done",
                "result": _result_payload(
                    QueryResult(
                        query=query,
                        answer="",
                        abstained=True,
                        route=retrieval.route.target,
                        traces=traces + trace.as_list(),
                    )
                ),
            }
            return

        # ---------- 最终结果 ----------
        _finalize_fact(observer, abstained=False)
        yield {
            "type": "done",
            "result": _result_payload(
                QueryResult(
                    query=query,
                    answer=generated.answer if generated is not None else "",
                    citations=verification.citations,
                    verdict={"evidence_chunks": evidence_chunks(chunks)},
                    abstained=False,
                    route=retrieval.route.target,
                    traces=traces + trace.as_list(),
                )
            ),
        }


def _stream_generate_verify(
    comp: dict[str, Any],
    query: str,
    chunks: list[RetrievedChunk],
    traces: list[str],
    *,
    observer: RunObserver | None,
    attempt: int,
) -> Generator[dict[str, Any], None, tuple[Any, VerificationResult, list[str]]]:
    """一轮「流式生成 + 三层验证」（yield from 子生成器）。

    生成期间逐 token 转发；生成失败**返回** (None, 失败 VerificationResult, traces)，
    与顺序版 generate_and_verify 的兜底语义一致（最终由弃权门接管）。
    """
    _notify_observer(observer, "start_node", "generate", attempt=attempt, repeatable=True)
    t0 = time.perf_counter()
    raw_parts: list[str] = []
    generated = None
    try:
        yield {"type": "phase", "phase": "generating"}
        for token in comp["generator"].generate_stream(query, chunks):
            raw_parts.append(token)
            yield {"type": "token", "text": token}
        evidence_count = len(comp["generator"]._dedup_chunks(chunks))
        generated = comp["generator"].postprocess_stream("".join(raw_parts), evidence_count)
        _notify_observer(
            observer,
            "complete_node",
            "generate",
            attempt=attempt,
            attributes={
                "output_chars": len(generated.answer),
                "token_count": len(raw_parts),
            },
        )
    except GeneratorExit:
        _notify_observer(
            observer,
            "cancel_node",
            "generate",
            attempt=attempt,
            attributes={"reason": "client_disconnected"},
        )
        raise  # 客户端断开：向上传播，终止 LLM 流
    except Exception as exc:  # noqa: BLE001 - 生成失败按弃权处理
        _notify_observer(
            observer,
            "fail_node",
            "generate",
            attempt=attempt,
            error_type=type(exc).__name__,
            error_code="generation_failed",
            recoverable=True,
        )
        _notify_observer(
            observer,
            "degraded",
            "generate",
            attempt=attempt,
            attributes={"reason": "generation_failed"},
        )
        traces.append(f"生成失败（弃权）: {exc}")
        return (None, VerificationResult(notes=[f"生成失败: {exc}"]), traces)
    finally:
        # 生成耗时 span（无论成败）
        current = current_trace()
        if current is not None:
            current.add_span("generate", (time.perf_counter() - t0) * 1000.0)

    _notify_observer(observer, "start_node", "verify", attempt=attempt, repeatable=True)
    try:
        yield {"type": "phase", "phase": "verifying"}
        verification = comp["verifier"].verify(generated.answer, chunks)
    except GeneratorExit:
        _notify_observer(
            observer,
            "cancel_node",
            "verify",
            attempt=attempt,
            attributes={"reason": "client_disconnected"},
        )
        raise
    except Exception as exc:  # noqa: BLE001 - 验证失败按弃权处理
        _notify_observer(
            observer,
            "fail_node",
            "verify",
            attempt=attempt,
            error_type=type(exc).__name__,
            error_code="verification_failed",
            recoverable=True,
        )
        _notify_observer(
            observer,
            "degraded",
            "verify",
            attempt=attempt,
            attributes={"reason": "verification_failed"},
        )
        verification = VerificationResult(notes=[f"验证失败: {exc}"])
    else:
        _notify_observer(
            observer,
            "complete_node",
            "verify",
            attempt=attempt,
            attributes={
                "citation_count": len(verification.citations),
                "entailment_evaluated": verification.entailment_evaluated,
                "missing_evidence": verification.missing_evidence,
                "supported": verification.supported,
            },
        )
    traces.extend(verification.notes)
    return (generated, verification, traces)
