"""元数据过滤基础设施：系统保留键 + Milvus 表达式白名单校验器。

设计目标（manual / automatic 双通道共用）：
- manual：调用方显式传入过滤条件（dict 或表达式）；
- automatic：LLM 按 schema 提示生成过滤表达式（core.metadata 把 schema
  转成提示文本），生成结果**必须**经 validate_filter_expr 校验
  后才能拼入检索请求——键白名单 + 值类型 + 递归下降解析，杜绝注入。

系统保留键（写入路径自动生成，过滤白名单恒包含）：

    tenant_id, dataset_id, app_id, workflow_id, doc_id, doc_type,
    source, page, position, chunk_level, language

用户字段来自 MetadataField 注册表（models.orm），每知识库独立 schema。
"""
from __future__ import annotations

import re
from typing import Any


class FilterValidationError(ValueError):
    """过滤表达式校验失败（非法键 / 类型不匹配 / 语法错误 / 注入尝试）。"""


# 系统保留键 -> 值类型（Milvus 表达式中的字面量形态）
SYS_KEYS: dict[str, str] = {
    "tenant_id": "string",
    "dataset_id": "string",
    "app_id": "string",
    "workflow_id": "string",
    "doc_id": "string",
    "doc_type": "string",
    "source": "string",
    "page": "number",
    "position": "number",
    "chunk_level": "string",  # doc | segment | child
    "language": "string",
}

VALUE_TYPES: tuple[str, ...] = ("string", "number", "bool", "date")

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_NUMBER_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def schema_to_prompt_text(fields: list[dict[str, Any]]) -> str:
    """把元数据 schema 转成 LLM 提示文本（automatic 通道的 schema 注入）。"""
    lines = ["可用过滤键（值类型）："]
    for key, vtype in SYS_KEYS.items():
        lines.append(f"  {key}: {vtype}（系统）")
    for f in fields:
        lines.append(f"  {f['key']}: {f['value_type']}（{f.get('label') or f['key']}）")
    lines.append(
        "语法：key == 'value' / key in ['a','b'] / key >= 1 并用 &&、||、! 组合；"
        "仅允许上述键，输出单行表达式。"
    )
    return "\n".join(lines)


class _Tokenizer:
    """表达式分词器（不依赖第三方）。"""

    def __init__(self, expr: str):
        self.expr = expr
        self.pos = 0
        self.tokens: list[tuple[str, str]] = []

    def tokenize(self) -> list[tuple[str, str]]:
        while self.pos < len(self.expr):
            ch = self.expr[self.pos]
            if ch.isspace():
                self.pos += 1
                continue
            if self.expr.startswith("&&", self.pos) or self.expr.startswith("||", self.pos):
                self.tokens.append(("OP", self.expr[self.pos : self.pos + 2]))
                self.pos += 2
                continue
            if ch in "()![]":
                self.tokens.append(("SYM", ch))
                self.pos += 1
                continue
            if ch == ",":
                self.tokens.append(("SYM", ","))
                self.pos += 1
                continue
            two = self.expr[self.pos : self.pos + 2]
            if two in ("==", "!=", ">=", "<="):
                self.tokens.append(("CMP", two))
                self.pos += 2
                continue
            if ch in "><":
                self.tokens.append(("CMP", ch))
                self.pos += 1
                continue
            if ch == "'" or ch == '"':
                quote = ch
                end = self.pos + 1
                while end < len(self.expr) and self.expr[end] != quote:
                    end += 1
                if end >= len(self.expr):
                    raise FilterValidationError("字符串未闭合")
                self.tokens.append(("STR", self.expr[self.pos : end + 1]))
                self.pos = end + 1
                continue
            if ch.isalpha() or ch == "_":
                end = self.pos
                while end < len(self.expr) and (self.expr[end].isalnum() or self.expr[end] == "_"):
                    end += 1
                word = self.expr[self.pos : end]
                if word in ("in", "not"):
                    self.tokens.append(("KEYWORD", word))
                elif word in ("true", "false", "TRUE", "FALSE"):
                    self.tokens.append(("BOOL", word))
                elif _IDENT_RE.match(word):
                    self.tokens.append(("IDENT", word))
                else:
                    raise FilterValidationError(f"非法标识符: {word!r}")
                self.pos = end
                continue
            if ch.isdigit() or ch in "+-.":
                end = self.pos
                while end < len(self.expr) and (
                    self.expr[end].isdigit() or self.expr[end] in "+-."
                ):
                    end += 1
                num = self.expr[self.pos : end]
                if not _NUMBER_RE.match(num):
                    raise FilterValidationError(f"非法数字: {num!r}")
                self.tokens.append(("NUM", num))
                self.pos = end
                continue
            raise FilterValidationError(f"非法字符: {ch!r}")
        return self.tokens


