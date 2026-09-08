"""本地目录文档源：把一棵目录树按同一套契约接进来。

看上去「本地文件直接调 add_file 就行，何必包一层」，但包了这一层之后，
本地目录和远程仓库在**增量、清单、dataset 归属、可观测**上走的是同一段
代码——不会出现「远程源支持增量、本地导入不支持」这种能力分裂。

不复制文件：本地文件已经在磁盘上了，``local_path`` 直接指向原位置，
``workdir`` 参数被忽略。
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Iterator

from sources.base import (
    DocumentSource,
    FetchedDocument,
    SourceError,
    content_sha256,
    stage_verified_bytes,
)
from sources.github_repo import _DEFAULT_EXTENSIONS, _glob_to_regex, _MAX_FILE_BYTES


def _is_reparse_point(path: Path) -> bool:
    """Return whether a candidate is a Windows reparse point without following it."""
    try:
        attributes = int(getattr(path.lstat(), "st_file_attributes", 0) or 0)
    except OSError as exc:
        raise SourceError(f"无法检查候选路径属性: {path.name}: {exc}") from exc
    marker = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return bool(attributes & marker)



def _read_all_fd(fd: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _secure_read_posix(candidate: Path, root: Path) -> tuple[Path, bytes] | None:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory_flag is None or os.open not in os.supports_dir_fd:
        raise SourceError("当前 POSIX 平台缺少 openat/O_NOFOLLOW 安全原语，已拒绝")
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise SourceError(f"候选路径越界，已拒绝: {candidate.name}") from exc
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise SourceError(f"候选相对路径非法，已拒绝: {candidate.name}")
    cloexec = int(getattr(os, "O_CLOEXEC", 0))
    root_fd = os.open(root, os.O_RDONLY | directory_flag | nofollow | cloexec)
    root_info = os.fstat(root_fd)
    if not stat.S_ISDIR(root_info.st_mode):
        os.close(root_fd)
        raise SourceError("安全打开拒绝非目录 source root")
    current_fd = root_fd
    opened_dirs: list[int] = []
    file_fd: int | None = None
    try:
        for component in relative.parts[:-1]:
            next_fd = os.open(
                component,
                os.O_RDONLY | directory_flag | nofollow | cloexec,
                dir_fd=current_fd,
            )
            opened_dirs.append(next_fd)
            current_fd = next_fd
        file_fd = os.open(
            relative.parts[-1],
            os.O_RDONLY | nofollow | cloexec,
            dir_fd=current_fd,
        )
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode):
            raise SourceError(f"安全打开拒绝非普通文件: {candidate.name}")
        if info.st_size > _MAX_FILE_BYTES:
            return None
        return root / relative, _read_all_fd(file_fd)
    except OSError as exc:
        raise SourceError(f"openat 安全打开失败 {candidate.name}: {exc}") from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)
        for fd in reversed(opened_dirs):
            os.close(fd)
        os.close(root_fd)


def _secure_read_windows(candidate: Path, root: Path) -> tuple[Path, bytes] | None:
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes
    except ImportError as exc:
        raise SourceError("Windows 安全文件句柄能力不可用，已拒绝本地源读取") from exc

    generic_read = 0x80000000
    share_all = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    open_reparse = 0x00200000
    sequential = 0x08000000
    file_attribute_reparse = 0x00000400
    file_attribute_directory = 0x00000010
    invalid_handle = ctypes.c_void_p(-1).value

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(candidate),
        generic_read,
        share_all,
        None,
        open_existing,
        open_reparse | sequential,
        None,
    )
    if handle == invalid_handle:
        error = ctypes.get_last_error()
        raise SourceError(f"Windows 安全打开失败 {candidate.name}: winerror={error}")
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    transferred = False
    try:
        information = ByHandleFileInformation()
        if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(information)):
            raise SourceError(
                f"Windows 文件句柄属性读取失败: {candidate.name}: "
                f"winerror={ctypes.get_last_error()}"
            )
        if information.dwFileAttributes & file_attribute_reparse:
            raise SourceError(f"Windows 安全打开拒绝重解析点: {candidate.name}")
        if information.dwFileAttributes & file_attribute_directory:
            raise SourceError(f"Windows 安全打开拒绝目录: {candidate.name}")
        size = (int(information.nFileSizeHigh) << 32) | int(information.nFileSizeLow)
        if size > _MAX_FILE_BYTES:
            return None
        get_final = kernel32.GetFinalPathNameByHandleW
        get_final.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        get_final.restype = wintypes.DWORD
        needed = get_final(handle, None, 0, 0)
        if needed <= 0:
            raise SourceError(f"Windows 无法验证文件句柄最终路径: {candidate.name}")
        buffer = ctypes.create_unicode_buffer(needed + 1)
        if get_final(handle, buffer, len(buffer), 0) <= 0:
            raise SourceError(f"Windows 无法读取文件句柄最终路径: {candidate.name}")
        final_value = buffer.value
        if final_value.startswith("\\\\?\\UNC\\"):
            final_value = "\\\\" + final_value[8:]
        elif final_value.startswith("\\\\?\\"):
            final_value = final_value[4:]
        final_path = Path(final_value).resolve(strict=True)
        if not final_path.is_relative_to(root):
            raise SourceError(f"文件句柄最终路径越界，已拒绝: {candidate.name}")
        fd = msvcrt.open_osfhandle(int(handle), os.O_RDONLY | os.O_BINARY)
        transferred = True
        try:
            return final_path, _read_all_fd(fd)
        finally:
            os.close(fd)
    finally:
        if not transferred:
            close_handle(handle)


def _secure_read_file(candidate: Path, root: Path) -> tuple[Path, bytes] | None:
    if os.name == "nt":
        return _secure_read_windows(candidate, root)
    if os.name == "posix":
        return _secure_read_posix(candidate, root)
    raise SourceError("当前平台没有受支持的 no-follow 安全读取原语")


class LocalDirectorySource(DocumentSource):
    """递归遍历本地目录，产出文档文件。

    参数（manifest 的 ``params``）：

    ==================  ====================================================
    ``path``            必填，目录绝对或相对路径
    ``include``         glob 列表（相对该目录），命中才要
    ``exclude``         glob 列表，命中即排除，优先级高于 include
    ``extensions``      认作文档的扩展名，默认与 GitHub 源一致
    ==================  ====================================================
    """

    name = "local_dir"

    def __init__(
        self,
        path: str,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        extensions: list[str] | None = None,
        max_files: int = 0,
    ) -> None:
        self.root = Path(path).expanduser()
        self._include = [_glob_to_regex(p) for p in (include or [])]
        self._exclude = [_glob_to_regex(p) for p in (exclude or [])]
        self.extensions = tuple(
            e.lower() if e.startswith(".") else f".{e.lower()}"
            for e in (extensions or _DEFAULT_EXTENSIONS)
        )
        self.max_files = max_files

    def describe(self) -> str:
        return "本地目录文档源：递归遍历，按 include/exclude 与扩展名筛选。"

    def fetch(self, workdir: Path) -> Iterator[FetchedDocument]:  # noqa: ARG002
        try:
            root = self.root.resolve(strict=True)
        except OSError as exc:
            raise SourceError(f"目录不存在或不是目录: {self.root}") from exc
        if not root.is_dir():
            raise SourceError(f"目录不存在或不是目录: {self.root}")
        if self.root.is_symlink() or _is_reparse_point(self.root):
            raise SourceError(f"源根目录不能是符号链接或重解析点: {self.root.name}")

        count = 0
        for candidate in sorted(root.rglob("*")):
            if candidate.is_symlink():
                raise SourceError(f"拒绝符号链接候选: {candidate.name}")
            if _is_reparse_point(candidate):
                raise SourceError(f"拒绝重解析点候选: {candidate.name}")
            try:
                resolved = candidate.resolve(strict=True)
            except OSError as exc:
                raise SourceError(f"候选路径解析失败: {candidate.name}: {exc}") from exc
            if not resolved.is_relative_to(root):
                raise SourceError(f"候选路径越界，已拒绝: {candidate.name}")
            if not resolved.is_file():
                continue
            rel = resolved.relative_to(root).as_posix()
            if not rel.lower().endswith(self.extensions):
                continue
            if any(rx.match(rel) for rx in self._exclude):
                continue
            if self._include and not any(rx.match(rel) for rx in self._include):
                continue
            secured = _secure_read_file(candidate, root)
            if secured is None:
                continue
            resolved, data = secured
            staged = stage_verified_bytes(workdir, rel, data)
            try:
                yield FetchedDocument(
                    uri=resolved.as_uri(),
                    rel_path=rel,
                    local_path=staged,
                    content_hash=content_sha256(data),
                    metadata={"root": str(root)},
                )
            finally:
                staged.unlink(missing_ok=True)
            count += 1
            if self.max_files and count >= self.max_files:
                break

        if count == 0:
            raise SourceError(
                f"{root} 下未匹配到任何文档文件"
                f"（扩展名 {', '.join(self.extensions)}）。"
            )


__all__ = ["LocalDirectorySource"]
