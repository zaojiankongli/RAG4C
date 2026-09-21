"""RAG4C 编排共享工具（:mod:`rag` 与 :mod:`rag_graph` 两条编排路径共用）。

从 ``rag.py`` 抽出的模块级共享逻辑，消除 rag_graph.py 对 rag.py 内部私有
函数（``_evidence_chunks`` / ``_merge_chunks`` / ``_generate_and_verify``）
的跨模块私有耦合：图路径不再依赖顺序路径的内部实现，``import rag_graph``
也不再会拉起 ``rag`` 的全部依赖链。

内容：
- :data:`MAX_ROUNDS`：检索-生成-验证的最多轮数（1 轮 + 1 轮补救）。
- :func:`generate_and_verify`：执行一轮生成 + 三层验证。
- :func:`merge_chunks`：按 chunk_id 合并多轮检索结果（保持首次出现顺序）。
- :func:`evidence_chunks`：把检索结果转成供裁判（eval）使用的证据 chunk
  dict 列表。
- :func:`resolve_tenant`：多租户强制隔离的统一租户解析入口
  （实现位于 :mod:`config.settings`，此处再导出，供两条编排路径共用）。

本模块只依赖 ``models.schemas`` / ``generation.generator`` /
``verify.verifier`` / ``config.settings`` / ``typing``，不 import
``rag`` / ``rag_graph``，两条编排路径（顺序版在 :mod:`rag`，图版在
:mod:`rag_graph`）均可离线导入。
"""
from __future__ import annotations

from typing import Any

from config.settings import resolve_tenant
from generation.generator import Generator
from models.schemas import RetrievedChunk
from verify.verifier import CitationVerifier, VerificationResult

# 检索-生成-验证的最多轮数（1 轮 + 1 轮补救）
MAX_ROUNDS = 2


def generate_and_verify(
    comp: dict[str, Any],
    query: str,
    chunks: list[RetrievedChunk],
) -> tuple[Any | None, VerificationResult]:
    """执行一轮生成 + 三层验证。

    Args:
        comp: :func:`rag.get_pipeline` 返回的组件字典。
        query: 用户查询。
        chunks: 本轮证据（检索结果）。

    Returns:
        (generated, verification)；generated 为 None 表示生成失败
        （调用方按空答案 / 弃权处理）。
    """
    generator: Generator = comp["generator"]
    verifier: CitationVerifier = comp["verifier"]
    try:
        generated = generator.generate(query, chunks)
        verification = verifier.verify(generated.answer, chunks)
        return generated, verification
    except Exception as exc:  # noqa: BLE001 - 编排层兜底：失败按弃权处理
        return None, VerificationResult(notes=[f"生成或验证失败: {exc}"])


def merge_chunks(*lists: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """按 chunk_id 合并多轮检索结果（保持首次出现顺序）。"""
    seen: set[str] = set()
    out: list[RetrievedChunk] = []
    for chunks in lists:
        for rc in chunks:
            if rc.chunk.chunk_id not in seen:
                seen.add(rc.chunk.chunk_id)
                out.append(rc)
    return out


def evidence_chunks(chunks: list[RetrievedChunk]) -> list[dict[str, Any]]:
    """把检索结果转成供裁判（eval）使用的证据 chunk dict 列表。"""
    out: list[dict[str, Any]] = []
    for rc in chunks:
        dump = rc.chunk.model_dump(mode="json")
        try:
            from retrieval.qa_retrieval import qa_evidence_enrichment

            dump = qa_evidence_enrichment(dump)
        except Exception:  # noqa: BLE001
            pass
        if rc.branch:
            dump.setdefault("branch", rc.branch)
        out.append(dump)
    return out


def second_round_can_help(settings: Any) -> bool:
    """二轮检索**有没有可能**捞到新证据。没可能就别跑。

    二轮补救两条路径都是拿**同一个 query、同一套参数**再调一次
    ``RetrievalPipeline.run``。检索链路本身是确定性的：同样的输入 → 同样的
    嵌入 → 同样的 Milvus 命中 → 同样的重排序。于是 ``merge_chunks`` 必然
    不增长，代码走到「二轮检索无新增证据，停止补救」break 掉——**但一次
    完整检索的钱已经花光了**：一次嵌入 API、一次 Milvus 混合检索、一次重排
    API 往返。

    唯一能让两轮结果不同的是带 LLM 的查询增强（HyDE / 子查询 / Stepback）：
    它们温度非零，每轮生成的假设文档 / 子问题不同，检索入口才真的变了。
    三者默认全关，也就是说**默认配置下这一轮 100% 是纯浪费**，而且专挑
    「答案本来就没被证据支撑」的慢请求上加码——最该省的时候最费。

    这里不删功能，只是不做已知无用的调用：三个开关全关时返回 False，
    调用方跳过并在 traces 里写明原因（而不是静默少跑一轮）。任一开关打开，
    行为与改前完全一致。
    """
    try:
        p = settings.pipeline
    except AttributeError:
        return True  # 拿不到配置时按老行为走，不擅自省略
    return bool(p.hyde_on or p.subqueries_on or p.stepback_on)


__all__ = [
    "MAX_ROUNDS",
    "merge_chunks",
    "evidence_chunks",
    "generate_and_verify",
    "resolve_tenant",
    "second_round_can_help",
]
