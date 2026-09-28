"""文档源接入层冒烟（全离线，不联网、不连 Milvus）。

运行：python scripts/smoke_sources.py
覆盖：
1. glob -> 正则的语义（``**`` 跨目录、``*`` 不跨目录、``?`` 单字符）
2. 注册表：未知类型 / 缺必填参数 / 正常创建
3. 清单解析：正常 / 文件不存在 / 非法 JSON / 结构错 / 缺字段
4. doc_id 稳定性（增量与删除检测的前提）
5. LocalDirectorySource 的筛选与空结果报错
6. SourceSyncer 增量语义：首轮全入、次轮全跳、改动重入、删除清理、
   ``--limit`` 截断时**不做删除**、dry-run 不落任何副作用
7. 真实的 config/sources.json 能被解析，且每条源都能装配出实例

第 6 项是这套东西最容易写错也最贵的地方：删除逻辑错一次，
就会把库里好好的文档整片抹掉，所以这里逐条钉死。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# ruff: noqa: E402

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _smoke_redis import isolate_redis_keyspace

# 同步器跑完会 bump 语料代次（sources/runner.py），那是一次真实的 Redis 写。
# 理由同 smoke_documents：代次键没有 TTL 也没有批量删除入口，落进共用实例的
# 生产前缀就是永久残留。
isolate_redis_keyspace("sources")

from sources import (
    SourceError,
    SourcePlugin,
    SourceSpec,
    SourceSyncer,
    create_source,
    list_source_plugins,
    load_manifest,
    make_doc_id,
    register_source_plugin,
    unregister_source_plugin,
)
from sources.github_repo import GitHubRepoSource, _glob_to_regex
from sources.local_dir import LocalDirectorySource

passed = 0
failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} {detail}")


def expect_error(name: str, fn, exc_type=SourceError) -> None:
    try:
        fn()
    except exc_type:
        check(name, True)
    except Exception as exc:  # noqa: BLE001
        check(name, False, f"抛的是 {type(exc).__name__}: {exc}")
    else:
        check(name, False, "没有抛错")


print("== 1. glob -> 正则 ==")
_cases = [
    ("src/oss/**", "src/oss/langchain/index.mdx", True),
    ("src/oss/**", "src/oss/a.mdx", True),
    ("src/oss/**", "src/langsmith/a.mdx", False),
    # 单星不跨目录：这条错了会让 `src/oss/*.mdx` 把整棵子树都拖进来
    ("src/oss/*.mdx", "src/oss/index.mdx", True),
    ("src/oss/*.mdx", "src/oss/langchain/index.mdx", False),
    ("**/partials/**", "docs/modules/partials/x.adoc", True),
    ("**/partials/**", "docs/modules/pages/x.adoc", False),
    ("**/_index.md", "content/operate/_index.md", True),
    ("**/_index.md", "content/operate/index.md", False),
    ("a?c.md", "abc.md", True),
    ("a?c.md", "abbc.md", False),
    # 正则元字符必须被转义，否则 `.` 会匹配任意字符
    ("a.md", "axmd", False),
]
for pattern, path, want in _cases:
    got = bool(_glob_to_regex(pattern).match(path))
    check(f"{pattern!r} vs {path!r} -> {want}", got == want, f"实际 {got}")

print("== 2. 注册表 ==")
names = {p["name"] for p in list_source_plugins()}
check("内置源已注册", {"github_repo", "local_dir"} <= names, str(names))
expect_error("未知类型报错", lambda: create_source("no_such_source", {}))
expect_error("缺必填参数报错", lambda: create_source("github_repo", {"ref": "main"}))
src = create_source("github_repo", {"repo": "a/b", "include": ["docs/**"]})
check("正常创建 github_repo", isinstance(src, GitHubRepoSource))

print("== 3. 清单解析 ==")
_tmp = Path(tempfile.mkdtemp(prefix="rag4c-src-"))

good = _tmp / "good.json"
good.write_text(
    json.dumps({"sources": [
        {"name": "s1", "type": "local_dir", "params": {"path": "x"}},
        {"name": "s2", "type": "local_dir", "dataset_id": "kb2", "enabled": False,
         "params": {"path": "y"}, "metadata": {"project": "P"}},
    ]}),
    encoding="utf-8",
)
specs = load_manifest(good)
check("解析出 2 条源", len(specs) == 2, str(len(specs)))
check("dataset_id 默认取 name", specs[0].dataset_id == "s1", specs[0].dataset_id)
check("dataset_id 可显式覆盖", specs[1].dataset_id == "kb2", specs[1].dataset_id)
check("enabled 默认 True", specs[0].enabled is True)
check("enabled=false 被读到", specs[1].enabled is False)
check("metadata 透传", specs[1].metadata == {"project": "P"}, str(specs[1].metadata))

expect_error("清单不存在报错", lambda: load_manifest(_tmp / "nope.json"))
bad_json = _tmp / "bad.json"
bad_json.write_text("{not json", encoding="utf-8")
expect_error("非法 JSON 报错", lambda: load_manifest(bad_json))
bad_shape = _tmp / "shape.json"
bad_shape.write_text(json.dumps({"sources": "oops"}), encoding="utf-8")
expect_error("结构错报错", lambda: load_manifest(bad_shape))
no_type = _tmp / "notype.json"
no_type.write_text(json.dumps({"sources": [{"name": "x"}]}), encoding="utf-8")
expect_error("缺 type 报错", lambda: load_manifest(no_type))

print("== 4. doc_id 稳定性 ==")
a1 = make_doc_id("milvus", "zh-CN/getstarted/install.md")
a2 = make_doc_id("milvus", "zh-CN/getstarted/install.md")
b = make_doc_id("milvus", "zh-CN/getstarted/other.md")
c = make_doc_id("langchain", "zh-CN/getstarted/install.md")
check("同输入 -> 同 doc_id", a1 == a2, a1)
check("不同路径 -> 不同 doc_id", a1 != b)
check("不同源名 -> 不同 doc_id", a1 != c)
check("doc_id 带可读前缀", a1.startswith("milvus-install-"), a1)
check("doc_id 长度可控", len(a1) < 80, str(len(a1)))

print("== 5. LocalDirectorySource 筛选 ==")
docroot = _tmp / "docs"
(docroot / "guide").mkdir(parents=True)
(docroot / "draft").mkdir(parents=True)
(docroot / "a.md").write_text("# A\n正文一", encoding="utf-8")
(docroot / "guide" / "b.md").write_text("# B\n正文二", encoding="utf-8")
(docroot / "draft" / "c.md").write_text("# C\n草稿", encoding="utf-8")
(docroot / "img.png").write_bytes(b"\x89PNG")

got = [d.rel_path for d in LocalDirectorySource(str(docroot)).fetch(_tmp)]
check("按扩展名过滤掉 png", got == ["a.md", "draft/c.md", "guide/b.md"], str(got))
got = [d.rel_path for d in LocalDirectorySource(str(docroot), exclude=["draft/**"]).fetch(_tmp)]
check("exclude 生效", got == ["a.md", "guide/b.md"], str(got))
got = [d.rel_path for d in LocalDirectorySource(str(docroot), include=["guide/**"]).fetch(_tmp)]
check("include 生效", got == ["guide/b.md"], str(got))
got = [d.rel_path for d in LocalDirectorySource(str(docroot), max_files=2).fetch(_tmp)]
check("max_files 生效", len(got) == 2, str(got))
expect_error(
    "无匹配时报错而不是静默返回空",
    lambda: list(LocalDirectorySource(str(docroot), include=["nothing/**"]).fetch(_tmp)),
)
expect_error(
    "目录不存在报错",
    lambda: list(LocalDirectorySource(str(_tmp / "ghost")).fetch(_tmp)),
)
one_iter = LocalDirectorySource(str(docroot), include=["a.md"]).fetch(docroot.parent / "workdir_files")
one = next(one_iter)
staged_alive = one.local_path.exists()  # 生成器推进前副本必须活着且可读
try:
    next(one_iter, None)
finally:
    one_iter.close()
check("content_hash 非空", len(one.content_hash) == 64, one.content_hash)
# local_dir 现在走 verified staging（fab924c 起）：fetch 时把字节复制到
# workdir/_verified 的随机名校验副本（uuid + 原后缀）交给消费方，迭代结束后
# 生成器负责清理（staged.unlink）——不再直接指原文件。模块 docstring 那句
# 「不复制文件」已过期，以行为为准。副本活着时必须落在 workdir/_verified/ 下，
# 与 runner（源缓存 files/ 下）同一形态；close() 之后必须被回收。
check(
    "local_path 指向 verified staging 副本",
    staged_alive
    and one.local_path.parent.parent.name == "workdir_files"
    and one.local_path.parent.name == "_verified"
    and one.local_path.suffix == ".md"
    and not one.local_path.exists(),
    str(one.local_path),
)


class FakePipeline:
    """只记录调用，不真正写库。

    ``delete_document`` 的签名必须跟得上 :class:`indexing.ingest.IngestPipeline`：
    SourceSyncer 是按鸭子类型调用管线的，替换式重入库那条路会显式传
    ``unregister=False``。这个桩少一个形参，整源同步就会在「单篇失败不中断整源」
    里被吞成 3 篇全失败——桩和真身脱节时，报出来的现象离根因很远。
    """

    def __init__(self) -> None:
        self.added: list[str] = []
        self.deleted: list[str] = []
        self.unregistered: list[bool] = []
        self.ensured = 0

    def ensure_collection(self) -> None:
        self.ensured += 1

    def delete_document(self, doc_id: str, unregister: bool = True) -> int:
        self.deleted.append(doc_id)
        self.unregistered.append(unregister)
        return 1

    def add_file(self, path, doc_id, **kwargs):
        self.added.append(doc_id)
        self.last_kwargs = kwargs
        return type("R", (), {"chunk_count": 3})()


print("== 6. SourceSyncer 增量语义 ==")
cache = _tmp / "cache"
spec = SourceSpec(
    name="demo",
    type="local_dir",
    dataset_id="kb-demo",
    params={"path": str(docroot)},
    metadata={"project": "Demo"},
)

# dry-run：不碰管线、不写状态
fp = FakePipeline()
rep = SourceSyncer(fp, cache).sync(spec, dry_run=True)
check("dry-run 抓到 3 篇", rep.fetched == 3, rep.summary())
check("dry-run 不调用管线", fp.added == [] and fp.ensured == 0, str(fp.added))
# 状态文件按写方真实落点算（source-id / 派生 cache key），不许自己拼
# `cache/<name>/_state.json`——那与真实落点不同名，本脚本曾因此从 :286
# 起持续 FileNotFoundError（doc_count 恒 0 缺陷的同款拼法）。
syncer_probe = SourceSyncer(fp, cache)
check("dry-run 不写状态文件", not syncer_probe._state_path(spec).exists())  # noqa: SLF001
# dry-run + limit 是「先小批量看一眼」最常用的组合；limit 曾因写在
# 非 dry-run 分支里而在这条路径上完全失效，这里钉死。
rep = SourceSyncer(FakePipeline(), cache).sync(spec, dry_run=True, limit=2)
check("dry-run 下 limit 生效", rep.fetched == 2 and rep.ingested == 2, rep.summary())

# 首轮：全部入库
fp = FakePipeline()
syncer = SourceSyncer(fp, cache)
rep = syncer.sync(spec)
check("首轮入库 3 篇", rep.ingested == 3 and rep.skipped == 0, rep.summary())
check("首轮统计 chunk 数", rep.chunks == 9, str(rep.chunks))
check("首轮不删任何东西", fp.deleted == [], str(fp.deleted))
check("ensure_collection 被调用", fp.ensured == 1, str(fp.ensured))
check("dataset_id 透传给 add_file", fp.last_kwargs.get("dataset_id") == "kb-demo",
      str(fp.last_kwargs))
check("metadata 合并了源级与文档级",
      fp.last_kwargs["metadata"].get("project") == "Demo"
      and "rel_path" in fp.last_kwargs["metadata"],
      str(fp.last_kwargs["metadata"]))

# 次轮：内容没变，全跳过
fp2 = FakePipeline()
rep = SourceSyncer(fp2, cache).sync(spec)
check("次轮全部跳过", rep.skipped == 3 and rep.ingested == 0, rep.summary())
check("次轮零写入", fp2.added == [], str(fp2.added))

# 本段以下断言按现行语义校准（2026-09-27）。原断言钉的是「管线同步先删后写」的
# 旧行为；现行 runner 在 replace 时走 reindex/state-machine 的 durable 写路径
# （chunk 级差量，FakePipeline 不再收到同步 delete_document），上游删除走
# durable delete 请求（无 ledger/tenant 的裸场景下显式报 "authority unavailable"
# 进 failed，而不是假装删掉）——那才是现在"删错比漏删危险"的 fail-closed 出口。

# force：无视哈希重入
fp3 = FakePipeline()
rep = SourceSyncer(fp3, cache).sync(spec, force=True)
check("force 重新入库全部", rep.ingested == 3 and rep.skipped == 0, rep.summary())
check("force 重入不伪造同步删除", fp3.deleted == [], str(fp3.deleted))

# 改一篇：只有它重入
(docroot / "a.md").write_text("# A\n正文一改过了", encoding="utf-8")
fp4 = FakePipeline()
rep = SourceSyncer(fp4, cache).sync(spec)
check("改动后只重入 1 篇", rep.ingested == 1 and rep.skipped == 2, rep.summary())
changed_id = make_doc_id("demo", "a.md")
check("重入的是被改的那篇", fp4.added == [changed_id], str(fp4.added))
check("重入不伪造同步删除", fp4.deleted == [], str(fp4.deleted))

# 删一篇：上游没了。本 spec 无 tenant/ledger（durable delete authority 不可用），
# 显式进 failed 而不是假装删掉；状态保留原条目，等有权威的一侧来清。
(docroot / "draft" / "c.md").unlink()
fp5 = FakePipeline()
rep = SourceSyncer(fp5, cache).sync(spec)
gone_id = make_doc_id("demo", "draft/c.md")
check("上游删除不伪造同步清理", rep.removed == 0 and fp5.deleted == [], rep.summary())
check("无删除权威时显式报失败（fail-closed，不静默吞）",
      any(doc_id == gone_id for doc_id, _msg in rep.failed), str(rep.failed))
state_after_gone = json.loads(
    SourceSyncer(fp5, cache)._state_path(spec).read_text(encoding="utf-8")  # noqa: SLF001
)
check("被删文档状态保留待权威清理", gone_id in state_after_gone["docs"],
      str(list(state_after_gone["docs"])))
fp6 = FakePipeline()
rep = SourceSyncer(fp6, cache).sync(spec)
check("清理不会重复触发", rep.removed == 0 and fp6.deleted == [], rep.summary())

# limit 截断：绝不能把没轮到的文档当成「上游已删除」
(docroot / "guide" / "d.md").write_text("# D\n新文档", encoding="utf-8")
fp7 = FakePipeline()
syncer7 = SourceSyncer(fp7, cache)
rep = syncer7.sync(spec, force=True, limit=1)
check("limit 只入库 1 篇", rep.ingested == 1, rep.summary())
check("limit 截断时不做删除", rep.removed == 0 and len(fp7.deleted) == 0,
      f"removed={rep.removed} deleted={fp7.deleted}")
state = json.loads(syncer7._state_path(spec).read_text(encoding="utf-8"))  # noqa: SLF001
# 截断前状态里有 a、b 两篇（c 上一轮已无权威清理），本轮只轮到 a。
# 关键不变量：没轮到的 b 必须原样留在状态里——丢了它，下一轮就会把一篇
# 早就入过库的文档当成新文档重嵌一遍。而本轮同样没轮到的新文件 guide/d.md
# 理应还不在状态里，它下一轮才会被认成新增。
check("limit 截断时未处理的旧条目被保留",
      make_doc_id("demo", "guide/b.md") in state["docs"], str(list(state["docs"])))
check("limit 截断时没轮到的新文件不会被误记为已入库",
      make_doc_id("demo", "guide/d.md") not in state["docs"], str(list(state["docs"])))

# 单篇失败：不写进状态，下次还会重试
class FlakyPipeline(FakePipeline):
    def add_file(self, path, doc_id, **kwargs):
        if doc_id == make_doc_id("demo", "a.md"):
            raise RuntimeError("模拟嵌入服务抖动")
        return super().add_file(path, doc_id, **kwargs)


fp8 = FlakyPipeline()
syncer8 = SourceSyncer(fp8, cache)
rep = syncer8.sync(spec, force=True)
# 现行语义两处 fail-closed：flaky 那篇按单篇失败隔离（其余照常入库）；
# 被改文档的旧登记因上一轮后无删除权威而追加一条 durable-authority 失败——
# 两条都显式进 failed，绝不静默吞。
check("单篇失败不中断整源",
      rep.ingested == 2
      and any(msg == "模拟嵌入服务抖动" for _doc, msg in rep.failed)
      and any("durable source delete authority" in msg for _doc, msg in rep.failed),
      rep.summary())
state = json.loads(syncer8._state_path(spec).read_text(encoding="utf-8"))  # noqa: SLF001
# 失败文档的重试语义按现行实现钉住：失败篇**不更新** state（hash 保持上一轮
# fp4 成功入库后的值），下一轮按 hash/force 判定仍会对它重跑——"失败会重试"
# 不变，只是不再假装条目不存在。（本序列里 a.md 在 fp4 那轮已成功入库过改后
# 内容，所以这里 state 的 hash 就是当前文件 hash——它表示"最后成功同步的版本"。）
flaky_target = make_doc_id("demo", "a.md")
flaky_iter = LocalDirectorySource(str(docroot), include=["a.md"]).fetch(
    docroot.parent / "workdir_files"
)
current = next(flaky_iter)
flaky_iter.close()
check("失败的文档保留待重试（state 保留最后成功版本）",
      flaky_target in state["docs"]
      and state["docs"][flaky_target]["hash"] == current.content_hash,
      f"state_hash={state['docs'].get(flaky_target, {}).get('hash', '')[:12]}")

print("== 7. 抓取中途中断：进度必须保住 ==")
# 实测踩到的场景：抓了 85 篇后连接被重置，异常穿出 sync()，状态文件从未
# 落盘——下一轮把这 85 篇全部重嵌一遍。这里钉死「已完成的部分要留下」。
from sources.base import DocumentSource, FetchedDocument, content_sha256  # noqa: E402


class HalfwaySource(DocumentSource):
    """产出 2 篇后抛错，模拟网络中途断掉。"""

    name = "halfway"

    def describe(self) -> str:
        return "测试用：产出两篇后中断"

    def fetch(self, workdir):  # noqa: ARG002
        for i in range(2):
            p = docroot / f"halfway_{i}.md"
            p.write_text(f"# H{i}\n内容 {i}", encoding="utf-8")
            data = p.read_bytes()
            yield FetchedDocument(
                uri=f"test://h{i}", rel_path=f"halfway_{i}.md",
                local_path=p, content_hash=content_sha256(data),
            )
        raise SourceError("模拟连接被重置")


register_source_plugin(SourcePlugin(
    name="halfway", factory=lambda p, s: HalfwaySource(), describe=lambda: "测试源",
))
half_cache = _tmp / "cache_half"
half_spec = SourceSpec(name="half", type="halfway", dataset_id="kb-half")

fp9 = FakePipeline()
syncer9 = SourceSyncer(fp9, half_cache)
rep = syncer9.sync(half_spec)
check("中断不抛到调用方", rep.fetch_error != "", rep.summary())
check("中断前入库的 2 篇算数", rep.ingested == 2, rep.summary())
half_state = json.loads(
    syncer9._state_path(half_spec).read_text(encoding="utf-8")  # noqa: SLF001
)
check("中断时状态照常落盘", len(half_state["docs"]) == 2, str(list(half_state["docs"])))
check("summary 明说没走完", "未走完" in rep.summary(), rep.summary())

# 重跑：已完成的应当跳过，而不是重嵌
fp10 = FakePipeline()
rep = SourceSyncer(fp10, half_cache).sync(half_spec)
check("重跑从断点续（已完成的跳过）", rep.skipped == 2 and rep.ingested == 0,
      rep.summary())
# 中断这一轮绝不能做删除清理：没轮到的文档不等于上游删了它
check("中断轮次不做删除清理", rep.removed == 0 and fp10.deleted == [], str(fp10.deleted))
unregister_source_plugin("halfway")

print("== 8. github 抓取路径选择（离线，喂假 tree） ==")
from sources.github_repo import _AUTO_TARBALL_RATIO, _git_blob_sha  # noqa: E402

expect_error("非法 mode 立刻报错而不是静默回退",
             lambda: GitHubRepoSource(repo="a/b", mode="tarbal"))
for m in ("auto", "tarball", "api"):
    check(f"mode={m} 可接受", GitHubRepoSource(repo="a/b", mode=m).mode == m)

# git blob sha 必须和 git 对得上，否则 api 模式的缓存命中判据永远为假，
# 每次同步都会把所有文件重下一遍（不报错，只是白花时间）。
check("git blob sha 算法正确",
      _git_blob_sha(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a",
      _git_blob_sha(b"hello\n"))
check("空文件的 blob sha 正确",
      _git_blob_sha(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391")

# 大仓库里只要一小片 -> 应当选 api
big = GitHubRepoSource(repo="redis/docs", include=["content/operate/oss_and_stack/**"])
tree_big = (
    [{"path": f"src/code_{i}.go", "size": 10_000, "type": "blob"} for i in range(500)]
    + [{"path": f"content/operate/oss_and_stack/d{i}.md", "size": 8_000, "type": "blob"}
       for i in range(50)]
)
check("大仓小切片 -> api", big._choose_mode(tree_big) == "api",
      f"ratio={sum(t['size'] for t in tree_big if big._wanted(t['path']))/sum(t['size'] for t in tree_big):.3f}")

# 全是文档的小仓库 -> 应当选 tarball（逐文件往返反而更亏）
small = GitHubRepoSource(repo="milvus-io/milvus-docs", include=["site/**"])
tree_small = [{"path": f"site/zh-CN/d{i}.md", "size": 9_000, "type": "blob"}
              for i in range(40)]
check("文档占绝大多数 -> tarball", small._choose_mode(tree_small) == "tarball")
check("阈值取值合理（0<r<1）", 0.0 < _AUTO_TARBALL_RATIO < 1.0, str(_AUTO_TARBALL_RATIO))

print("== 8. 真实清单 config/sources.json ==")
real = _PROJECT_ROOT / "config" / "sources.json"
check("清单文件存在", real.is_file(), str(real))
if real.is_file():
    real_specs = load_manifest(real)
    check("清单含 5 个目标项目", len(real_specs) == 5, str([s.name for s in real_specs]))
    want = {"springboot", "langchain", "langgraph", "milvus", "redis-stack"}
    check("覆盖用户点名的 5 个项目", {s.name for s in real_specs} == want,
          str({s.name for s in real_specs}))
    for s in real_specs:
        try:
            inst = create_source(s.type, s.params, None)
            check(f"{s.name} 可装配", inst is not None)
        except SourceError as exc:
            check(f"{s.name} 可装配", False, str(exc))
    check("dataset_id 两两不同",
          len({s.dataset_id for s in real_specs}) == len(real_specs))

print()
print(f"结果：{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
