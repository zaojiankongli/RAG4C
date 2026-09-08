"""RAG4C 桥服务的无状态支撑层。

存放从 ``server.app`` 中抽出的**纯函数**：dotenv 值序列化、配置值类型强制、
Prometheus 指标文本、文件末尾回读。这些逻辑不持有进程级状态、不依赖
FastAPI 应用生命周期，抽取后行为与原实现逐字节等价。

设计说明：
- 只放"输入 -> 输出"的纯函数；任何依赖模块级可变状态（查询槽位、最近查询
  缓存、指标持久化线程、热更新 os.environ、cache/llm/embed 统计函数）的逻辑
  一律**不**下沉到这里，它们与 app 生命周期强耦合，属于装配层的职责。
- **配置标签/提示字典（_FIELD_HINTS/_PATH_HINTS/_SECTION_HINTS/_FIELD_LABELS/
  _PATH_LABELS）刻意不迁移**：它们仅服务 /api/config 渲染，留在 app.py 更贴近
  使用点，也避免大段静态数据搬动用引入文案漂移风险。
- 关键符号仍由 ``server.app`` 重导出并保留下划线别名，保证
  ``from server.app import _format_env_value`` 等既有调用（含测试）不变。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# dotenv 值序列化（配置热更新用）
# ---------------------------------------------------------------------------


def env_runtime_value(value: Any) -> str:
    """把已校验的值序列化成**进程内** os.environ 该有的样子。

    刻意不复用 :func:`format_env_value`：那个函数产出的是 *dotenv 文件字面量*，
    需要引号和转义才能被 dotenv 解析器正确读回来。而 ``os.environ`` 里存的是
    解析**之后**的值——把带引号的形式塞进去，Settings 读到的 str 字段会连引号
    一起收下（``rag4c_foo = '"a b"'``），于是配置页显示成功、值也确实变了、
    只是多了一对引号。两条路径长得像但含义相反，合成一个函数迟早出这种事。
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def format_env_value(value: Any) -> str:
    """Serialize a dotenv value without allowing line injection."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if any(char in text for char in ("\r", "\n", "\x00")):
        raise ValueError("字符串不能包含换行或 NUL 字符")
    if re.fullmatch(r"[A-Za-z0-9_./:@+\-]*", text):
        return text
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
    return f'"{escaped}"'


def coerce_config_value(raw: Any, target_type: type) -> Any:
    """把前端提交的字符串值按目标字段类型做严格转换。"""
    if target_type is bool:
        if isinstance(raw, bool):
            return raw
        s = str(raw).strip().lower()
        if s in ("true", "1", "yes", "on"):
            return True
        if s in ("false", "0", "no", "off"):
            return False
        raise ValueError("期望 true/false")
    if target_type is int:
        return int(str(raw).strip())
    if target_type is float:
        return float(str(raw).strip())
    return str(raw)


# ---------------------------------------------------------------------------
# 指标 / Prometheus 纯函数
# ---------------------------------------------------------------------------


def metric_key_parts(key: str) -> tuple[str, dict[str, str]]:
    """指标 key 拆分为 (name, tags)。"""
    if "|" in key:
        name, tags_raw = key.split("|", 1)
        tags = dict(kv.split("=", 1) for kv in tags_raw.split(",") if "=" in kv)
        return name, tags
    return key, {}


def prometheus_text(snapshot: dict[str, dict[str, float]]) -> str:
    """指标快照 -> Prometheus 文本格式（histogram 摘要导出为分项 gauge）。"""
    lines: list[str] = []
    for key, entry in snapshot.items():
        name, tags = metric_key_parts(key)
        metric = "rag4c_" + name.replace(".", "_").replace("-", "_")
        labels = ""
        if tags:
            labels = "{" + ",".join(f'{k}="{v}"' for k, v in tags.items()) + "}"
        for stat in ("count", "sum", "mean", "min", "max", "p50", "p95", "p99"):
            lines.append(f"{metric}_{stat}{labels} {entry[stat]}")
        if "error_rate" in entry:
            lines.append(f"{metric}_error_rate{labels} {entry['error_rate']}")
    return "\n".join(lines) + "\n"


def tail_lines(path: Path, limit: int, block_size: int = 64 * 1024) -> list[str]:
    """从文件末尾回读，返回最后 ``limit`` 个非空行（按原顺序）。

    以二进制方式按块从后往前读，直到攒够足够多的换行符为止；只对最终
    需要的片段做 UTF-8 解码。首块可能截断到某行中间，因此解码后丢弃
    第一段（除非已经读到文件开头）。
    """
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        end = f.tell()
        buf = b""
        pos = end
        while pos > 0 and buf.count(b"\n") <= limit:
            step = min(block_size, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step) + buf
        chunk = buf.decode("utf-8", errors="replace")
    lines = chunk.splitlines()
    if pos > 0 and lines:
        lines = lines[1:]  # 首行可能被块边界截断
    return [ln for ln in lines if ln.strip()][-limit:]
