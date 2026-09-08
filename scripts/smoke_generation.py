"""RAG4C 生成模块冒烟测试（完全离线）。

作用：用 FakeLLM 替身验证生成模块，覆盖：
- 不联网、不调用真实模型、不启动 Milvus
- 引用标记解析与校验（越界标记剥离并记入警告）
- 证据按 chunk_id 去重、编号重建 1..N
- 模板占位符替换（question / evidence / max_tokens_hint / language）
- 空答案抛出 GenerationError
- 追踪 span 写入
- 退出码 0

运行：
    python scripts/smoke_generation.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

# 保证从任意工作目录运行都能找到 config / models / core / generation
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models.schemas import Chunk, RetrievedChunk
from core.tracing import current_trace, trace_session
from generation import (
    GeneratedAnswer,
    CitationExtractor,
    GenerationError,
    Generator,
    create_generator,
)

QUESTION = "系统如何组织检索与排序？"

CANNED_ANSWER = (
    "根据证据，系统采用混合检索策略，同时结合稠密向量与 BM25 稀疏检索[1]。"
    "检索结果经过重排序以提升相关性[3]。另有一个越界引用编号[9]。"
)


class FakeLLM:
    """离线替身：记录调用消息并返回预设文本。"""

    def __init__(self, output: str = CANNED_ANSWER, model: str = "fake-llm") -> None:
        self.output = output
        self.config = SimpleNamespace(model=model)
        self.calls: list[list[dict]] = []

    def chat(self, messages: list[dict], json_mode: bool = False) -> str:
        self.calls.append(messages)
        return self.output


def _chunk(cid: str, text: str) -> Chunk:
    now = datetime.now(timezone.utc)
    return Chunk(
        chunk_id=cid,
        doc_id="doc-" + cid,
        text=text,
        text_hash="smoke-hash",
        created_at=now,
        updated_at=now,
    )


def _retrieved(cid: str, text: str, score: float, rank: int) -> RetrievedChunk:
    return RetrievedChunk(chunk=_chunk(cid, text), score=score, rank=rank, branch="hybrid")


def main() -> int:
    # ---------------- 1. 引用解析 ----------------
    ids = CitationExtractor.parse_citation_ids(CANNED_ANSWER)
    assert ids == [1, 3, 9], ids
    # 无方括号的普通数字不应被解析为引用
    assert CitationExtractor.parse_citation_ids("价格 42 元[1]") == [1]
    assert CitationExtractor.parse_citation_ids("无引用标记的文本") == []
    print("[1/6] parse_citation_ids ok  ids=%s" % ids)

    # ---------------- 2. 引用校验 ----------------
    valid, invalid = CitationExtractor.validate(ids, evidence_count=3)
    assert valid == [1, 3], valid
    assert invalid == [9], invalid
    assert CitationExtractor.validate([0, 4], 3) == ([], [0, 4])
    print("[2/6] validate ok  valid=%s invalid=%s" % (valid, invalid))

    # ---------------- 3. 生成：去重 + 越界剥离 + 警告 ----------------
    chunks = [
        _retrieved("c1", "证据一：混合检索结合稠密向量与 BM25。", 0.9, 0),
        _retrieved("c2", "证据二：结果经过重排序。", 0.8, 1),
        _retrieved("c3", "证据三：BM25 由内置 Function 处理。", 0.7, 2),
        _retrieved("c1", "证据一（重复片段，应被去重）。", 0.6, 3),
    ]
    fake = FakeLLM()
    gen = Generator(llm_client=fake, language="zh")
    result = gen.generate(query=QUESTION, chunks=chunks)

    assert isinstance(result, GeneratedAnswer)
    assert "[9]" not in result.answer, result.answer
    assert "[1]" in result.answer and "[3]" in result.answer
    assert result.citation_ids == [1, 3], result.citation_ids
    assert len(result.evidence) == 3, len(result.evidence)
    assert [c.chunk_id for c in result.evidence] == ["c1", "c2", "c3"]
    assert any("9" in w for w in result.warnings), result.warnings

    # prompt 替换与编号重建检查
    assert fake.calls[0][0]["role"] == "system"
    assert fake.calls[0][1] == {"role": "user", "content": QUESTION}
    system_msg = fake.calls[0][0]["content"]
    assert "{question}" not in system_msg
    assert "{evidence}" not in system_msg
    assert "{max_tokens_hint}" not in system_msg
    assert "{language}" not in system_msg
    assert "中文" in system_msg
    assert "[1]" in system_msg and "[2]" in system_msg and "[3]" in system_msg
    print(
        "[3/6] generate ok  evidence=%d warnings=%d"
        % (len(result.evidence), len(result.warnings))
    )

    # ---------------- 4. 追踪 span ----------------
    with trace_session("trace-gen-001") as trace:
        gen.generate(query=QUESTION, chunks=chunks)
        names = [name for name, _ in trace.spans]
        assert "generate" in names, names
    assert current_trace() is None  # 退出会话后解绑
    print("[4/6] trace span ok")

    # ---------------- 5. 空答案 -> GenerationError ----------------
    empty = FakeLLM(output="   ")
    gen_empty = Generator(llm_client=empty, language="zh")
    try:
        gen_empty.generate(query=QUESTION, chunks=chunks)
        raise AssertionError("空答案应抛出 GenerationError")
    except GenerationError as exc:
        assert "fake-llm" in str(exc), str(exc)
    print("[5/6] empty answer raises GenerationError ok")

    # ---------------- 6. 工厂函数（离线创建，不发起任何请求） ----------------
    factory_gen = create_generator()
    assert isinstance(factory_gen, Generator)
    print("[6/6] create_generator ok  model=%s" % factory_gen.llm_client.config.model)

    print("=" * 60)
    print("SMOKE GENERATION PASSED (exit 0)")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
