"""切分路由冒烟：递归固定 / 父子 / QA 对 三模式 + 路由决策。

运行：python scripts/smoke_chunking.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from indexing.chunker import StructureAwareChunker  # noqa: E402
from indexing.chunker_qa import CsvQaChunker  # noqa: E402
from indexing.chunker_recursive import RecursiveChunker  # noqa: E402
from indexing.chunking_router import ChunkingRouter  # noqa: E402

passed = 0
failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} {detail}")


LONG_TEXT = "第一章 概述\n\n" + "这是第一段内容。\n\n" * 40 + "第二章 细节\n\n" + "这是第二段内容。\n\n" * 40

print("== 1. RecursiveChunker（简单文档：递归固定切分） ==")
rc = RecursiveChunker(max_chunk_chars=300, overlap_chars=20, min_chunk_chars=50)
r_chunks = rc.chunk_document("doc-r", LONG_TEXT)
check("产出多个扁平 chunk", len(r_chunks) >= 2, str(len(r_chunks)))
check("无父块（parent_chunk_id 全空）", all(c.parent_chunk_id is None for c in r_chunks))
check("chunk 大小 <= max", all(len(c.text) <= 300 for c in r_chunks), str(max(len(c.text) for c in r_chunks)))
check("chunk_mode=recursive 标记", all(c.metadata.get("chunk_mode") == "recursive" for c in r_chunks))
check("chunk_id 确定性", r_chunks[0].chunk_id.startswith("doc-r::0000::"))

print("== 2. StructureAwareChunker（难文档：父子切分） ==")
pc = StructureAwareChunker(max_chunk_chars=300, min_chunk_chars=50)
p_chunks = pc.chunk_document("doc-p", LONG_TEXT)
parents = [c for c in p_chunks if c.metadata.get("is_parent")]
children = [c for c in p_chunks if not c.metadata.get("is_parent")]
check("产出父块 + 子块", len(parents) > 0 and len(children) > 0, f"parent={len(parents)} child={len(children)}")
check("子块 parent_chunk_id 指向父块", all(c.parent_chunk_id in {p.chunk_id for p in parents} for c in children))

print("== 3. CsvQaChunker（CSV -> QA 对） ==")
CSV_TEXT = "季度,请求量,弃权率\nQ1,12340,2.1%\nQ2,18905,1.4%\nQ3,20110,1.8%\n"
qa = CsvQaChunker()
q_chunks = qa.chunk_document("doc-c", CSV_TEXT)
check("每行一个 QA chunk（3 行数据）", len(q_chunks) == 3, str(len(q_chunks)))
check("text 含问与答", "问：" in q_chunks[0].text and "答：" in q_chunks[0].text, q_chunks[0].text[:80])
check("问题含首列值 Q1", "Q1" in q_chunks[0].text, q_chunks[0].text[:60])
check("chunk_level=qa 标记", q_chunks[0].metadata.get("chunk_level") == "qa")
check("metadata.row 存原始行", q_chunks[0].metadata.get("row", {}).get("季度") == "Q1", str(q_chunks[0].metadata.get("row")))
check("metadata.headers 存表头", q_chunks[0].metadata.get("headers") == ["季度", "请求量", "弃权率"])

print("== 4. CsvQaChunker（Markdown 表格输入：xlsx 经 MinerU 的形态） ==")
MD_TABLE = "| 季度 | 请求量 |\n| --- | --- |\n| Q1 | 12340 |\n| Q2 | 18905 |\n"
md_chunks = qa.chunk_document("doc-m", MD_TABLE)
check("Markdown 表格解析 2 行", len(md_chunks) == 2, str(len(md_chunks)))
check("行值正确", md_chunks[1].metadata.get("row", {}).get("请求量") == "18905")

print("== 5. ChunkingRouter 决策（auto） ==")
router = ChunkingRouter(mode="auto", simple_max_chars=300)
check("csv -> qa", router.decide("csv", CSV_TEXT) == "qa")
check("excel -> qa", router.decide("xlsx", MD_TABLE) == "qa")
check("短文本无版面 -> recursive", router.decide("txt", "短文档内容。") == "recursive")
check("长文本 -> parent_child", router.decide("txt", LONG_TEXT) == "parent_child")
check("markdown 长文档 -> parent_child", router.decide("markdown", LONG_TEXT) == "parent_child")

print("== 6. 显式模式覆盖 + doc 级一致性 ==")
router_rc = ChunkingRouter(mode="recursive")
check("mode=recursive 覆盖决策", router_rc.decide("csv", CSV_TEXT) == "recursive")
routed = router.chunk_document("doc-x", CSV_TEXT, doc_type="csv")
check("auto 路由 csv -> QA 产出", all(c.metadata.get("chunk_level") == "qa" for c in routed))
chunks_a = router.chunk_with_mode("recursive", "doc-s1", LONG_TEXT[:1000])
chunks_b = router.chunk_with_mode("recursive", "doc-s2", LONG_TEXT[400:900], start_seq=len(chunks_a))
check("chunk_with_mode 跨段 seq 连续", chunks_b[0].metadata["chunk_index"] == len(chunks_a))

print()
print(f"结果：{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
