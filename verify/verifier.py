"""三层引用验证（L1 / L2 / L3）+ 事后引用指派。

企业 RAG 引用防线（任务书第 6 章）：

- **L1 存在性**：解析答案中的 ``[N]`` 引用编号，映射到本次检索证据
  （按 chunk_id 去重后的顺序，1 基）。编号不存在或不在证据集合内
  -> ``unsupported``（防伪造 / 越界编号）。
- **L2 文本哈希**：对通过 L1 的引用，从 Milvus 取回**当前** chunk 记录，
  与**检索快照**（生成答案时实际看到的那一份）的 ``text_hash`` 比较。
  不一致说明证据在检索之后被修改 -> ``stale``。未接入 milvus 或取回失败时
  没有"当前"可比，该轮如实记为未校验，而不是伪装成"已校验且通过"。
- **L3 蕴含判定**（成本分层）：对通过 L1+L2 的引用，用 judge 槽位
  按 :file:`prompts/judge_groundedness_v1.txt` 逐条判断声明是否被证据
  蕴含。支持整批（strict）与确定性抽样（strict=False）；失败降级为
  ``exists_only``，绝不让验证异常中断生成。
- **事后引用指派**（任务书 6.3）：免费 API 模型无法做生成期约束解码，
  当答案完全没有有效引用标记时，用嵌入向量余弦相似度把每条声明指派
  到最相似的证据 chunk（相似度 >= 0.5），再交由 L3 把关。

成本控制：
- L3 可抽样（``sample_ratio``，确定性选取）或整体跳过
  （``entailment_mode="skip"``）；
- L2 仅对通过 L1 的引用发起一次批量 ``get_chunks_by_ids``；
- 所有 LLM / Milvus / 嵌入异常都被捕获降级，验证不崩溃。

设计取舍：milvus 可为 None（测试 / 纯离线场景），此时 L2 只能判"记录是否还在"，
判不了"内容是否变过"。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from config.settings import get_settings
from core.embedding import EmbeddingService
from core.llm import create_client
from core.milvus_client import RagMilvusClient
from core.observability import get_logger
from core.tracing import current_trace
from models.schemas import Chunk, Citation, RetrievedChunk

from verify.claims import split_claims

# 项目根目录：本文件位于 <root>/verify/verifier.py
_PROJECT_ROOT = Path(__file__).resolve().parents[1]

_logger = get_logger(__name__)

# 默认 groundedness 评审模板（相对项目根）
_DEFAULT_TEMPLATE = "prompts/judge_groundedness_v1.txt"

# 事后指派的最低余弦相似度（任务书 6.3）
_POSTHOC_MIN_SIMILARITY = 0.5

# 引用标记正则（仅在 generation.CitationExtractor 不可导入时的本地兜底）
_CITATION_RE = re.compile(r"\[(\d+)\]")

# L3 判定状态 -> 蕴含分数
_VERDICT_SCORES: dict[str, float] = {
    "supported": 1.0,
    "unsupported": 0.0,
    "neutral": 0.5,
}

# 允许的蕴含判定模式
_ENTAILMENT_MODES = ("llm", "nli", "skip")

# L3 证据块上限。必须与 generation.MAX_EVIDENCE_CHUNKS 一致：生成器只喂了前 N 条，
# judge 若铺开全部证据，答案里的 [N] 编号就和 judge 看到的编号对不上。
# 这里不 import 而是复制常量（避免 verify -> generation 的方向依赖），
# 由 tests/test_verifier_robustness.py 钉住两者相等，防止悄悄漂移。
MAX_EVIDENCE_CHUNKS = 12

# 单条证据送进 judge 的字符上限：长尾文档会把 prompt 顶爆，
# 而爆掉的表现是 judge 调用失败 -> 走降级 -> 完全不可见。
_MAX_EVIDENCE_CHARS = 2000


def _truncate(text: str, limit: int) -> str:
    """超长文本截断并显式标注，避免 judge 把截断处误读成原文结尾。"""
    if len(text) <= limit:
        return text
    return text[:limit] + "…（已截断）"


class NliNotImplemented(RuntimeError):
    """NLI 模式预留占位：当前版本未内置 NLI 模型。"""


@dataclass
class VerificationResult:
    """一次引用验证的结果。

    Attributes:
        citations: 全部引用（含 L1 伪造 / L2 stale / L3 判定结论）。
        supported: 是否所有引用均为 ``ok``（全部通过三层防线）。
        missing_evidence: 是否存在未挂任何引用的声明（信号：应重试或弃权）。
        notes: 验证过程备注（降级 / 事后指派 / 抽样等）。
        entailment_scores: 声明文本 -> 蕴含分数（1.0 支撑 / 0.0 不支撑 /
            0.5 中性），仅 L3 实际评到的声明有记录。
        entailment_evaluated: L3 蕴含判定**这一轮到底跑没跑**。
            用来把两种"``entailment_scores`` 是空的"区分开：

            - ``True`` + 空 dict = L3 跑了，但一条声明都没能评上 -> 证据确实不足，该弃权；
            - ``False`` + 空 dict = L3 根本没跑（``entailment_mode="skip"``、
              judge 调用失败等）-> **无从判断**，不该拿它当"证据不足"的证据。

            弃权门的契约里 ``None`` 表示"不参与判定"、``[]`` 表示"有证据但无可用
            蕴含分"。历史上编排器无条件写 ``list(entailment_scores.values())``，
            于是 L3 没跑也会传出 ``[]``，被门判成弃权——配置里关掉 L3 反而导致
            100% 拒答。这个字段就是让调用方能正确地传 ``None``。
    """

    citations: list[Citation] = field(default_factory=list)
    supported: bool = False
    missing_evidence: bool = False
    notes: list[str] = field(default_factory=list)
    entailment_scores: dict[str, float] = field(default_factory=dict)
    entailment_evaluated: bool = False

    def gate_entailment_scores(self) -> list[float] | None:
        """按弃权门的契约给出蕴含分数：L3 没跑就是 ``None``（不参与判定）。

        编排器一律走这个方法，不要再自己 ``list(...values())``。
        """
        if not self.entailment_evaluated:
            return None
        return list(self.entailment_scores.values())


def _parse_citation_ids(answer: str) -> list[int]:
    """解析答案中的全部 [N] 引用编号（保持出现顺序）。

    优先复用 :class:`~generation.generator.CitationExtractor`（同一定义，
    保证与生成模块解析行为一致）；不可导入时本地正则兜底。
    """
    try:
        from generation.generator import CitationExtractor

        return CitationExtractor.parse_citation_ids(answer)
    except ImportError:  # pragma: no cover - 生成模块缺失时的本地兜底
        return [int(m.group(1)) for m in _CITATION_RE.finditer(answer)]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度。

    实现在 :func:`core.vectors.cosine_similarity`——``core.milvus_client`` 要算
    同一个东西，数值原语留两份迟早会各自漂移。这里保留这个名字是因为它已经
    被测试与本模块多处引用。
    """
    from core.vectors import cosine_similarity

    return cosine_similarity(a, b)


