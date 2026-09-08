"""GitHub 仓库文档源：直接抓取官方文档仓库里的 Markdown / AsciiDoc。

**两条抓取路径，按代价自动选。**

- ``tarball``：``codeload.github.com/<repo>/tar.gz/refs/heads/<ref>`` 一次请求
  拿到整个仓库快照，配合流式解包（``tarfile`` 的 ``r|gz`` 顺序模式）边下边筛。
  一次往返搞定，仓库不大时最省。
- ``api``：先用 git trees API 拿到全量文件清单（一次请求），再从
  ``raw.githubusercontent.com`` 逐个下载命中的文件。往返多，但**只下要的那部分**。

为什么两条都要：``redis/docs`` 有一万多个文件、``spring-projects/spring-boot``
有一万一千多个，而我们各自只要两百来篇文档。为了这 200 篇去拖整个仓库快照，
在慢网络上要多花十几分钟；反过来，``milvus-io/milvus-docs`` 全仓才 152 个文件，
逐个请求的往返开销反而更亏。默认的 ``auto`` 用 trees API 里的 blob 尺寸算出
「要的字节 / 全仓字节」，按这个比例挑路径——判据是实测得到的数，不是拍脑袋。

``api`` 路径还顺带拿到一个便宜的好处：trees API 返回每个 blob 的 git sha，
它本身就是内容哈希。本地缓存文件若算出同一个 sha，这次连下载都省了。

**为什么不用别的路子。**

- **不用 ``git clone``**：环境里不保证有 git，且默认会把完整历史拖下来。
- **不用网页抓取**：官方文档站是渲染后的 HTML，要还原成干净文本需要
  bs4 / trafilatura 之类的解析依赖，而这些包本项目都没装；何况仓库里的
  Markdown 本来就是文档站的**源文件**，比抓渲染结果更干净、更完整。

抓取时只落盘命中 include/exclude 的文档文件，其余直接丢弃——仓库里的源码、
图片、测试数据既没有入库价值，落盘也白白占几百 MB。
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import io
import json
import re
import tarfile
import time
from pathlib import Path
from typing import Any, Iterator

from core.observability import get_logger
from sources.base import DocumentSource, FetchedDocument, SourceError, content_sha256

# 默认认作「文档」的扩展名。AsciiDoc（.adoc）必须在内：SpringBoot 的参考
# 文档整本都是 AsciiDoc，漏掉它等于抓不到 SpringBoot 的正文。
_DEFAULT_EXTENSIONS = (".md", ".mdx", ".markdown", ".adoc", ".rst", ".txt")

# 单个文件大小上限：文档仓库里偶尔混着几十 MB 的生成物 / 数据文件，
# 它们即便扩展名是 .txt 也没有入库价值，还会把嵌入成本推高。
_MAX_FILE_BYTES = 2 * 1024 * 1024

#: auto 模式的判据：想要的字节数占全仓的比例低于此值就走 api 逐文件。
#: 0.30 来自 gzip 对文本约 3 倍的压缩比——tarball 传输量大致是全仓字节的
#: 三分之一，低于这个比例时，逐文件下载传的字节更少。
_AUTO_TARBALL_RATIO = 0.30

#: api 模式下载并发。设小值是刻意的：raw.githubusercontent.com 对匿名请求
#: 有速率限制，开太大反而更容易被限流，收益也早已被带宽吃掉。
_API_WORKERS = 6

_VALID_MODES = ("auto", "tarball", "api")


class _ApiPathUnavailable(SourceError):
    """api 路径在**产出任何文档之前**就失败了，auto 模式可以改走 tarball。

    单独一个类型是为了把「这条路走不通，换一条」和「抓取真的失败了」区分开。
    只在零产出时抛：已经产出过文档再回退，会让同一批文档被产出两次。
    """


def _git_blob_sha(data: bytes) -> str:
    """算 git 的 blob 对象 sha1，用来和 trees API 返回的 sha 对比。

    git 不是对文件内容直接做 sha1，而是对 ``blob <字节数>\\0<内容>`` 做——
    少了这个头，算出来的值和 GitHub 给的永远对不上。
    """
    h = hashlib.sha1()  # noqa: S324 - 对齐 git 的对象 ID，不作安全用途
    h.update(f"blob {len(data)}\0".encode("utf-8"))
    h.update(data)
    return h.hexdigest()


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """把 glob 转成正则，支持 ``**`` 跨目录。

    不用 ``fnmatch``：它把 ``*`` 当成「匹配任意字符（含 ``/``）」，
    ``docs/*.md`` 会连 ``docs/a/b.md`` 一起命中，跟直觉不符。也不用
    ``PurePath.match``：它对 ``**`` 的支持在不同 Python 版本上不一致。
    自己翻译一遍，行为对所有版本都确定。

    语义：``**`` 跨任意层目录；``*`` 只在单层内匹配；``?`` 匹配单个非 ``/`` 字符。
    """
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                # `**/` 应当也能匹配零层目录，否则 `a/**/*.md` 匹配不到 `a/x.md`
                if i + 2 < n and pattern[i + 2] == "/":
                    out.append("(?:.*/)?")
                    i += 3
                    continue
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def _http_get_stream(url: str, timeout: float, retries: int = 3) -> Any:
    """发起流式 GET，返回一个带 ``raw`` / ``iter_content`` 的响应对象。

    与 :mod:`indexing.parsers.mineru_http` 同样是「两个 HTTP 库任选其一」的
    惰性 fallback，但**顺序相反：这里 requests 优先**。原因很实际——本仓库
    的 venv 里装的是 ``requests`` 和 ``httpx2``，``httpx`` 只在 dev extra 里
    声明、实际并未安装。先试 httpx 只会白白吃一次 ImportError。

    带重试是因为实测到连接被重置：抓 GitHub 时链路上的连接重置相当常见，
    而一次同步要发几百个请求，不重试的话几乎必然中途失败。只对**连接层**
    异常重试；HTTP 404 / 403 这类明确答复重试多少次都是同一个结果，
    重试只会拖时间并加速触发限流。
    """
    try:
        import requests
    except ImportError:
        raise SourceError(
            "抓取需要 HTTP 客户端，但环境里没有 requests。请执行 pip install requests。"
        ) from None

    last_exc: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            resp = requests.get(url, timeout=timeout, stream=True)
        except Exception as exc:  # noqa: BLE001 - 网络异常归一后重试
            last_exc = exc
            if attempt + 1 < retries:
                time.sleep(0.5 * (2**attempt))
            continue
        if resp.status_code == 404:
            raise SourceError(
                f"仓库或分支不存在（HTTP 404）: {url}。"
                "请核对 params.repo 与 params.ref（主分支名常见 main / master 两种）。"
            )
        if resp.status_code in (403, 429):
            raise SourceError(
                f"被 GitHub 限流或拒绝（HTTP {resp.status_code}）: {url}。"
                "未认证的 API 配额是 60 次/小时；可把 params.mode 设为 tarball 改走单次下载。"
            )
        if resp.status_code != 200:
            raise SourceError(f"下载失败 HTTP {resp.status_code}: {url}")
        return resp

    raise SourceError(f"下载失败（重试 {retries} 次）{url}: {last_exc}")


class GitHubRepoSource(DocumentSource):
    """从 GitHub 仓库快照里抓取文档文件。

    参数（manifest 的 ``params``）：

    ==================  ====================================================
    ``repo``            必填，``owner/name``
    ``ref``             分支或标签，默认 ``main``
    ``include``         glob 列表；给了就只要命中的（如 ``docs/**/*.md``）
    ``exclude``         glob 列表；命中即排除，优先级高于 include
    ``extensions``      认作文档的扩展名，默认见 ``_DEFAULT_EXTENSIONS``
    ``strip_prefix``    从 rel_path 前缀里去掉的部分（如 ``site/en/``），
                        只影响 doc_id 与展示，不影响匹配
    ``mode``            ``auto``（默认）/ ``tarball`` / ``api``，见模块文档
    ==================  ====================================================
    """

    name = "github_repo"

    def __init__(
        self,
        repo: str,
        ref: str = "main",
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        extensions: list[str] | None = None,
        strip_prefix: str = "",
        timeout: float = 300.0,
        max_files: int = 0,
        mode: str = "auto",
    ) -> None:
        self.repo = repo.strip().strip("/")
        self.ref = (ref or "main").strip()
        self._include = [_glob_to_regex(p) for p in (include or [])]
        self._exclude = [_glob_to_regex(p) for p in (exclude or [])]
        self.extensions = tuple(
            e.lower() if e.startswith(".") else f".{e.lower()}"
            for e in (extensions or _DEFAULT_EXTENSIONS)
        )
        self.strip_prefix = strip_prefix.strip("/")
        self.timeout = timeout
        self.max_files = max_files
        mode = (mode or "auto").strip().lower()
        if mode not in _VALID_MODES:
            # 拼错的 mode 若静默回退到 auto，使用者会以为自己指定的路径生效了，
            # 而实际抓取行为与预期不同——这类「配置没报错但没生效」最难查。
            raise SourceError(
                f"mode 只能是 {'/'.join(_VALID_MODES)}，收到 {mode!r}"
            )
        self.mode = mode

    def describe(self) -> str:
        return (
            "GitHub 仓库文档源：tarball 流式解包或 trees+raw 逐文件抓取，"
            "按仓库规模自动择优；只落盘命中筛选条件的文档，无需认证与 git。"
        )

    # -- 内部 ------------------------------------------------------------- #

    def _wanted(self, rel: str) -> bool:
        """判断一个仓库内相对路径是否要抓。"""
        if not rel.lower().endswith(self.extensions):
            return False
        if any(rx.match(rel) for rx in self._exclude):
            return False
        if self._include and not any(rx.match(rel) for rx in self._include):
            return False
        return True

    def _safe_dest(self, workdir: Path, rel: str) -> Path:
        """把仓库内相对路径映射到缓存目录下的落盘路径。

        必须自己拼路径并复核，不能信 tar 里的成员名：构造恶意 tar 让成员名
        带 ``../`` 或绝对路径，就能把文件写到缓存目录之外（经典的 tar 穿越）。
        官方仓库不至于如此，但这段代码接受任意 ``repo`` 参数，只要来源可配置
        就得按不可信处理。
        """
        dest = (workdir / rel).resolve()
        root = workdir.resolve()
        if not dest.is_relative_to(root):
            raise SourceError(f"归档成员路径越界，已拒绝: {rel!r}")
        return dest

    def _emit(
        self, workdir: Path, rel: str, data: bytes, content_hash: str
    ) -> FetchedDocument:
        """落盘并封装成 :class:`FetchedDocument`（两条抓取路径共用）。"""
        dest = self._safe_dest(workdir, rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

        shown = rel
        if self.strip_prefix and shown.startswith(self.strip_prefix + "/"):
            shown = shown[len(self.strip_prefix) + 1 :]

        return FetchedDocument(
            uri=f"github://{self.repo}@{self.ref}/{rel}",
            rel_path=shown,
            local_path=dest,
            content_hash=content_hash,
            metadata={"repo": self.repo, "ref": self.ref, "repo_path": rel},
        )

    # -- 抓取 ------------------------------------------------------------- #

    def fetch(self, workdir: Path) -> Iterator[FetchedDocument]:
        mode = self.mode
        tree: list[dict[str, Any]] | None = None

        if mode in ("auto", "api"):
            try:
                tree = self._list_tree()
            except SourceError:
                if mode == "api":
                    raise
                # auto 下 trees API 不可用（限流 / 仓库过大被截断）不该让同步失败，
                # tarball 路径本来就不依赖它。
                tree = None

        if mode == "auto":
            mode = "tarball" if tree is None else self._choose_mode(tree)

        if mode == "api":
            try:
                count = yield from self._fetch_via_api(workdir, tree or [])
            except _ApiPathUnavailable as exc:
                if self.mode == "api":
                    # 用户显式点名 api，就不要替他改主意——静默换路径会让
                    # 「我明明配了 api」和实际行为对不上。
                    raise
                # 实测过的真实场景：部分网络能连 codeload / api.github.com，
                # 却连不上 raw.githubusercontent.com。这时 tarball 仍然可用，
                # 没必要让整个源同步失败。
                get_logger(__name__).warning(
                    "%s@%s: api 路径不可用（%s），回退 tarball", self.repo, self.ref, exc
                )
                count = yield from self._fetch_via_tarball(workdir)
        else:
            count = yield from self._fetch_via_tarball(workdir)

        if count == 0:
            raise SourceError(
                f"{self.repo}@{self.ref} 未匹配到任何文档文件。"
                "请检查 params.include / params.extensions 是否与仓库实际结构一致。"
            )

    # -- 路径一：trees API + raw 逐文件 ------------------------------------ #

    def _list_tree(self) -> list[dict[str, Any]]:
        """拉取全量文件清单（一次请求）。

        Returns:
            ``[{"path": ..., "size": int, "sha": str}, ...]``，只含 blob。

        Raises:
            SourceError: 请求失败，或仓库文件数超过 GitHub 的返回上限被截断
                （截断的清单会漏文件，拿它当全量用会静默少抓）。
        """
        url = f"https://api.github.com/repos/{self.repo}/git/trees/{self.ref}?recursive=1"
        resp = _http_get_stream(url, self.timeout)
        try:
            payload = json.loads(resp.content)
        except (ValueError, AttributeError) as exc:
            raise SourceError(f"trees API 返回的不是合法 JSON: {url}") from exc
        finally:
            close = getattr(resp, "close", None)
            if callable(close):
                close()

        if payload.get("truncated"):
            raise SourceError(
                f"{self.repo}@{self.ref} 的文件清单被 GitHub 截断，"
                "无法用 api 模式保证完整；请把 params.mode 设为 tarball。"
            )
        return [t for t in payload.get("tree", []) if t.get("type") == "blob"]

    def _choose_mode(self, tree: list[dict[str, Any]]) -> str:
        """按「想要的字节 / 全仓字节」在两条路径间择优。"""
        total = sum(int(t.get("size") or 0) for t in tree) or 1
        wanted = sum(
            int(t.get("size") or 0) for t in tree if self._wanted(t.get("path", ""))
        )
        ratio = wanted / total
        get_logger(__name__).info(
            "%s@%s: 命中 %d/%d 文件、%.1f%% 字节 -> %s",
            self.repo, self.ref, sum(1 for t in tree if self._wanted(t.get("path", ""))),
            len(tree), ratio * 100, "tarball" if ratio >= _AUTO_TARBALL_RATIO else "api",
        )
        return "tarball" if ratio >= _AUTO_TARBALL_RATIO else "api"

    def _fetch_one_raw(self, workdir: Path, item: dict[str, Any]) -> tuple[str, bytes]:
        """取一个文件：本地缓存命中就不下载。

        缓存判据是「本地文件算出的 git blob sha 等于 trees API 给的 sha」。
        用 git 的对象 ID 而不是自己另存一份索引，好处是判据由文件本身携带——
        缓存目录被手动删掉一半也不会算错，只是那部分重新下载。

        注意 git blob sha **只用于这里的下载去重，不作为文档的 content_hash**。
        content_hash 决定增量判定，必须与抓取路径无关：两者混用的话，
        auto 模式某次从 tarball 切到 api，全库文档的哈希会集体对不上，
        于是整个知识库白白重嵌一遍——不报错，只是慢和费钱。
        """
        rel = item["path"]
        sha = str(item.get("sha") or "")
        dest = self._safe_dest(workdir, rel)
        if sha and dest.is_file():
            try:
                cached = dest.read_bytes()
                if _git_blob_sha(cached) == sha:
                    return rel, cached
            except OSError:
                pass  # 读不出来就当没缓存，重新下载

        url = f"https://raw.githubusercontent.com/{self.repo}/{self.ref}/{rel}"
        resp = _http_get_stream(url, self.timeout)
        try:
            data = resp.content
        finally:
            close = getattr(resp, "close", None)
            if callable(close):
                close()
        return rel, data

    def _fetch_via_api(
        self, workdir: Path, tree: list[dict[str, Any]]
    ) -> Iterator[FetchedDocument]:
        wanted = [
            t for t in tree
            if self._wanted(t.get("path", "")) and int(t.get("size") or 0) <= _MAX_FILE_BYTES
        ]
        # 排序保证产出顺序稳定：顺序不稳时配合 --limit 会每次抓到不同子集，
        # 增量状态就会来回抖动。
        wanted.sort(key=lambda t: t["path"])
        if self.max_files:
            wanted = wanted[: self.max_files]

        count = 0
        # 分批并发：批内并发抹掉往返延迟，批间顺序产出保持稳定。
        # 不用 executor.map 一次吃完全部，是为了让 --limit 与调用方的提前
        # 中断能真正省下后面的请求，而不是先把几百个下载都排进队列。
        for start in range(0, len(wanted), _API_WORKERS):
            batch = wanted[start : start + _API_WORKERS]
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=_API_WORKERS) as pool:
                    results = list(
                        pool.map(lambda it: self._fetch_one_raw(workdir, it), batch)
                    )
            except SourceError as exc:
                if count == 0:
                    raise _ApiPathUnavailable(str(exc)) from exc
                raise
            for rel, data in results:
                yield self._emit(workdir, rel, data, content_sha256(data))
                count += 1
        return count

    # -- 路径二：codeload tarball 流式解包 --------------------------------- #

    def _fetch_via_tarball(self, workdir: Path) -> Iterator[FetchedDocument]:
        url = f"https://codeload.github.com/{self.repo}/tar.gz/refs/heads/{self.ref}"
        resp = _http_get_stream(url, self.timeout)

        # r|gz = 顺序流式读取：不要求底层可 seek，因此可以直接吃 HTTP 响应流，
        # 全程不把整个 tar 落盘或读进内存。代价是只能从头读一遍，不能回退——
        # 对「遍历所有成员各处理一次」的场景正合适。
        raw = getattr(resp, "raw", None)
        stream = raw if raw is not None else io.BytesIO(resp.content)
        if raw is not None:
            # requests 默认不自动解 gzip 传输编码，但 .tar.gz 是内容本身而非
            # 传输编码，这里要拿到未经改动的原始字节。
            raw.decode_content = False

        count = 0
        try:
            with tarfile.open(fileobj=stream, mode="r|gz") as tf:
                for member in tf:
                    if not member.isfile():
                        continue
                    # tarball 的第一层是 "<repo>-<ref>/"，去掉它才是仓库内路径
                    parts = member.name.split("/", 1)
                    if len(parts) < 2:
                        continue
                    rel = parts[1]
                    if not self._wanted(rel):
                        continue
                    if member.size > _MAX_FILE_BYTES:
                        continue

                    fh = tf.extractfile(member)
                    if fh is None:
                        continue
                    data = fh.read()

                    yield self._emit(workdir, rel, data, content_sha256(data))
                    count += 1
                    if self.max_files and count >= self.max_files:
                        # 到量即停：tar 是顺序流，提前 break 会让连接被关闭，
                        # 剩余字节不再下载，这正是我们想要的省流量行为。
                        break
        except tarfile.TarError as exc:
            raise SourceError(f"解包失败 {url}: {exc}") from exc
        finally:
            close = getattr(resp, "close", None)
            if callable(close):
                close()
        return count


__all__ = ["GitHubRepoSource"]
