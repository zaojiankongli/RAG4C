"""真实全链路联调：入库 -> 检索 -> 生成 -> 引用验证 -> 弃权 -> 知识库隔离。

与 ``scripts/smoke_*.py`` 的区别：冒烟脚本用桩件在离线环境验证「接线正确」，
本脚本**不使用任何桩件**，打真实的 Milvus / 嵌入 / 重排 / LLM，验证「链路真的能跑」。

运行前提（用 ``scripts/check_services.py`` 与 ``scripts/check_milvus.py`` 自检）：
  - Milvus 可达（``RAG4C_MILVUS_URI``）
  - 嵌入与重排服务可达
  - LLM 槽位可达（默认本机 Ollama）

运行：
    python scripts/e2e_live.py

脚本自带清理：无论成功失败，结束时都会删除本次写入的临时文档，
不会在业务知识库里留下残留（用独立的 dataset_id + 随机 doc_id 双重隔离）。
"""
from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from config.settings import get_settings  # noqa: E402
from core.embedding import create_embedder  # noqa: E402
from core.milvus_client import RagMilvusClient  # noqa: E402
from indexing.ingest import IngestPipeline  # noqa: E402
from rag import answer_query  # noqa: E402

# --------------------------------------------------------------------------- #
# 断言计数
# --------------------------------------------------------------------------- #
_passed = 0
_failed = 0


def check(name: str, cond: bool, detail: str = "") -> bool:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {name}")
    else:
        _failed += 1
        print(f"  [FAIL] {name} {detail}")
    return bool(cond)


def info(msg: str) -> None:
    print(f"  [INFO] {msg}")


# --------------------------------------------------------------------------- #
# 测试语料：内容是自造的，确保答案只可能来自本文档，不可能来自模型先验。
# 「归墟三号」「17 分钟」「4200 元」这类实体在任何公开语料里都不存在，
# 模型答对就只能是检索到了证据 —— 这是有据可依的唯一硬证明。
# --------------------------------------------------------------------------- #
_DOC_TEXT = """# 归墟三号内部运维手册

## 一、值班与响应

归墟三号集群实行三班倒值班制度。一线值班工程师在收到 P0 级告警后，必须在
17 分钟内完成首次响应并在值班群同步初步结论。若 17 分钟内未响应，告警自动
升级至二线，并同时通知值班经理。P1 级告警的首次响应时限为 2 小时，P2 级为
一个工作日。所有响应时间以告警平台记录的时间戳为准，不接受口头追认。

## 二、变更窗口

生产环境变更窗口固定为每周二、周四的 22:00 至次日 02:00。窗口外变更一律
需要走紧急变更审批，由值班经理与架构组组长双签。节假日前一个工作日冻结
所有非紧急变更，冻结期通常为三天。灰度发布必须先在归墟一号集群完成至少
24 小时的观察期，观察期内错误率低于万分之五方可推进到归墟三号。

## 三、备件与报销

现场备件采购单笔金额不超过 4200 元的，由值班经理直接审批，财务在三个
工作日内完成付款。超过 4200 元的采购需提交采购委员会评审，评审每两周
召开一次。差旅报销统一走飞书审批流，票据需在行程结束后 30 天内提交，
逾期需附情况说明并由部门负责人签字。

## 四、数据保留

告警原始日志保留 90 天，聚合后的指标数据保留 400 天，审计日志保留 5 年。
超期数据由归档任务自动清理，清理动作本身也会写入审计日志。任何人不得
手工删除审计日志，违规操作将触发安全告警并上报合规组。
"""

_DATASET = "e2e-live-probe"
_OTHER_DATASET = "e2e-live-absent"
_DOC_ID = f"e2e-live-{uuid.uuid4().hex[:10]}"


