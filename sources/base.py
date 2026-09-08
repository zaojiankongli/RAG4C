"""文档源接入层的基础契约：抓取产物与源接口。

**这一层只负责「把文档搞到本地」，不负责解析和入库。** 解析与入库已经由
:class:`indexing.ingest.IngestPipeline` 承担，源层的产物刻意做成「本地文件
路径」而不是「内存文本」，有三个理由：

1. **复用完整入库链路。** ``IngestPipeline.add_document`` 走的是精简路径，
   会绕过 cleaner、预切分策略和切分模式路由；只有 ``add_file`` /
   ``parse_and_chunk`` 才是完整链路。产物是文件，就能直接走完整链路，
   远程文档和本地文档从此走同一条路，不存在「两种入库语义」。
2. **增量可复用。** :func:`indexing.reindex.reindex_document` 的文件级哈希
   跳过是按 ``file_path`` 设计的，产物落盘后可以直接接上。
3. **可检查、可重跑。** 抓取结果留在缓存目录里，出问题时能直接打开看到底
   抓到了什么，而不是只能从日志倒推。

代价是多一次磁盘写入——相对于随后的嵌入调用，这点开销可以忽略。
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


class SourceError(RuntimeError):
    """文档源抓取失败（网络不可达、仓库不存在、路径非法等）。

    与 :class:`indexing.parsers.base.MineruParserError` 同一设计：源实现内部
    的各类异常（requests / tarfile / OSError……）一律归一到这个基类，调用方
    只需要 catch 一种。
    """


@dataclass
class FetchedDocument:
    """一篇被抓取到本地的文档。

    Attributes:
        uri: 文档的来源标识，要求**全局唯一且可追溯**（如
            ``github://milvus-io/milvus-docs@master/site/en/about.md``）。
            它会作为 ``source`` 字段写进每个 chunk，是「这句话出自哪里」的
            最终依据，也是引用能落到具体出处的前提。
        rel_path: 相对源根的路径（如 ``site/en/about.md``）。用于生成稳定
            doc_id 与 include/exclude 匹配。
        local_path: 落盘后的绝对路径，交给 IngestPipeline 解析。
        content_hash: 内容 sha256（hex）。增量判定只看它，不看 mtime——
            重新抓取会刷新 mtime 但内容可能没变，用 mtime 会导致每次全量重嵌。
        metadata: 附加元信息，会并入 chunk 的 metadata（项目名、语言、版本等）。
    """

    uri: str
    rel_path: str
    local_path: Path
    content_hash: str
    metadata: dict[str, Any] = field(default_factory=dict)


_SAFE_SOURCE_CACHE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REPARSE_ATTRIBUTE = 0x400


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        return bool(int(getattr(path.lstat(), "st_file_attributes", 0) or 0) & _REPARSE_ATTRIBUTE)
    except OSError:
        return True


def prepare_source_cache_directory(cache_root: Path, source_id: str) -> Path:
    if not _SAFE_SOURCE_CACHE_ID.fullmatch(str(source_id or "")):
        raise SourceError("source cache id is not a safe immutable identifier")
    root = Path(cache_root).expanduser()
    if root.exists() and _is_link_or_reparse(root):
        raise SourceError("source cache root must not be a symlink or reparse point")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    resolved_root = root.resolve(strict=True)
    source_dir = root / source_id
    if source_dir.exists() and _is_link_or_reparse(source_dir):
        raise SourceError("source cache directory must not be a symlink or reparse point")
    source_dir.mkdir(exist_ok=True, mode=0o700)
    source_dir.chmod(0o700)
    resolved_source = source_dir.resolve(strict=True)
    if not resolved_source.is_relative_to(resolved_root):
        raise SourceError("source cache directory escaped configured cache root")
    return resolved_source


def prepare_verified_staging(
    workdir: Path,
    *,
    ttl_seconds: float,
    now: float | None = None,
) -> Path:
    work = Path(workdir)
    if work.exists() and _is_link_or_reparse(work):
        raise SourceError("source work directory must not be a symlink or reparse point")
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    work.chmod(0o700)
    root = work.resolve(strict=True)
    verified = work / "_verified"
    if verified.exists() and _is_link_or_reparse(verified):
        raise SourceError("verified staging directory must not be a symlink or reparse point")
    verified.mkdir(exist_ok=True, mode=0o700)
    if _is_link_or_reparse(verified):
        raise SourceError("verified staging directory became a symlink or reparse point")
    verified.chmod(0o700)
    resolved = verified.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise SourceError("verified staging directory escaped source cache")
    cutoff = (time.time() if now is None else float(now)) - max(0.0, ttl_seconds)
    for candidate in resolved.iterdir():
        if _is_link_or_reparse(candidate):
            raise SourceError("verified staging contains an unsafe link or reparse point")
        if candidate.is_file() and candidate.stat().st_mtime < cutoff:
            candidate.unlink(missing_ok=True)
    return resolved


def stage_verified_bytes(
    workdir: Path,
    rel_path: str,
    data: bytes,
    *,
    ttl_seconds: float = 86400.0,
) -> Path:
    """Atomically stage verified bytes into an app-owned restrictive file."""

    root = prepare_verified_staging(workdir, ttl_seconds=ttl_seconds)
    suffix = Path(rel_path).suffix or ".bin"
    fd, temporary = tempfile.mkstemp(prefix=".stage-", suffix=suffix, dir=root)
    final = root / f"{uuid.uuid4().hex}{suffix}"
    try:
        try:
            os.fchmod(fd, 0o600)
        except (AttributeError, OSError):
            pass
        with os.fdopen(fd, "wb", closefd=True) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, final)
        final.chmod(0o600)
        return final
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        Path(temporary).unlink(missing_ok=True)
        final.unlink(missing_ok=True)
        raise


def content_sha256(data: bytes) -> str:
    """内容哈希（增量判定用）。

    与 :func:`indexing.hashing.text_hash` 的区别：那个吃 str（用于 chunk 级
    文本比对），这个吃 bytes（用于文件级比对，不需要先决定编码）。
    """
    return hashlib.sha256(data).hexdigest()


class DocumentSource(ABC):
    """一个文档源。

    实现约定（与 :class:`indexing.parsers.base.DocumentParser` 保持一致）：

    - **构造必须离线且廉价**：不发起网络请求、不下载、不建连接。所有 I/O
      只允许发生在 :meth:`fetch` 内。这条约定让 ``import`` 与配置自省始终
      安全，也让「列出有哪些源」不会意外触发几百 MB 的下载。
    - **``fetch`` 是生成器**：逐篇产出而不是先攒一个大 list。远程仓库动辄
      几千个文件，一次性物化会把内存和首次产出延迟都推高。
    - **失败归一为** :class:`SourceError`，并带上可操作的提示（缺什么依赖、
      哪个 URL 404、哪个目录不存在）。
    """

    #: 源类型名，与注册表的 key 一致，子类必须覆盖。
    name: str = ""

    @abstractmethod
    def fetch(self, workdir: Path) -> Iterator[FetchedDocument]:
        """把文档抓取到 ``workdir`` 下，逐篇产出。

        Args:
            workdir: 本源专属的缓存目录（调用方保证已存在）。实现应把文件
                按 ``rel_path`` 的结构落在这个目录下，便于人工核对。

        Yields:
            :class:`FetchedDocument`

        Raises:
            SourceError: 抓取失败。
        """

    @abstractmethod
    def describe(self) -> str:
        """一句话能力说明（供 CLI / 配置中心展示与错误提示）。"""


__all__ = [
    "DocumentSource",
    "FetchedDocument",
    "SourceError",
    "content_sha256",
    "stage_verified_bytes",
    "prepare_source_cache_directory",
    "prepare_verified_staging",
]