class _Parser:
    """递归下降解析器：校验语法 + 键白名单 + 值类型，输出规范化表达式。"""

    def __init__(self, tokens: list[tuple[str, str]], whitelist: dict[str, str]):
        self.tokens = tokens
        self.pos = 0
        self.whitelist = whitelist
        self.out: list[str] = []

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def next(self) -> tuple[str, str]:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def parse(self) -> str:
        self.parse_or()
        if self.peek() is not None:
            raise FilterValidationError("表达式存在多余内容")
        return " ".join(self.out)

    def parse_or(self) -> None:
        self.parse_and()
        while self.peek() and self.peek()[0] == "OP" and self.peek()[1] == "||":
            self.next()
            self.out.append("||")
            self.parse_and()

    def parse_and(self) -> None:
        self.parse_unary()
        while self.peek() and self.peek()[0] == "OP" and self.peek()[1] == "&&":
            self.next()
            self.out.append("&&")
            self.parse_unary()

    def parse_unary(self) -> None:
        tok = self.peek()
        if tok and tok[0] == "SYM" and tok[1] == "!":
            self.next()
            self.out.append("not")
            self.parse_unary()
        elif tok and tok[0] == "SYM" and tok[1] == "(":
            self.next()
            self.out.append("(")
            self.parse_or()
            close = self.next()
            if close[0] != "SYM" or close[1] != ")":
                raise FilterValidationError("缺少右括号")
            self.out.append(")")
        else:
            self.parse_condition()

    def parse_condition(self) -> None:
        key_tok = self.next()
        if key_tok[0] != "IDENT":
            raise FilterValidationError(f"期望过滤键，得到 {key_tok!r}")
        key = key_tok[1]
        if key not in self.whitelist:
            raise FilterValidationError(
                f"非法过滤键: {key!r}（不在 schema 白名单内）"
            )
        vtype = self.whitelist[key]
        self.out.append(key)

        tok = self.next()
        if tok[0] == "CMP":
            self.out.append(tok[1])
            value = self.next()
            self._check_value(key, vtype, value)
            self.out.append(value[1])
            return
        if tok[0] == "KEYWORD" and tok[1] in ("in", "not"):
            self.out.append(tok[1])
            if tok[1] == "not":
                nxt = self.next()
                if nxt[0] != "KEYWORD" or nxt[1] != "in":
                    raise FilterValidationError("期望 'not in'")
                self.out.append("in")
            bracket = self.next()
            if bracket[0] != "SYM" or bracket[1] != "[":
                raise FilterValidationError("in 列表需要方括号")
            self.out.append("[")
            values: list[str] = []
            while True:
                value = self.next()
                self._check_value(key, vtype, value)
                values.append(value[1])
                sep = self.next()
                if sep[0] == "SYM" and sep[1] == "]":
                    break
                if sep[0] != "SYM" or sep[1] != ",":
                    raise FilterValidationError("in 列表需要逗号分隔")
            self.out.append(", ".join(values))
            self.out.append("]")
            return
        raise FilterValidationError(f"期望比较运算符，得到 {tok!r}")

    def _check_value(self, key: str, vtype: str, tok: tuple[str, str]) -> None:
        kind, raw = tok
        if vtype == "string":
            if kind != "STR":
                raise FilterValidationError(f"键 {key!r} 期望字符串值，得到 {raw!r}")
            return
        if vtype == "number":
            if kind != "NUM":
                raise FilterValidationError(f"键 {key!r} 期望数值，得到 {raw!r}")
            return
        if vtype == "bool":
            if kind != "BOOL":
                raise FilterValidationError(f"键 {key!r} 期望布尔值，得到 {raw!r}")
            return
        # date：按字符串字面量（YYYY-MM-DD 形式）
        if kind != "STR":
            raise FilterValidationError(f"键 {key!r} 期望日期字符串，得到 {raw!r}")


def validate_filter_expr(
    expr: str,
    user_fields: list[dict[str, Any]] | None = None,
) -> str:
    """校验过滤表达式（键白名单 + 值类型 + 语法），返回规范化表达式。

    Args:
        expr: Milvus 表达式子集（== != > >= < <= in not in && || ! 括号）。
        user_fields: 用户元数据 schema（[{key, value_type}, ...]，
            来自 catalog.list_metadata_fields）。

    Returns:
        规范化后的表达式（可直接拼入 Milvus filter）。

    Raises:
        FilterValidationError: 非法键 / 类型不匹配 / 语法错误 / 注入尝试。
    """
    if expr is None or not str(expr).strip():
        raise FilterValidationError("表达式为空")
    whitelist = dict(SYS_KEYS)
    for f in user_fields or []:
        key = str(f.get("key", ""))
        vtype = str(f.get("value_type", "string"))
        if key and vtype in VALUE_TYPES:
            whitelist[key] = vtype
    tokens = _Tokenizer(str(expr).strip()).tokenize()
    if not tokens:
        raise FilterValidationError("表达式为空")
    return _Parser(tokens, whitelist).parse()


def filters_from_dict(
    conditions: dict[str, Any],
    user_fields: list[dict[str, Any]] | None = None,
) -> str | None:
    """把显式条件 dict 转成已校验的 Milvus 表达式（manual 通道便捷入口）。

    conditions: {"doc_type": "pdf", "year": 2024, "tags": ["a", "b"]}
        -> doc_type == 'pdf' && year == 2024 && tags in ['a', 'b']

    Returns:
        规范化表达式；空 conditions 返回 None。
    """
    if not conditions:
        return None
    parts: list[str] = []
    for key, value in conditions.items():
        if isinstance(value, (list, tuple)):
            quoted = ", ".join(_quote(v) for v in value)
            parts.append(f"{key} in [{quoted}]")
        else:
            parts.append(f"{key} == {_quote(value)}")
    expr = " && ".join(parts)
    return validate_filter_expr(expr, user_fields)


def _quote(value: Any) -> str:
    """值字面量化（数字直接，其余加单引号并转义）。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "\\'") + "'"


__all__ = [
    "SYS_KEYS",
    "VALUE_TYPES",
    "FilterValidationError",
    "schema_to_prompt_text",
    "validate_filter_expr",
    "filters_from_dict",
]