def main() -> int:
    settings = get_settings()
    print("=" * 68)
    print("RAG4C 真实全链路联调（无桩件）")
    print("=" * 68)
    info(f"Milvus     : {settings.milvus.uri}")
    info(f"集合       : {settings.milvus.collection_name}")
    info(f"嵌入       : {settings.embedding.provider} / {settings.embedding.model}")
    info(f"重排       : {settings.reranker.provider} / {settings.reranker.model}")
    info(f"生成槽位   : {settings.llm.generation.model}")
    info(f"知识库     : {_DATASET}   文档: {_DOC_ID}")
    print()

    ingest = IngestPipeline(
        embedder=create_embedder(settings.embedding),
        milvus=RagMilvusClient(settings.milvus),
    )

    try:
        # ---------------------------------------------------------------- #
        print("== 1. 入库 ==")
        t0 = time.perf_counter()
        ingest.ensure_collection()
        info(f"集合就绪（{(time.perf_counter() - t0) * 1000:.0f}ms）")

        t0 = time.perf_counter()
        n = ingest.add_document(
            doc_id=_DOC_ID,
            text=_DOC_TEXT,
            source="e2e",
            metadata={"kind": "runbook"},
            dataset_id=_DATASET,
        )
        info(f"写入 {n} 个 chunk（{(time.perf_counter() - t0) * 1000:.0f}ms）")
        if not check("入库产出至少 1 个 chunk", n > 0, f"chunk_count={n}"):
            return 1

        # Milvus 写入到可检索之间存在可见性延迟，轮询而非固定 sleep：
        # 固定 sleep 要么白等、要么在慢环境下仍然抢跑。
        milvus = RagMilvusClient(settings.milvus)
        visible = 0
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                rows = milvus.query_chunks_by_doc(_DOC_ID)
                visible = len(rows)
                if visible >= n:
                    break
            except Exception as exc:  # noqa: BLE001 - 可见性探测失败不算硬错误
                info(f"可见性探测异常（继续重试）: {exc}")
            time.sleep(1.0)
        check("写入的 chunk 在检索侧可见", visible >= n, f"visible={visible}/{n}")

        # ---------------------------------------------------------------- #
        print()
        print("== 2. 有据可依的问答（应答出文档中的事实并带引用）==")
        t0 = time.perf_counter()
        r = answer_query("归墟三号的 P0 告警首次响应时限是多久？", dataset_id=_DATASET)
        ms = (time.perf_counter() - t0) * 1000
        info(f"耗时 {ms:.0f}ms  route={r.route}  abstained={r.abstained}")
        info(f"答案: {r.answer[:200]}")

        answered = check("未弃权", r.abstained is False, f"traces={r.traces[-3:]}")
        if answered:
            check(
                "答案命中文档事实「17 分钟」",
                "17" in r.answer,
                f"answer={r.answer[:200]}",
            )
            check("产出了引用条目", len(r.citations) > 0, f"citations={len(r.citations)}")
            ev = (r.verdict or {}).get("evidence_chunks") or []
            # evidence_chunks 是 model_dump 后的 dict 列表，不是 Chunk 对象——
            # 这里用 getattr 取会恒为默认值，断言看似通过实则什么都没验。
            check("证据来自本次入库的文档",
                  any(c.get("doc_id") == _DOC_ID for c in ev) if ev else False,
                  f"evidence={len(ev)} doc_ids={[c.get('doc_id') for c in ev][:3]}")

        # ---------------------------------------------------------------- #
        print()
        print("== 3. 第二个事实点（验证不是蒙对的）==")
        r2 = answer_query("现场备件采购单笔多少金额以内由值班经理直接审批？", dataset_id=_DATASET)
        info(f"abstained={r2.abstained}  答案: {r2.answer[:160]}")
        if r2.abstained is False:
            check("答案命中文档事实「4200」", "4200" in r2.answer, f"answer={r2.answer[:200]}")
        else:
            check("第二个事实点未弃权", False, f"traces={r2.traces[-3:]}")

        # ---------------------------------------------------------------- #
        print()
        print("== 4. 无据可依时弃权（不许编）==")
        r3 = answer_query(
            "归墟三号集群的机房电费单价是多少钱一度？", dataset_id=_DATASET
        )
        info(f"abstained={r3.abstained}  答案: {r3.answer[:160]}")
        check(
            "文档未覆盖的问题应弃权或不给出杜撰数字",
            r3.abstained is True or not r3.answer.strip(),
            f"answer={r3.answer[:200]}",
        )

        # ---------------------------------------------------------------- #
        print()
        print("== 5. 知识库隔离（dataset_id 过滤真实生效）==")
        # 同一个问题，换一个不存在的知识库：必须检索不到、必须弃权。
        # 这条断言是 dataset_id 全链路打通的硬证明——它此前只写不读，
        # 换库不影响结果，本断言必然失败。
        r4 = answer_query(
            "归墟三号的 P0 告警首次响应时限是多久？", dataset_id=_OTHER_DATASET
        )
        info(f"dataset={_OTHER_DATASET}  abstained={r4.abstained}  答案: {r4.answer[:120]}")
        check(
            "指定空知识库时检索不到证据 -> 弃权",
            r4.abstained is True,
            f"answer={r4.answer[:200]}, traces={r4.traces[-3:]}",
        )

        # 不指定知识库时应能检索到（全域检索语义）
        r5 = answer_query("归墟三号的 P0 告警首次响应时限是多久？")
        info(f"dataset=<全域>  abstained={r5.abstained}")
        check(
            "不指定知识库时全域检索仍可命中",
            r5.abstained is False and "17" in r5.answer,
            f"abstained={r5.abstained}, answer={r5.answer[:160]}",
        )

    finally:
        print()
        print("== 清理 ==")
        try:
            removed = ingest.delete_document(_DOC_ID)
            info(f"已删除 {removed} 个 chunk（doc_id={_DOC_ID}）")
        except Exception as exc:  # noqa: BLE001 - 清理失败只提示，不改变结论
            print(f"  [WARN] 清理失败，请手工删除 doc_id={_DOC_ID}: {exc}")

    print()
    print("=" * 68)
    if _failed:
        print(f"全链路联调失败：{_passed} passed, {_failed} failed")
        return 1
    print(f"全链路联调通过：{_passed} passed")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
