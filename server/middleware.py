"""桥服务中间件：访问日志 + 全局异常 JSON 错误契约。

错误契约（前端 client.ts 按 error.code 分支处理）::

    {"error": {"code": "internal_error", "message": "..."}}

- HTTPException / 参数校验错误（422）同样转成该结构；
- 未捕获异常记录 exception 日志（含 query_id），响应 500 JSON；
- 慢请求（>10s）单独 WARNING 告警。
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from typing import Any, get_args, get_origin

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.observability import get_logger

_logger = get_logger("server.http")

SLOW_REQUEST_MS = 10_000.0

#: 访问日志是否输出结构化 JSON 单行（默认关，保持人类可读单行）。
_ACCESS_LOG_JSON = os.environ.get("RAG4C_ACCESS_LOG_JSON", "").strip() in {"1", "true", "yes"}

#: 请求体大小上限（字节）。1 MiB 对本服务的所有 JSON 接口都绰绰有余——
#: 最大的 query 字段本身才限 2000 字符——但足以挡住"发个几百 MB 的 body
#: 把内存打爆"这类最省事的攻击。
MAX_BODY_BYTES = 1024 * 1024


async def limit_body_size(request: Request, call_next):
    """按 Content-Length 拒绝过大的请求体。

    为什么要在中间件层挡：Pydantic 的 ``max_length`` 是在**读完整个 body 并
    解析成对象之后**才校验的。也就是说一个 500MB 的请求，哪怕最终必然因为
    query 超长被拒，服务端也已经先把这 500MB 读进内存了——校验拦得住脏数据，
    拦不住资源耗尽。

    只看 Content-Length 而不去实际累计读取的字节数：这里要的是一道**廉价**
    的前置闸门，真正的分块限流应由反向代理（nginx ``client_max_body_size``）
    承担。缺失该头的分块传输请求放行，交给下游正常处理——为一个边缘情况在
    热路径上加一层包装读取，不划算。
    """
    raw = request.headers.get("content-length")
    if raw:
        try:
            if int(raw) > MAX_BODY_BYTES:
                return JSONResponse(
                    status_code=413,
                    content=error_payload(
                        "payload_too_large",
                        f"请求体过大（上限 {MAX_BODY_BYTES // 1024} KiB）",
                    ),
                )
        except ValueError:
            # Content-Length 不是数字：交给下游按协议错误处理，这里不越权
            pass
    return await call_next(request)


def error_payload(code: str, message: str) -> dict[str, Any]:
    """统一错误响应体。"""
    return {"error": {"code": code, "message": message}}


async def access_log(request: Request, call_next):
    """访问日志中间件：方法/路径/状态/耗时，慢请求告警。

    默认输出人类可读单行；环境变量 ``RAG4C_ACCESS_LOG_JSON=1`` 时切换为
    结构化 JSON 单行（含 method/path/status/duration_ms/query_id），便于
    日志采集系统（ELK/Loki）按字段检索，不改变慢请求/错误级别判定逻辑。
    """
    started = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - started) * 1000.0
    status = response.status_code
    query_id = request.headers.get("x-rag4c-query-id", "-")
    if _ACCESS_LOG_JSON:
        _logger.info(
            json.dumps(
                {
                    "method": request.method,
                    "path": request.url.path,
                    "status": status,
                    "duration_ms": round(duration_ms, 1),
                    "query_id": query_id,
                },
                ensure_ascii=False,
            )
        )
    else:
        line = f"{request.method} {request.url.path} -> {status} ({duration_ms:.1f}ms)"
        if duration_ms > SLOW_REQUEST_MS:
            _logger.warning("%s [slow] query_id=%s", line, query_id)
        elif status >= 500:
            _logger.error("%s query_id=%s", line, query_id)
        else:
            _logger.info("%s query_id=%s", line, query_id)
    return response


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """HTTPException -> 统一 JSON 错误契约（保留状态码）。"""
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail and "message" in detail:
        content = {"error": detail}
    else:
        content = error_payload(f"http_{exc.status_code}", str(detail))
    return JSONResponse(status_code=exc.status_code, content=content)


def _validation_reason(error_type: str) -> str:
    normalized = str(error_type or "").casefold()
    custom_reasons = {
        "judgment_patch_mutation_required": ("one of relevance_label, score, or note is required"),
        "judgment_patch_relevance_null": "relevance_label cannot be null",
        "judgment_patch_note_null": "note cannot be null",
    }
    if normalized in custom_reasons:
        return custom_reasons[normalized]
    if normalized == "missing":
        return "required field"
    if normalized == "extra_forbidden":
        return "unexpected field"
    if normalized.startswith("int_"):
        return "invalid integer"
    if normalized.startswith(("float_", "decimal_")):
        return "invalid number"
    if normalized.startswith("bool_"):
        return "invalid boolean"
    if normalized.startswith("string_"):
        return "invalid string"
    if normalized.startswith(("dict_", "model_")):
        return "invalid object"
    if normalized.startswith(("list_", "tuple_", "set_")):
        return "invalid array"
    if normalized in {"enum", "literal_error"}:
        return "invalid choice"
    if normalized == "json_invalid":
        return "malformed JSON"
    if normalized.startswith(("greater_than", "less_than")):
        return "value out of range"
    return "invalid value"


def _collect_model_fields(annotation: Any, fields: set[str]) -> None:
    origin = get_origin(annotation)
    if origin is not None:
        for argument in get_args(annotation):
            _collect_model_fields(argument, fields)
        return
    if not isinstance(annotation, type) or not issubclass(annotation, BaseModel):
        return
    for name, model_field in annotation.model_fields.items():
        fields.add(name)
        if isinstance(model_field.alias, str):
            fields.add(model_field.alias)
        _collect_model_fields(model_field.annotation, fields)


def _request_validation_schema(request: Request) -> tuple[set[str], bool]:
    route = request.scope.get("route")
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return set(), False
    fields: set[str] = set()
    for collection_name in (
        "path_params",
        "query_params",
        "header_params",
        "cookie_params",
        "body_params",
    ):
        for parameter in getattr(dependant, collection_name, ()):
            fields.add(str(parameter.alias or parameter.name))
            _collect_model_fields(parameter.field_info.annotation, fields)
    body_params = tuple(getattr(dependant, "body_params", ()))
    root_mapping = False
    if len(body_params) == 1:
        annotation = body_params[0].field_info.annotation
        origin = get_origin(annotation)
        root_mapping = origin is dict or (isinstance(origin, type) and issubclass(origin, Mapping))
    return fields, root_mapping


def _validation_location(
    request: Request,
    error_type: str,
    raw_location: tuple[Any, ...],
) -> str:
    if error_type == "json_invalid":
        raw_location = tuple(part for part in raw_location if not isinstance(part, int))
    known_fields, root_mapping = _request_validation_schema(request)
    safe: list[str] = []
    for index, part in enumerate(raw_location):
        if index == 0:
            rendered = str(part)
        elif error_type == "extra_forbidden" and index == len(raw_location) - 1:
            rendered = "<extra>"
        elif part == "[key]":
            rendered = "<key>"
        elif isinstance(part, int):
            rendered = "<item>"
        elif index == 1 and safe[0] == "body" and root_mapping:
            rendered = "<key>"
        elif str(part) in known_fields:
            rendered = str(part)
        else:
            rendered = "<key>"
        if not safe or safe[-1] != rendered:
            safe.append(rendered)
    return ".".join(safe) or "request"


def _validation_message(request: Request, exc: RequestValidationError) -> str:
    messages: list[str] = []
    for error in exc.errors()[:5]:
        error_type = str(error.get("type", ""))
        location = _validation_location(
            request,
            error_type,
            tuple(error.get("loc", ())),
        )
        messages.append(f"{location}: {_validation_reason(error_type)}")
    return "; ".join(messages) or "request: invalid value"


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """参数校验错误（422）-> 不回显输入值的统一 JSON 错误契约。"""
    return JSONResponse(
        status_code=422,
        content=error_payload("validation_error", _validation_message(request, exc)),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """未捕获异常 -> 500 JSON（含 exception 日志，可经 query_id 串联）。"""
    _logger.exception("未处理异常: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content=error_payload("internal_error", "服务器内部错误，请查看服务日志"),
    )