class CitationVerifier:
    """三层引用验证器（L1 存在性 / L2 哈希 / L3 蕴含）。

    Args:
        milvus: Milvus 客户端（可为 None：测试或纯离线场景下以证据
            chunk 为 L2 数据源）。
        judge_llm: judge 槽位 LLM 客户端（``chat_json`` 契约）。
        embedder: 嵌入服务（提供时才启用事后引用指派，任务书 6.3）。
        groundedness_template: L3 模板路径；None 使用默认模板
            ``prompts/judge_groundedness_v1.txt``（懒加载）。
        entailment_mode: "llm"（judge_llm 评审）| "nli"（预留，抛出
            :class:`NliNotImplemented`）| "skip"（跳过 L3）。
        strict: True 全量评审；False 按 ``sample_ratio`` 确定性抽样。
        sample_ratio: 非严格模式下送入 L3 的引用比例（0~1）。
    """

    def __init__(
        self,
        milvus: RagMilvusClient | None,
        judge_llm,
        embedder: EmbeddingService | None = None,
        groundedness_template: str | None = None,
        entailment_mode: str = "llm",
        strict: bool = True,
        sample_ratio: float = 1.0,
    ) -> None:
        if entailment_mode not in _ENTAILMENT_MODES:
            raise ValueError(
                f"entailment_mode 必须为 {_ENTAILMENT_MODES} 之一，收到 {entailment_mode!r}"
            )
        if not (0.0 < sample_ratio <= 1.0):
            raise ValueError(f"sample_ratio 必须落在 (0, 1]，收到 {sample_ratio!r}")
        self.milvus = milvus
        self.judge_llm = judge_llm
        self.embedder = embedder
        self.groundedness_template = groundedness_template or _DEFAULT_TEMPLATE
        self.entailment_mode = entailment_mode
        self.strict = strict
        self.sample_ratio = sample_ratio
        self._template_cache: str | None = None

    # ------------------------------------------------------------------ #
    # 公开入口
    # ------------------------------------------------------------------ #
    def verify(
        self,
        answer: str,
        evidence_chunks: list[RetrievedChunk],
        strict: bool | None = None,
    ) -> VerificationResult:
        """执行三层引用验证。

        Args:
            answer: 模型生成的答案文本（可含 [N] 引用标记）。
            evidence_chunks: 本次检索证据（按 chunk_id 去重后作为编号基准）。
            strict: 覆盖实例级 strict 设置（None 用实例默认）。

        Returns:
            :class:`VerificationResult`。任何外部服务异常都被捕获降级，
            本方法不抛出 LLM / Milvus / 嵌入相关异常。
        """
        t_start = time.perf_counter()
        effective_strict = self.strict if strict is None else strict
        notes: list[str] = []
        entailment_scores: dict[str, float] = {}

        # 证据按 chunk_id 去重（保持出现顺序），作为 1 基编号基准
        evidence = self._dedupe_evidence(evidence_chunks)
        id_to_chunk = {i + 1: rc for i, rc in enumerate(evidence)}

        claims_raw = split_claims(answer)
        claim_citations: dict[str, list[Citation]] = {}
        citations: list[Citation] = []
        claims: list[str] = []
        for raw in claims_raw:
            cid_list = _parse_citation_ids(raw)
            clean = self._clean_claim(raw)
            if not clean:
                continue
            claims.append(clean)
            claim_citations[clean] = []
            for cid in cid_list:
                cit = self._l1_check(clean, cid, id_to_chunk)
                claim_citations[clean].append(cit)
                citations.append(cit)

        # 事后引用指派（任务书 6.3）：答案完全没有有效引用标记时启用
        if not citations and self.embedder is not None and claims:
            self._assign_posthoc(claims, evidence, claim_citations, notes)
            for attached in claim_citations.values():
                citations.extend(attached)

        # L2：文本哈希陈旧性检查（仅对通过 L1 的 ok 引用）
        ok_citations = [c for c in citations if c.status == "ok"]
        if ok_citations:
            self._l2_hash_check(ok_citations, evidence, notes)

        # L3：蕴含判定（成本分层）
        ok_citations = [c for c in citations if c.status == "ok"]
        # 一条 ok 引用都没有时 L3 无从评起，这同样属于"没评"，不是"评了不达标"。
        entailment_evaluated = False
        if ok_citations:
            entailment_evaluated = self._l3_entailment(
                ok_citations,
                evidence,
                effective_strict,
                entailment_scores,
                notes,
            )

        missing_evidence = any(not claim_citations.get(c) for c in claims)
        supported = all(c.status == "ok" for c in citations)

        trace = current_trace()
        if trace is not None:
            trace.add_span("verify", (time.perf_counter() - t_start) * 1000.0)

        return VerificationResult(
            citations=citations,
            supported=supported,
            missing_evidence=missing_evidence,
            notes=notes,
            entailment_scores=entailment_scores,
            entailment_evaluated=entailment_evaluated,
        )

    # ------------------------------------------------------------------ #
    # L1：存在性
    # ------------------------------------------------------------------ #
    @staticmethod
    def _l1_check(
        claim: str,
        cid: int,
        id_to_chunk: dict[int, RetrievedChunk],
    ) -> Citation:
        """L1 存在性：编号是否落在本次证据集合内。

        编号无对应证据或对应 chunk 不在提供集合中 -> ``unsupported``。
        """
        rc = id_to_chunk.get(cid)
        if rc is None:
            return Citation(
                claim=claim,
                chunk_id=f"#{cid}",
                status="unsupported",
                reason="citation id 不存在或不在本次检索集合",
            )
        return Citation(claim=claim, chunk_id=rc.chunk.chunk_id, status="ok")

    # ------------------------------------------------------------------ #
    # L2：文本哈希陈旧性
    # ------------------------------------------------------------------ #
    def _l2_hash_check(
        self,
        citations: list[Citation],
        evidence: list[RetrievedChunk],
        notes: list[str],
    ) -> None:
        """L2 文本哈希检查：比对**检索快照**与 Milvus 当前记录。

        - 生成答案依据的是检索那一刻的文本快照（``evidence``）；
        - 陈旧性 = 那之后原文被改过 = 快照哈希 != 当前记录哈希；
        - milvus 为 None 或取回失败时**无法判定**（没有"当前"可比），记 note 说明；
        - 当前记录缺失（已删除）-> ``stale``。

        .. note::
           从前这里比的是 ``record.text_hash != text_hash(record.text)``——
           拿同一条记录的存储哈希和它自己的文本重新算的哈希比。这两个值是
           入库时一起写的，**恒等**，所以这个分支永远不成立，L2 是一段死代码。
           真正要比的是"生成时看到的"和"现在是什么"这两个**不同时刻**的值。
        """
        t0 = time.perf_counter()
        ids = sorted({c.chunk_id for c in citations})
        # 检索快照：答案实际依据的那一份文本的哈希。
        snapshot: dict[str, str] = {
            rc.chunk.chunk_id: rc.chunk.text_hash for rc in evidence
        }
        fresh: dict[str, Chunk] = {}
        comparable = True
        if self.milvus is None:
            # 没有数据源就没有"当前"，快照只能和自己比 —— 那不是检查。
            comparable = False
            notes.append("L2 陈旧性未校验（未接入 milvus，无当前记录可比）")
            fresh = {rc.chunk.chunk_id: rc.chunk for rc in evidence}
        else:
            try:
                records = self.milvus.get_chunks_by_ids(ids)
                fresh = {c.chunk_id: c for c in records}
            except Exception as exc:  # RagMilvusError 等：降级，不中断验证
                comparable = False
                notes.append(f"L2 取回最新 chunk 失败（陈旧性本轮未校验）: {exc}")
                fresh = {rc.chunk.chunk_id: rc.chunk for rc in evidence}
        for cit in citations:
            record = fresh.get(cit.chunk_id)
            if record is None:
                # 引用存在但最新记录缺失（可能已删除）：同样视为不可信
                cit.status = "stale"
                cit.reason = "chunk 记录已不存在（可能已删除）"
            elif comparable and record.text_hash != snapshot.get(
                cit.chunk_id, record.text_hash
            ):
                cit.status = "stale"
                cit.reason = "文本已变更（stale）"
        trace = current_trace()
        if trace is not None:
            trace.add_span("verify_l2", (time.perf_counter() - t0) * 1000.0)

    # ------------------------------------------------------------------ #
    # 事后引用指派（任务书 6.3）
    # ------------------------------------------------------------------ #
    def _assign_posthoc(
        self,
        claims: list[str],
        evidence: list[RetrievedChunk],
        claim_citations: dict[str, list[Citation]],
        notes: list[str],
    ) -> None:
        """嵌入对齐事后指派：为无引用答案的每条声明找最佳证据 chunk。

        相似度 >= 0.5 才指派；指派出的引用以 ``ok`` 起步（reason="事后指派"），
        后续照常进入 L2 / L3 把关。任何嵌入失败都记 note 并跳过。
        """
        if not claims or not evidence:
            return
        # 声明与证据合成**一次**嵌入请求。两者之间没有任何数据依赖，分两次
        # 发纯粹是写法留下的：这条路径在 LLM 一个 ``[N]`` 标记都没吐出来时
        # 触发（也就是答案已经比平时更糟的那次），却要在关键路径上再叠一次
        # 完整的嵌入往返。
        #
        # 合并的代价是**对齐**：分两次调用时，某一批少返回几条只会让 zip
        # 少配几对；合成一批之后，少返回一条就意味着 claim_vecs 与
        # chunk_vecs 的切分点错位——每条声明都会被指派到错误的 chunk，而且
        # 是带着 status="ok" 指派的。所以下面那条长度校验不是防御性编程，
        # 是这次合并的前提条件。
        texts = list(claims) + [rc.chunk.text for rc in evidence]
        try:
            vecs = self.embedder.embed_texts(texts)
        except Exception as exc:
            notes.append(f"事后引用指派嵌入失败（跳过）: {exc}")
            return
        if len(vecs) != len(texts):
            notes.append(
                f"事后引用指派嵌入数量不符（期望 {len(texts)}，实得 {len(vecs)}），跳过"
            )
            return
        claim_vecs, chunk_vecs = vecs[: len(claims)], vecs[len(claims) :]
        for claim, cvec in zip(claims, claim_vecs):
            best_j, best_sim = -1, -1.0
            for j, cvec2 in enumerate(chunk_vecs):
                sim = _cosine_similarity(cvec, cvec2)
                if sim > best_sim:
                    best_sim, best_j = sim, j
            if best_j >= 0 and best_sim >= _POSTHOC_MIN_SIMILARITY:
                chunk_id = evidence[best_j].chunk.chunk_id
                claim_citations[claim] = [
                    Citation(
                        claim=claim,
                        chunk_id=chunk_id,
                        status="ok",
                        reason="事后指派",
                    )
                ]
        notes.append("使用事后引用指派")

    # ------------------------------------------------------------------ #
    # L3：蕴含判定（成本分层）
    # ------------------------------------------------------------------ #
    def _l3_entailment(
        self,
        citations: list[Citation],
        evidence: list[RetrievedChunk],
        strict: bool,
        entailment_scores: dict[str, float],
        notes: list[str],
    ) -> bool:
        """L3 蕴含判定入口：按模式分发（llm / nli / skip），分层控成本。

        - ``skip``：直接跳过，不调用任何模型；
        - ``nli``：预留模式，抛出 :class:`NliNotImplemented`；
        - ``llm``：非严格模式先做确定性抽样，再调 judge_llm 评审；
          LLM 异常捕获降级为 ``exists_only``，绝不向上抛出。

        Returns:
            L3 是否真的做出了判定。``False`` 表示这一轮没有任何蕴含信息可用
            （跳过 / 无可评候选 / judge 故障），调用方据此向弃权门传 ``None``
            而不是空列表——否则"没评"会被门读成"评了且不达标"。
        """
        if self.entailment_mode == "skip":
            notes.append("L3 蕴含判定已跳过（entailment_mode=skip）")
            return False
        if self.entailment_mode == "nli":
            raise NliNotImplemented("NLI 模式预留，请配置 judge_llm")
        # llm 模式
        t0 = time.perf_counter()
        candidates = citations
        if not strict:
            candidates = self._sample_citations(citations)
            notes.append(f"非严格模式，L3 仅抽样评审 {len(candidates)}/{len(citations)} 条引用")
        if not candidates:
            return False
        # 按声明去重：同一声明多条 ok 引用只送一条声明，一并判定
        claim_to_ids: dict[str, list[int]] = {}
        for cit in candidates:
            claim_to_ids.setdefault(cit.claim, []).append(
                self._index_of_chunk(cit.chunk_id, evidence)
            )
        claims_to_judge = list(claim_to_ids.items())
        try:
            verdicts = self._judge_claims(claims_to_judge, evidence)
        except Exception as exc:  # noqa: BLE001 - 见下方说明
            # 这里刻意捕获 Exception 而非只捕 LLMError/ParseFallbackError。
            # _judge_claims 的输入是模型自由文本，失败面远不止"调用出错"：
            # 模板读不到是 ValueError，返回体结构不对是 TypeError/AttributeError/
            # KeyError。这些从前会穿透验证层，把**已经生成好的答案整个丢弃**并
            # 触发二轮重算——双倍计费，零输出。验证层的职责是给答案打标，
            # 它自己坏掉不该成为拒答的理由。
            notes.append(f"L3 判定失败（降级：蕴含不可用，本轮不参与弃权判定）: {exc}")
            for cit in candidates:
                if cit.status == "ok":
                    # 引用状态照常降级——"没能确认"确实弱于"已确认"。
                    # 但**不再合成 0.5 分**：judge 挂掉时我们对蕴含一无所知，
                    # 而 0.5 低于默认阈值 0.6，写进去等于把"不知道"伪装成
                    # "已判定为不支撑"，让门在检索和生成都已付费之后强制弃权。
                    cit.status = "exists_only"
                    cit.reason = "L3 判定失败（降级）"
            return False
        for claim, status in verdicts.items():
            entailment_scores[claim] = _VERDICT_SCORES.get(status, 0.5)
            if status != "unsupported":
                continue
            for cit in citations:
                if cit.claim == claim and cit.status == "ok":
                    cit.status = "unsupported"
                    cit.reason = "L3 蕴含校验不支撑"
        trace = current_trace()
        if trace is not None:
            trace.add_span("verify_l3", (time.perf_counter() - t0) * 1000.0)
        return True

    def _judge_claims(
        self,
        claims_to_judge: list[tuple[str, list[int]]],
        evidence: list[RetrievedChunk],
    ) -> dict[str, str]:
        """调 judge_llm 对声明逐条评审，返回 {声明: verdict}。

        verdict 取 ``supported`` / ``unsupported`` / ``neutral``。
        按模板约定 verdicts 与输入声明一一对应（索引兜底），
        再按声明文本精确匹配优先。
        """
        template = self._load_template()
        claims_block = "\n".join(
            f"{i}. 声明：{claim}（引用编号：[{','.join(str(n) for n in ids)}]）"
            for i, (claim, ids) in enumerate(claims_to_judge, 1)
        )
        # 证据块必须和生成侧用同一把尺子截断。生成器只喂了前 MAX_EVIDENCE_CHUNKS
        # 条（generation/generator.py:249），judge 这边若把全部证据铺开，
        # 编号就和答案里的 [N] 对不上了——更要命的是，长尾证据会把 prompt 顶爆，
        # 表现为随机的 judge 失败（而失败又走降级，于是完全不可见）。
        capped = evidence[:MAX_EVIDENCE_CHUNKS]
        evidence_block = "\n".join(
            f"[{i}] {_truncate(rc.chunk.text, _MAX_EVIDENCE_CHARS)}"
            for i, rc in enumerate(capped, 1)
        )
        prompt = (
            template.replace("{claims}", claims_block).replace("{evidence}", evidence_block)
        )
        messages = [{"role": "user", "content": prompt}]
        schema_hint = (
            '{"verdicts":[{"claim":"string","status":"supported|unsupported",'
            '"cited_chunk_ids":[],"reason":"string"}]}'
        )
        data = self.judge_llm.chat_json(messages, schema_hint=schema_hint)
        raw_verdicts = (data or {}).get("verdicts") or []
        # judge 是个 LLM，schema_hint 是**请求**不是**保证**。它完全可能吐出
        # ["supported", "unsupported"] 这样的字符串列表——从前这里直接 .get()，
        # 会以 AttributeError 冲出验证层，把已经生成好的答案整个丢掉并触发二轮重算
        # （双倍计费、零输出）。非 dict 的条目直接忽略，按"没给判定"处理。
        by_claim = {
            str(v.get("claim", "")): v for v in raw_verdicts if isinstance(v, dict)
        }
        out: dict[str, str] = {}
        matched = 0
        for idx, (claim, _ids) in enumerate(claims_to_judge):
            verdict = by_claim.get(claim)
            if verdict is None and idx < len(raw_verdicts):
                candidate = raw_verdicts[idx]
                verdict = candidate if isinstance(candidate, dict) else None
            if verdict is not None:
                matched += 1
            # 缺省值必须是 neutral。从前是 supported(=1.0)，这是整条验证链上
            # **唯一的 fail-open**：judge 漏判了某条声明、或返回体缺 status 字段，
            # 那条声明就自动拿到满分蕴含，带着一个绿色的引用发给用户。
            # "没判"不是"判过且通过"。
            out[claim] = str((verdict or {}).get("status") or "neutral")
        if claims_to_judge and matched == 0:
            # 一条都对不上，说明返回体整个不可用（模型跑飞了）。
            # 这不是"判定为中立"，是"根本没判"——必须走降级路径传 None 给弃权门，
            # 否则整批声明会拿到 neutral(0.5) < 阈值 0.6，把 judge 故障
            # 伪装成"证据不足"，在检索和生成都已付费之后强制弃权。
            raise ValueError(
                f"judge 返回体不含任何可用判定（{len(raw_verdicts)} 条原始条目）"
            )
        return out

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    def _load_template(self) -> str:
        """懒加载 L3 模板（首次使用时读盘并缓存）。"""
        if self._template_cache is not None:
            return self._template_cache
        path = Path(self.groundedness_template)
        if not path.is_absolute():
            path = _PROJECT_ROOT / path
        try:
            self._template_cache = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(
                f"无法读取 groundedness 模板 {path}: {exc}"
            ) from exc
        return self._template_cache

    @staticmethod
    def _clean_claim(claim: str) -> str:
        """剥离声明中的 [N] 引用标记并去除首尾空白。

        声明文本与引用编号分离：L3 模板中编号由 cited ids 单独列出，
        嵌入对齐也基于纯声明文本。
        """
        return _CITATION_RE.sub("", claim).strip()

    @staticmethod
    def _dedupe_evidence(
        evidence_chunks: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        """按 chunk_id 去重（保持出现顺序），作为编号基准。"""
        seen: set[str] = set()
        out: list[RetrievedChunk] = []
        for rc in evidence_chunks:
            if rc.chunk.chunk_id not in seen:
                seen.add(rc.chunk.chunk_id)
                out.append(rc)
        return out

    @staticmethod
    def _index_of_chunk(chunk_id: str, evidence: list[RetrievedChunk]) -> int:
        """chunk_id -> 证据编号（1 基）；未找到返回 0。"""
        for i, rc in enumerate(evidence, 1):
            if rc.chunk.chunk_id == chunk_id:
                return i
        return 0

    def _sample_citations(self, citations: list[Citation]) -> list[Citation]:
        """确定性抽样：按 ``sample_ratio`` 均匀取步长，保证可复现。"""
        if self.sample_ratio >= 1.0:
            return list(citations)
        step = max(1, int(round(1.0 / self.sample_ratio)))
        return citations[::step]


def create_verifier(settings=None) -> CitationVerifier:
    """由配置构造验证器（读取各业务槽位与 verify 段）。

    - judge 槽位：``settings.llm.judge``；
    - 验证强度：``settings.verify``（entailment_mode / strict / sample_ratio）；
    - Milvus / 嵌入均为惰性初始化（构造不联网、不加载模型），
      因此本函数可安全离线调用。

    Args:
        settings: Settings 实例；None 时取进程级单例
            :func:`~config.settings.get_settings`。

    Returns:
        :class:`CitationVerifier`。
    """
    settings = settings or get_settings()
    mode, strict, ratio = resolve_verify_settings(settings)
    return CitationVerifier(
        milvus=RagMilvusClient(settings.milvus),
        judge_llm=create_client(settings.llm.judge),
        embedder=None,  # 事后指派按需显式注入（避免隐式加载模型）
        groundedness_template=None,
        entailment_mode=mode,
        strict=strict,
        sample_ratio=ratio,
    )


def resolve_verify_settings(settings) -> tuple[str, bool, float]:
    """把 ``settings.verify`` 解析为 CitationVerifier 的三个构造参数。

    配置层没有类型/取值校验（``config.settings`` 全部是裸 str/float），
    非法值若直接传给 :class:`CitationVerifier` 会在构造时抛 ValueError，
    进而拖垮整条管线的装配。这里统一做一次兜底：非法值回退到安全默认
    （最严格的全量 LLM 评审），保证配置写错只会退化为「更慢但更严」，
    绝不会让服务起不来。

    Returns:
        ``(entailment_mode, strict, sample_ratio)``
    """
    cfg = getattr(settings, "verify", None)
    mode = str(getattr(cfg, "entailment_mode", "llm") or "llm").strip().lower()
    if mode not in _ENTAILMENT_MODES:
        mode = "llm"
    if mode == "nli":
        # `nli` 在 _ENTAILMENT_MODES 里（它是预留模式），于是它是唯一一个
        # **通过了配置校验、却会让每个请求都抛异常**的取值：verify() 一进 L3
        # 就 raise NliNotImplemented。本函数的全部意义就是"配置写错只会更慢更严，
        # 不会让服务坏掉"，放它过去等于自我否定。
        _logger.warning("verify.entailment_mode=nli 尚未实现，已回退为 llm")
        mode = "llm"
    strict = bool(getattr(cfg, "strict", True))
    try:
        ratio = float(getattr(cfg, "sample_ratio", 1.0))
    except (TypeError, ValueError):
        ratio = 1.0
    if not (0.0 < ratio <= 1.0):
        ratio = 1.0
    return mode, strict, ratio


__all__ = [
    "CitationVerifier",
    "VerificationResult",
    "NliNotImplemented",
    "create_verifier",
    "resolve_verify_settings",
]
