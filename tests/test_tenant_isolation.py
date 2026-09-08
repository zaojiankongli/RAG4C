from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.graph_store import _quote_string
from core.milvus_client import _quote_expr_str
from server.app import QueryRequest

# 这一整个文件是为一条已确认的越权缺陷做的回归护栏：
# 旧的 _escape_expr_str 只转义 \ 和 '，但 7 个调用点里有 6 个把结果插进
# **双引号**字面量，于是 tenant_id 里的一个 " 就能闭合字面量注入布尔子句，
# 造成跨租户读（/api/query）与跨租户删（delete_by_doc_id）。
# 主防线是引号收进 _quote_expr_str，第二道是请求层字符集校验。两道都要测。

#: 能把 `tenant_id == <值>` 变成恒真式的经典载荷。
TAUTOLOGY_PAYLOAD = 'zzz" or tenant_id != "zzz'

INJECTION_PAYLOADS = [
    TAUTOLOGY_PAYLOAD,
    'x" or "1"=="1',
    "y' or '1'='1",
    'a\\"b',
    'z"',
]


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_quote_expr_str_keeps_payload_inside_the_literal(payload: str) -> None:
    expr = f"tenant_id == {_quote_expr_str(payload)}"

    # 整个表达式必须是「字段 == 一个字面量」，字面量之外不能出现裸的引号。
    assert expr.startswith('tenant_id == "')
    assert expr.endswith('"')
    body = expr[len('tenant_id == "') : -1]
    # 字面量内部每一个 " 都必须被反斜杠转义（不能出现未转义的闭合引号）。
    assert '"' not in body.replace('\\"', "")


def test_quote_expr_str_does_not_produce_tautology() -> None:
    expr = f"tenant_id == {_quote_expr_str(TAUTOLOGY_PAYLOAD)}"

    assert expr == 'tenant_id == "zzz\\" or tenant_id != \\"zzz"'


@pytest.mark.parametrize(
    "value",
    [*INJECTION_PAYLOADS, "tenant-01", "财务部", "acme corp", "", "a\\b"],
)
def test_quote_expr_str_matches_graph_store_implementation(value: str) -> None:
    # 同一套语义在仓库里有两处实现，任何一处漂移都会让另一处的调用点重新暴露。
    assert _quote_expr_str(value) == _quote_string(value)


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_query_request_rejects_injection_in_tenant_id(payload: str) -> None:
    with pytest.raises(ValidationError):
        QueryRequest(query="q", tenant_id=payload)


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_query_request_rejects_injection_in_dataset_id(payload: str) -> None:
    with pytest.raises(ValidationError):
        QueryRequest(query="q", dataset_id=payload)


@pytest.mark.parametrize("tenant", ["tenant-01", "财务部", "acme corp", "a.b:c_d"])
def test_query_request_accepts_legitimate_tenant_ids(tenant: str) -> None:
    # 字符集校验刻意用「排除危险字符」而非白名单：白名单会把中文租户名、
    # 带空格的名字一并拒掉，那是引入回归而不是修复安全问题。
    assert QueryRequest(query="q", tenant_id=tenant).tenant_id == tenant


@pytest.mark.parametrize("dataset", ["", None, "kb-01", "知识库"])
def test_query_request_accepts_optional_dataset_id(dataset: str | None) -> None:
    # 空串是有意义的取值（= 不限知识库、全域检索），不能被字符集校验顺手拒掉。
    assert QueryRequest(query="q", dataset_id=dataset).dataset_id == dataset
