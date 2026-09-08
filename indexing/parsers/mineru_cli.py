"""MinerU CLI 解析器（provider="cli"）。

通过 ``subprocess`` 调用本机 mineru CLI（npm 包 ``mineru-open-api``），
以 ``flash-extract`` 模式（免 Token、≤10MB / ≤20 页）把文档转换为
Markdown。CLI 行为约定（见 MinerU skill 文档）：
- 结果 Markdown 输出到 stdout，进度输出到 stderr；
- ``--language`` 指定语言（默认 ``ch``），``--timeout`` 指定解析超时（秒）。

设计要点：
- 完全离线可导入：仅在 :meth:`parse` 内启动子进程；
- 可执行文件经 ``shutil.which`` 解析（Windows 下可命中 ``.cmd`` shim），
  找不到时给出 ``npm install -g mineru-open-api`` 的安装提示；
- 输出按 UTF-8 解码（``errors="replace"`` 兜底），非零退出码 / 空输出 /
  超时均抛出 :class:`MineruParserError`。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from config.settings import MineruSettings

from indexing.parsers.base import (
    SUPPORTED_EXTENSIONS,
    DocumentParser,
    MineruParserError,
    ParsedDocument,
    file_extension,
)


class MineruCliParser(DocumentParser):
    """调用本机 MinerU CLI 的解析器（flash-extract 模式）。

    Args:
        settings: MineruSettings，其中 ``provider`` 应为 ``"cli"``；
            ``executable`` 为 CLI 可执行文件名（默认 ``mineru-open-api``）；
            ``language`` 透传给 CLI 的 ``--language``（默认 ``ch``）；
            ``timeout`` 为子进程超时（秒，默认 900）。
    """

    def __init__(self, settings: MineruSettings, mode: str = "free") -> None:
        self.executable = settings.executable
        self.language = settings.language
        self.timeout = settings.timeout
        # 模式：free=flash-extract（免 Token，<=10MB/20 页）；paid=extract（需 auth 登录）
        if mode not in ("free", "paid"):
            raise MineruParserError(f"非法 mineru 模式: {mode!r}（free / paid）")
        self.mode = mode

    # ------------------------------------------------------------------ #
    # DocumentParser 接口
    # ------------------------------------------------------------------ #
    def supports(self, file_path: str) -> bool:
        """按扩展名判断是否支持（大小写不敏感）。"""
        return file_extension(file_path) in SUPPORTED_EXTENSIONS

    def parse(self, file_path: str) -> ParsedDocument:
        """以 flash-extract 模式运行 CLI，把 stdout 的 Markdown 包装为结果。

        Raises:
            MineruParserError: CLI 未安装 / 启动失败 / 退出码非零 /
                输出为空 / 子进程超时。
        """
        self._validate_file(file_path)
        exe = shutil.which(self.executable)
        if exe is None:
            raise MineruParserError(
                f"未找到 MinerU CLI 可执行文件 {self.executable!r}。"
                "请安装 npm 包：`npm install -g mineru-open-api`，"
                "或通过 RAG4C_MINERU_EXECUTABLE 指定可执行文件路径。"
            )
        action = "flash-extract" if self.mode == "free" else "extract"
        command = [
            exe,
            action,
            file_path,
            "--language",
            self.language,
            "--timeout",
            str(int(self.timeout)),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise MineruParserError(
                f"MinerU CLI 解析超时（>{self.timeout:.0f}s）: {file_path!r}"
            ) from exc
        except OSError as exc:
            raise MineruParserError(
                f"启动 MinerU CLI 失败: {exe!r}（{exc}）"
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr or "").strip()
            raise MineruParserError(
                f"MinerU CLI 解析失败（退出码 {result.returncode}）: {file_path!r}"
                + (f"\n{detail[-500:]}" if detail else "")
            )
        text = result.stdout
        if not text.strip():
            raise MineruParserError(f"MinerU CLI 返回空输出: {file_path!r}")
        return ParsedDocument(
            text=text,
            metadata={
                "file_name": Path(file_path).name,
                "provider": "cli",
                "mode": action,
                "executable": self.executable,
                "language": self.language,
            },
        )


__all__ = ["MineruCliParser"]
