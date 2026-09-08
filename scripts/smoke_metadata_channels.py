"""元数据双通道冒烟（stub LLM）：通道 A 表达式生成 + 通道 B 自动打标签。

运行：python scripts/smoke_metadata_channels.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from retrieval.auto_filter import AutoFilter  # noqa: E402
from indexing.auto_tagger import AutoTagger  # noqa: E402

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


class StubLLM:
    """按脚本返回预设输出的假 LLM。"""

    def __init__(self, reply: str):
        self.reply = reply

    def chat(self, messages=None, json_mode=False):
        return self.reply


USER_FIELDS = [
    {"key": "department", "value_type": "string", "source": "auto", "label": "部门"},
    {"key": "year", "value_type": "number", "source": "manual", "label": "年份"},
]

print("== 1. 通道 A：合法表达式生成并通过校验 ==")
af = AutoFilter(llm_client=StubLLM("department == 'fin' && year >= 2024"))
expr = af.generate("财务部门 2024 年以后的报销制度", USER_FIELDS)
check("生成合法表达式", expr == "department == 'fin' && year >= 2024", str(expr))

print("== 2. 通道 A：LLM 生成注入表达式 -> 降级 None ==")
af2 = AutoFilter(llm_client=StubLLM("password == 'admin' || 1 == 1"))
check("注入降级 None", af2.generate("查询", USER_FIELDS) is None)

print("== 3. 通道 A：NONE / 空输出 -> 不过滤 ==")
check("NONE -> None", AutoFilter(llm_client=StubLLM("NONE")).generate("查询", USER_FIELDS) is None)
check("空输出 -> None", AutoFilter(llm_client=StubLLM("  ")).generate("查询", USER_FIELDS) is None)

print("== 4. 通道 A：LLM 失败 -> 静默 None ==")
class BoomLLM:
    def chat(self, messages=None, json_mode=False):
        raise RuntimeError("LLM down")

check("LLM 异常 -> None", AutoFilter(llm_client=BoomLLM()).generate("查询", USER_FIELDS) is None)

print("== 5. 通道 B：合法标签保留 + 非法键丢弃 ==")
tag = AutoTagger(
    llm_client=StubLLM('{"topic": "报销", "department": "fin", "hacked_key": "x", "year": "not-a-number"}'),
    user_field_types={"year": "number"},
)
out = tag.tag("员工报销需在三日内提交发票")
check("合法标签保留", out.get("topic") == "报销" and out.get("department") == "fin", str(out))
check("非法键丢弃", "hacked_key" not in out, str(out))
check("类型不匹配丢弃", "year" not in out, str(out))

print("== 6. 通道 B：非法 JSON -> 空标签 ==")
out2 = AutoTagger(llm_client=StubLLM("这不是 JSON")).tag("文本")
check("非法输出 -> 空 dict", out2 == {}, str(out2))

print("== 7. 通道 B：tag_chunks 原地写入 ==")
class FakeChunk:
    def __init__(self, text: str):
        self.text = text
        self.metadata: dict = {"existing": 1}

chunks = [FakeChunk("报销流程说明")]
AutoTagger(llm_client=StubLLM('{"topic": "报销"}')).tag_chunks(chunks)
check("标签写入 chunk.metadata", chunks[0].metadata.get("topic") == "报销", str(chunks[0].metadata))
check("原有 metadata 保留", chunks[0].metadata.get("existing") == 1, str(chunks[0].metadata))

print()
print(f"结果：{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
