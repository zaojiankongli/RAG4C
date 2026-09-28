"""Contextual Retrieval 文档级上下文增强组件（Indexing Enhancement）。

Contextual Retrieval 出自 Anthropic 官方博客 how_to_enhance_your_rag，
属于其中 **Indexing Enhancement** 方向：文档切分为 chunk 后，用 LLM 为
每个 chunk 生成一段「该 chunk 在整篇文档中的定位上下文」（situate this
chunk within the overall document），入库 / 检索 / 重排时把上下文与 chunk
文本拼接使用，弥补纯文本 chunk 脱离文档后丢失的全局信息。

设计要点：
- 本组件**只负责生成上下文**；上下文与 chunk 文本的拼接策略（前缀拼接 /
  后缀拼接 / 独立字段存储）由调用方（ingest 入库管线 / 检索管线）决定；
- 默认 ``enabled=False`` 保持零成本：不读模板、不发起任何 LLM 调用，
  ``contextualize`` 直接返回空 dict，对现有管线完全透明；
- LLM 调用只在方法内部发生，失败一律静默降级（返回空上下文），永不抛错，
  不影响入库管线；模板文件缺失（OSError）同样静默降级；
- 按 ``batch_size`` 分批调用：每批把整篇文档 + 该批 chunk 列表一起发给
  LLM，减少调用次数；``chat_json`` 协议只接受 JSON 对象（裸数组会被判为
  解析失败），因此模板要求输出 ``{"contexts": [{"chunk_id": str,
  "context": str}]}``，解析函数仍防御性兼容裸数组 / 单条对象等任意形状；
- 解析逐条容错：单条 chunk 缺失或非法只跳过该条，不影响同批其他 chunk；
- 纯标准库实现，模板路径与 llm_client 均为构造注入，可完全离线导入。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from core.llm import LLMError, ParseFallbackError
from core.observability import get_logger

_logger = get_logger(__name__)


class ContextualizerError(RuntimeError):
    """上下文生成参数错误（如 document / chunks 类型非法）。"""


class Contextualizer:
    """文档级上下文生成器（Contextual Retrieval 的 Indexing Enhancement）。

    Args:
        llm_client: 实现 ``chat_json(messages, schema_hint=None) -> dict``
            的客户端（如 :class:`core.llm.LLMClient` 或测试桩）。
        template_path: 上下文生成提示词模板路径
            （``prompts/contextual_v1.txt``，占位符 ``{document}`` 与
            ``{chunk}``）。
        batch_size: 每批发送给 LLM 的 chunk 数量（默认 16）；每批输出
            ``{"contexts": [...]}``。小于 1 时按 1 处理。
        enabled: 是否启用上下文生成。为 False 时 ``contextualize``
            直接返回空 dict，零成本、零 LLM 调用。

    Raises:
        ContextualizerError: ``document`` 或 ``chunks`` 类型非法。
    """

    def __init__(
        self,
        llm_client: Any,
        template_path: str | Path,
        batch_size: int = 16,
        enabled: bool = True,
        concurrency: int = 4,
        document_char_limit: int = 4000,
    ) -> None:
        self.llm_client = llm_client
        self.template_path = Path(template_path)
        self.batch_size = max(1, batch_size)
        self.enabled = enabled
        # 批次并发度：各批独立，但不宜过高（远端限流 / 本地模型并发能力）
        self.concurrency = max(1, concurrency)
        # 放进提示词的文档摘要上限（字符）；<=0 表示不截断（发全文，慎用）
        self.document_char_limit = document_char_limit
        self._template: str | None = None

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _load_template(self) -> str:
        """惰性读取提示词模板（文件缺失抛 OSError，由调用方降级）。"""
        if self._template is None:
            self._template = self.template_path.read_text(encoding="utf-8")
        return self._template

    @staticmethod
    def _clean_chunks(chunks: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """过滤畸形 chunk 条目：要求可解包为二元组且文本为 str。"""
        cleaned: list[tuple[str, str]] = []
        for item in chunks:
            try:
                chunk_id, text = item
            except (TypeError, ValueError):
                continue
            if not isinstance(text, str):
                continue
            cleaned.append((chunk_id, text))
        return cleaned

    def _call_batch(
        self, template: str, document: str, batch: list[tuple[str, str]]
    ) -> list[tuple[str, str]]:
        """把文档摘要 + 该批 chunk 列表发给 LLM，解析出合法条目列表。

        ``document`` 参数接收的是 :meth:`_document_digest` 产出的摘要而非
        全文——各批并发执行，重复发送全文会成倍放大 token 成本并极易超窗。

        LLM 调用 / 解析失败返回空列表（静默降级），永不抛错——并发执行时
        单批失败不影响其余批次。
        """
        block = "\n".join(
            f'<chunk id="{chunk_id}">\n{text}\n</chunk>' for chunk_id, text in batch
        )
        try:
            prompt = template.replace("{document}", document).replace("{chunk}", block)
            messages: list[dict[str, str]] = [
                {
                    "role": "system",
                    "content": "你是企业 RAG 系统的文档上下文生成器，只输出 JSON。",
                },
                {"role": "user", "content": prompt},
            ]
            data = self.llm_client.chat_json(
                messages,
                schema_hint='{"contexts": [{"chunk_id": str, "context": str}]}',
            )
        except (LLMError, ParseFallbackError, OSError):
            return []
        return self._parse_batch(data)

    @staticmethod
    def _parse_batch(data: Any) -> list[tuple[str, str]]:
        """防御性解析 LLM 返回的任意形状，逐条校验后返回合法条目。

        支持形状：
        - dict：``{"contexts": [...]}``（优先），依次尝试 ``items`` /
          ``results`` 键；若 dict 自身含 ``chunk_id`` 键则视为单条对象；
        - list：视为裸 ``[{"chunk_id": str, "context": str}, ...]`` 数组；
        - str / None / 其他形状：返回空列表。
        非法条目（chunk_id / context 非 str、context 为空）逐条跳过。
        """
        if isinstance(data, dict):
            items = data.get("contexts")
            if not isinstance(items, list):
                items = data.get("items")
            if not isinstance(items, list):
                items = data.get("results")
            if not isinstance(items, list):
                items = [data] if "chunk_id" in data else None
        elif isinstance(data, list):
            items = data
        else:
            return []
        if not isinstance(items, list):
            return []
        parsed: list[tuple[str, str]] = []
        for item in items:
            entry = Contextualizer._parse_item(item)
            if entry is not None:
                parsed.append(entry)
        return parsed

    @staticmethod
    def _parse_item(item: Any) -> tuple[str, str] | None:
        """校验单条 ``{"chunk_id": str, "context": str}``，非法返回 None。"""
        if not isinstance(item, dict):
            return None
        chunk_id = item.get("chunk_id")
        context = item.get("context")
        if not isinstance(chunk_id, str) or not isinstance(context, str):
            return None
        context = context.strip()
        if not context:
            return None
        return chunk_id, context

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    def contextualize(
        self, document: str, chunks: list[tuple[str, str]]
    ) -> dict[str, str]:
        """为每个 chunk 生成文档级定位上下文，返回 ``{chunk_id: context}``。

        - ``enabled=False`` / ``document`` 为空 / ``chunks`` 为空时返回
          空 dict，不发起任何 LLM 调用；
        - 按 ``batch_size`` 分批调用 LLM，每批输出
          ``{"contexts": [{"chunk_id", "context"}, ...]}``，解析后按
          ``chunk_id`` 对齐输入；缺失或非法的条目跳过；
        - 任一整批 LLM 失败（LLMError / ParseFallbackError / 模板 OSError）
          静默降级，永不抛错，由调用方继续走无上下文路径。

        Args:
            document: 整篇文档全文（生成定位上下文的唯一依据）。
            chunks: ``[(chunk_id, chunk_text), ...]``；畸形条目被跳过。

        Returns:
            ``{chunk_id: context}`` 映射，键为输入 chunk 的原始 id。

        Raises:
            ContextualizerError: ``document`` 不是 str 或 ``chunks``
                不是 list。
        """
        if not isinstance(document, str):
            raise ContextualizerError("document 必须是 str")
        if not isinstance(chunks, list):
            raise ContextualizerError("chunks 必须是 list")
        if not self.enabled or not document.strip() or not chunks:
            return {}
        try:
            template = self._load_template()
        except OSError:
            # 模板文件缺失：静默降级
            return {}

        cleaned = self._clean_chunks(chunks)
        if not cleaned:
            return {}

        # str(chunk_id) -> 原始 chunk_id：容忍 LLM 以字符串回显任意 id
        requested: dict[str, str] = {str(chunk_id): chunk_id for chunk_id, _ in cleaned}

        # 每批发一次 LLM；各批之间完全独立（结果按 chunk_id 合并），因此并发执行。
        # 文档摘要只截取一次，避免把整篇文档重复塞进每一批的提示词——
        # 原实现对 200 个 chunk 会把全文发 13 遍（约 400 万字符），
        # 多数模型会直接超上下文窗口，然后 _call_batch 返回空、上层静默吞掉，
        # 表现为「开了上下文增强却一个上下文都没有，且毫无报错」。
        digest = self._document_digest(document)
        batches = [
            cleaned[start : start + self.batch_size]
            for start in range(0, len(cleaned), self.batch_size)
        ]
        contexts: dict[str, str] = {}
        if not batches:
            return {}

        results: list[list[tuple[str, str]]] = [[] for _ in batches]
        with ThreadPoolExecutor(
            max_workers=min(self.concurrency, len(batches)),
            thread_name_prefix="rag-contextual",
        ) as pool:
            futures = {
                pool.submit(self._call_batch, template, digest, batch): idx
                for idx, batch in enumerate(batches)
            }
            for future in as_completed(futures):
                results[futures[future]] = future.result()

        # 按原批次顺序合并，保证「先出现的 chunk 优先」与串行时一致
        for entries in results:
            for chunk_id, context in entries:
                original = requested.get(str(chunk_id))
                if original is not None and original not in contexts:
                    contexts[original] = context

        # 「开了增强却一个上下文都没生成」必须留下痕迹。
        #
        # 这条路径实测踩过：入库期三个槽位（contextual/triplet/classifier）刻意
        # 指向本机 Ollama，机器没装 / 没起时，每次调用要先把重试耗满（实测 113 秒）
        # 才失败，而失败被这里静默降级成空结果——外面看就是"增强开了、一篇文档
        # 慢了近两分钟、上下文一条没生成"，既不报错也不告警，纯白花时间。
        # 静默降级本身保留（入库不该被增强拖垮），但必须让它**看得见**。
        if not contexts:
            _logger.warning(
                "Contextual Retrieval 未产出任何上下文：请求 %d 个片段、%d 批，全部降级。"
                "常见原因是 contextual 槽位的端点不可用（如本机 Ollama 未启动）——"
                "此时每次调用会先把重试耗满才失败，入库会被显著拖慢。",
                len(cleaned),
                len(batches),
            )
            try:
                from core.metrics import get_metrics

                get_metrics().incr("contextual.batches_degraded", value=float(len(batches)))
            except Exception:  # noqa: BLE001 - 埋点失败不影响入库
                pass
        return contexts

    def _document_digest(self, document: str) -> str:
        """把整篇文档压成可放进提示词的摘要（截断，不调用模型）。

        Contextual Retrieval 需要的是「这个片段在全文里的位置与角色」，
        给出文档开头（通常含标题与总述）即可提供足够定位信息；把全文
        原样塞进每一批既昂贵又容易超窗。截断优先在段落边界处断开，
        避免把句子切碎。
        """
        limit = self.document_char_limit
        text = document.strip()
        if limit <= 0 or len(text) <= limit:
            return text
        head = text[:limit]
        cut = head.rfind("\n\n")
        if cut < limit // 2:
            cut = head.rfind("\n")
        if cut < limit // 2:
            cut = limit
        return head[:cut].rstrip() + "\n\n（文档较长，此处为开头部分摘录）"

    def contextualize_chunks(
        self, document: str, chunks: list[tuple[str, str]]
    ) -> list[tuple[str, str, str]]:
        """便捷方法：返回 ``[(chunk_id, text, context), ...]`` 三元组。

        未生成到上下文的 chunk 以空字符串占位，调用方可自行拼接，如：
        ``f"{context}\n{text}" if context else text``。
        """
        contexts = self.contextualize(document, chunks)
        return [
            (chunk_id, text, contexts.get(chunk_id, ""))
            for chunk_id, text in self._clean_chunks(chunks)
        ]


__all__ = ["ContextualizerError", "Contextualizer"]
