"""MinerU HTTP API 解析器（provider="http"）。

依据 MinerU_API_Summary.md（https://mineru.net/apiManage/docs）实现两条官方链路，
按 ``settings.api_key`` 是否为空自动选择：

- ``api_key`` 为空（Agent 轻量解析：免 Token、IP 限频、≤10MB / ≤20 页）：
  1. ``POST /api/v1/agent/parse/file`` 提交任务，返回 ``task_id`` 与
     OSS 签名上传地址 ``file_url``（摘要 §3.2）；
  2. ``PUT file_url`` 上传文件本体；
  3. 轮询 ``GET /api/v1/agent/parse/{task_id}`` 至 ``state=done``，
     取得 ``markdown_url``（CDN 链接，摘要 §3.3）并下载 Markdown。
- ``api_key`` 非空（v4 精准解析：需 Bearer Token、≤200MB / ≤200 页）：
  1. ``POST /api/v4/file-urls/batch`` 获取签名上传地址（摘要 §2.2）；
  2. ``PUT`` 上传文件；
  3. ``POST /api/v4/extract/task`` 创建解析任务（参数含 ``model_version`` /
     ``is_ocr`` / ``enable_formula`` / ``enable_table`` / ``language``，摘要 §2.1）；
  4. 轮询 ``GET /api/v4/extract/task/{task_id}`` 至 ``state=done``，
     取得 ``full_zip_url``（摘要 §2.1），下载 Zip 包并解出 Markdown。

设计要点：
- 完全离线可导入：HTTP 客户端惰性导入（优先 httpx，其次 requests），
  仅在 :meth:`parse` 内发起网络请求；两者都缺失时抛出带安装提示的
  :class:`MineruParserError`；
- 测试友好：构造函数可注入 ``http_client``（提供 ``request(method, url, **kw)``
  方法的会话对象即可），便于无网络单测；
- 错误归一：HTTP 错误码（含 429 限频）、HTTP 200 但业务 ``code != 0``、
  响应不可解析、轮询超时，统一包装为 :class:`MineruParserError`。
"""
from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path
from typing import Any

from config.settings import MineruSettings

from indexing.parsers.base import (
    SUPPORTED_EXTENSIONS,
    DocumentParser,
    MineruParserError,
    ParsedDocument,
    file_extension,
)

# PUT 上传文件时使用的 Content-Type（与支持的文件类型一一对应）
_CONTENT_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppt": "application/vnd.ms-powerpoint",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def _content_type(file_name: str) -> str:
    """按文件扩展名推断 Content-Type，未知类型回退 octet-stream。"""
    return _CONTENT_TYPES.get(Path(file_name).suffix.lower(), "application/octet-stream")


def _brief(text: str, limit: int = 300) -> str:
    """把响应体压缩成单行摘要（折叠空白），用于错误信息。"""
    collapsed = " ".join((text or "").split())
    return collapsed[:limit] if collapsed else "（无响应体）"


class MineruHttpParser(DocumentParser):
    """调用 MinerU HTTP API 的解析器。

    Args:
        settings: MineruSettings，其中 ``provider`` 应为 ``"http"``；
            ``api_base`` 为 API 根地址（默认 ``https://mineru.net``）；
            ``api_key`` 为空字符串时走 Agent 轻量链路（Flash 模式，
            免 Token、不携带认证头），非空时走 v4 精准链路（Bearer Token）；
            ``model_version`` / ``language`` / ``enable_ocr`` /
            ``enable_table`` / ``enable_formula`` 透传给 API 参数；
            ``timeout`` 为单次请求与整体轮询的预算（秒）。
        http_client: 可选注入的 HTTP 会话（提供 ``request(method, url, **kw)``
            方法，返回带 ``status_code`` / ``text`` / ``content`` / ``json()``
            的对象即可）。默认惰性创建 ``httpx.Client`` 或 ``requests.Session``。
    """

    _POLL_INTERVAL = 2.0  # 任务状态轮询间隔（秒）

    def __init__(self, settings: MineruSettings, http_client: Any = None, mode: str = "free") -> None:
        self.api_base = settings.api_base.rstrip("/")
        self.api_key = settings.api_key
        self.model_version = settings.model_version
        self.language = settings.language
        self.enable_ocr = settings.enable_ocr
        self.enable_table = settings.enable_table
        self.enable_formula = settings.enable_formula
        self.timeout = settings.timeout
        self._session: Any = http_client  # None 时在首次请求前惰性创建
        # 模式：free=Agent 轻量链路（免 Token，<=10MB/20 页）；
        # paid=v4 精准链路（需 api_key，<=200MB/200 页）
        if mode not in ("free", "paid"):
            raise MineruParserError(f"非法 mineru 模式: {mode!r}（free / paid）")
        self.mode = mode
        if mode == "paid" and not settings.api_key:
            raise MineruParserError(
                "mineru 付费模式（paid）需要 API Token："
                "配置 RAG4C_MINERU_API_KEY（https://mineru.net 获取）"
            )

    # ------------------------------------------------------------------ #
    # DocumentParser 接口
    # ------------------------------------------------------------------ #
    def supports(self, file_path: str) -> bool:
        """按扩展名判断是否支持（大小写不敏感）。"""
        return file_extension(file_path) in SUPPORTED_EXTENSIONS

    def parse(self, file_path: str) -> ParsedDocument:
        """把本地文件上传给 MinerU API 并返回解析出的 Markdown。

        无 Token（``api_key`` 为空）时走 Agent 轻量链路，
        有 Token 时走 v4 精准链路。

        Raises:
            MineruParserError: 缺少 HTTP 客户端库 / 网络或 HTTP 错误 /
                业务错误码 / 响应不可解析 / 任务失败或轮询超时。
        """
        self._validate_file(file_path)
        # 模式驱动的链路选择：paid 强制 v4 精准（key 已在 __init__ 校验）；
        # free 强制 Agent 轻量链路（忽略可能存在的 key）。
        if self.mode == "paid":
            return self._parse_v4(file_path)
        return self._parse_agent(file_path)

    # ------------------------------------------------------------------ #
    # HTTP 基础
    # ------------------------------------------------------------------ #
    def _ensure_session(self) -> Any:
        """惰性创建 HTTP 会话：优先 httpx，其次 requests。

        Raises:
            MineruParserError: httpx 与 requests 均未安装（附安装提示）。
        """
        if self._session is None:
            try:
                import httpx

                self._session = httpx.Client()
            except ImportError:
                try:
                    import requests

                    self._session = requests.Session()
                except ImportError as exc:
                    raise MineruParserError(
                        "未安装 HTTP 客户端库（httpx 或 requests），无法调用 MinerU API。"
                        "请执行 `pip install httpx` 或 `pip install requests`。"
                    ) from exc
        return self._session

    def _request(
        self,
        method: str,
        url: str,
        *,
        json: Any = None,
        data: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """发送请求并统一错误包装：网络异常 / HTTP >= 400 均抛 MineruParserError。"""
        session = self._ensure_session()
        kwargs: dict[str, Any] = {"timeout": self.timeout}
        if headers is not None:
            kwargs["headers"] = headers
        if json is not None:
            kwargs["json"] = json
        if data is not None:
            kwargs["data"] = data
        try:
            resp = session.request(method, url, **kwargs)
        except Exception as exc:  # httpx/requests 的各类网络与超时异常基类不一
            raise MineruParserError(
                f"请求 MinerU API 失败（{method} {url}）: {exc}"
            ) from exc
        if resp.status_code == 429:
            raise MineruParserError(
                f"MinerU 免费额度触发 IP 限频（HTTP 429，{method} {url}）。"
                "请稍后重试，或配置 RAG4C_MINERU_API_KEY 使用 v4 精准解析。"
            )
        if resp.status_code >= 400:
            raise MineruParserError(
                f"MinerU API 返回 HTTP {resp.status_code}（{method} {url}）: "
                f"{_brief(getattr(resp, 'text', '') or '')}"
            )
        return resp

    @staticmethod
    def _response_json(resp: Any) -> dict[str, Any]:
        """解析 JSON 响应体；非 JSON 或非 JSON 对象视为不可解析。"""
        try:
            payload = resp.json()
        except Exception as exc:  # httpx/requests 不同版本的解析异常基类不一
            raise MineruParserError(
                f"MinerU API 响应不是合法 JSON: {_brief(getattr(resp, 'text', '') or '')}"
            ) from exc
        if not isinstance(payload, dict):
            raise MineruParserError(
                f"MinerU API 响应结构异常（期望 JSON 对象，实际为 {type(payload).__name__}）"
            )
        return payload

    @staticmethod
    def _check_business_code(payload: dict[str, Any], action: str) -> None:
        """HTTP 200 但业务 ``code != 0`` 时抛出（如 -30002 不支持的文件类型）。"""
        code = payload.get("code")
        if code is not None and code != 0:
            msg = payload.get("msg") or payload.get("message") or "未知错误"
            raise MineruParserError(f"{action}失败（业务错误码 {code}）: {msg}")

    @staticmethod
    def _field(payload: dict[str, Any], key: str) -> Any:
        """从响应中取字段：兼容 ``{key: ...}`` 与 ``{code: 0, data: {key: ...}}`` 两种结构。"""
        if key in payload:
            return payload[key]
        data = payload.get("data")
        if isinstance(data, dict):
            return data.get(key)
        return None

    @staticmethod
    def _read_file(file_path: str) -> bytes:
        """读取文件字节，OSError 统一包装为 MineruParserError。"""
        try:
            with open(file_path, "rb") as fh:
                return fh.read()
        except OSError as exc:
            raise MineruParserError(f"读取文件失败: {file_path!r}（{exc}）") from exc

    def _poll(self, url: str, done_field: str, action: str) -> str:
        """轮询任务状态直至 ``state=done``，返回完成时携带的结果字段值。

        ``state=failed`` 立即失败；超出 ``timeout`` 预算仍未完成则超时失败。
        """
        deadline = time.monotonic() + self.timeout
        while True:
            resp = self._request("GET", url)
            body = self._response_json(resp)
            self._check_business_code(body, action)
            state = str(self._field(body, "state") or "").lower()
            if state == "done":
                value = self._field(body, done_field)
                if not value:
                    raise MineruParserError(f"{action}：任务完成但缺少 {done_field!r}")
                return value
            if state == "failed":
                detail = (
                    self._field(body, "error_msg")
                    or self._field(body, "msg")
                    or "未知失败原因"
                )
                raise MineruParserError(f"{action}：任务失败（{detail}）")
            if time.monotonic() >= deadline:
                raise MineruParserError(
                    f"{action}：轮询超时（>{self.timeout:.0f}s，URL={url}）"
                )
            time.sleep(self._POLL_INTERVAL)

    # ------------------------------------------------------------------ #
    # Agent 轻量链路（api_key 为空，免 Token）
    # ------------------------------------------------------------------ #
    def _parse_agent(self, file_path: str) -> ParsedDocument:
        name = Path(file_path).name
        content = self._read_file(file_path)

        # 1. 提交任务，取得 task_id 与 OSS 签名上传地址（摘要 §3.2）
        resp = self._request(
            "POST",
            f"{self.api_base}/api/v1/agent/parse/file",
            json={
                "filename": name,
                "language": self.language,
                "is_ocr": self.enable_ocr,
                "enable_table": self.enable_table,
                "enable_formula": self.enable_formula,
            },
            headers={"Content-Type": "application/json"},
        )
        body = self._response_json(resp)
        self._check_business_code(body, "提交 Agent 解析任务")
        task_id = self._field(body, "task_id")
        upload_url = self._field(body, "file_url")
        if not task_id or not upload_url:
            raise MineruParserError(
                f"Agent API 响应缺少 task_id / file_url: {_brief(resp.text)}"
            )

        # 2. PUT 上传文件本体到签名地址
        self._request(
            "PUT",
            upload_url,
            data=content,
            headers={"Content-Type": _content_type(name)},
        )

        # 3. 轮询直至 done，下载 markdown_url（摘要 §3.3）
        markdown_url = self._poll(
            f"{self.api_base}/api/v1/agent/parse/{task_id}",
            "markdown_url",
            "查询 Agent 解析进度",
        )
        markdown = self._request("GET", markdown_url).text
        if not markdown.strip():
            raise MineruParserError("MinerU 返回的 Markdown 为空")
        return ParsedDocument(
            text=markdown,
            metadata={
                "file_name": name,
                "provider": "http",
                "mode": "agent",
                "language": self.language,
            },
        )

    # ------------------------------------------------------------------ #
    # v4 精准链路（api_key 非空，Bearer Token）
    # ------------------------------------------------------------------ #
    def _parse_v4(self, file_path: str) -> ParsedDocument:
        name = Path(file_path).name
        auth = {"Authorization": f"Bearer {self.api_key}"}

        # 1. 申请签名上传地址（摘要 §2.2，单文件也走批量接口，取首个条目）
        resp = self._request(
            "POST",
            f"{self.api_base}/api/v4/file-urls/batch",
            json={"filenames": [name]},
            headers={**auth, "Content-Type": "application/json"},
        )
        body = self._response_json(resp)
        self._check_business_code(body, "申请文件上传地址")
        upload_list = self._field(body, "upload_file_list")
        first = upload_list[0] if isinstance(upload_list, list) and upload_list else None
        file_id = self._field(first, "file_id") if isinstance(first, dict) else None
        upload_url = (
            self._field(first, "upload_file_url") if isinstance(first, dict) else None
        )
        if not file_id or not upload_url:
            raise MineruParserError(
                f"v4 API 响应缺少 file_id / upload_file_url: {_brief(resp.text)}"
            )

        # 2. PUT 上传文件本体
        content = self._read_file(file_path)
        self._request(
            "PUT",
            upload_url,
            data=content,
            headers={"Content-Type": _content_type(name)},
        )

        # 3. 创建解析任务（摘要 §2.1 的参数列表）
        resp = self._request(
            "POST",
            f"{self.api_base}/api/v4/extract/task",
            json={
                "file_id": file_id,
                "model_version": self.model_version,
                "is_ocr": self.enable_ocr,
                "enable_table": self.enable_table,
                "enable_formula": self.enable_formula,
                "language": self.language,
            },
            headers={**auth, "Content-Type": "application/json"},
        )
        body = self._response_json(resp)
        self._check_business_code(body, "创建 v4 解析任务")
        task_id = self._field(body, "task_id")
        if not task_id:
            raise MineruParserError(f"v4 API 响应缺少 task_id: {_brief(resp.text)}")

        # 4. 轮询直至 done，下载 full_zip_url 并解出 Markdown（摘要 §2.1）
        zip_url = self._poll(
            f"{self.api_base}/api/v4/extract/task/{task_id}",
            "full_zip_url",
            "查询 v4 解析进度",
        )
        zip_resp = self._request("GET", zip_url)
        markdown = self._markdown_from_zip(zip_resp.content, task_id)
        if not markdown.strip():
            raise MineruParserError("MinerU 返回的 Markdown 为空")
        return ParsedDocument(
            text=markdown,
            metadata={
                "file_name": name,
                "provider": "http",
                "mode": "v4",
                "model_version": self.model_version,
                "language": self.language,
            },
        )

    @staticmethod
    def _markdown_from_zip(raw: bytes, task_id: str) -> str:
        """从 v4 结果 Zip 包中取出 Markdown 文本（按文件名排序取首个 ``.md``）。"""
        try:
            archive = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile as exc:
            raise MineruParserError(
                f"v4 解析结果不是有效的 ZIP 包（task_id={task_id}）: {exc}"
            ) from exc
        md_names = sorted(
            name for name in archive.namelist() if name.lower().endswith(".md")
        )
        if not md_names:
            raise MineruParserError(
                f"v4 解析结果 ZIP 中未找到 Markdown 文件（task_id={task_id}）"
            )
        return archive.read(md_names[0]).decode("utf-8", errors="replace")


__all__ = ["MineruHttpParser"]
