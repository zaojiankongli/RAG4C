"""重建 Milvus 集合（导出 -> 重建 -> 回填），用于无法原地修改的 schema 变更。

**为什么需要它。** Milvus 的一部分 schema 属性建集合时就固化了，之后改不动：
最典型的是 ``text`` 字段的 ``analyzer_params``（BM25 分词器）。想把分词器从
``standard`` 换成 ``chinese``，除了重建集合没有别的办法。集合里已有的数据不能
丢，所以重建必须是「导出 -> 重建 -> 回填」三步，而不是简单的 drop。

**为什么不需要重新嵌入。** 导出时连 ``dense_vector`` 一起导，回填时原样写回，
所以不调用嵌入服务、不花钱、也不引入嵌入模型版本漂移。稀疏向量则**必须**由
Milvus 重新计算（它是 BM25 Function 的输出字段，应用层写不了，也正是这次要
换分词器的那一路），回填时不提供，由 Milvus 在写路径上自动生成。

**安全设计。** 默认只导出、不改动任何东西（dry-run）。真正执行需要显式
``--yes``；且无论如何都会先把备份写到磁盘，再动集合。备份是普通 JSON，
可以用 ``--restore-from`` 单独回填，即便重建中途失败也能救回来。

用法::

    # 1. 先看会动什么（不改任何东西）
    python scripts/rebuild_collection.py

    # 2. 确认无误后执行
    python scripts/rebuild_collection.py --yes

    # 3. 万一中途失败，用备份单独回填
    python scripts/rebuild_collection.py --restore-from backups/xxx.json --yes
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from config.settings import get_settings  # noqa: E402
from core.milvus_client import RagMilvusClient  # noqa: E402

#: 不能导出的字段：稀疏向量是 BM25 Function 的输出，应用层写不进去，
#: 也正是重建要让 Milvus 用新分词器重算的那一份。
_SKIP_EXPORT_FIELDS = frozenset({"sparse_vector"})

#: 写死的导出字段清单曾经漏掉 document_revision / content_revision 这两个
#: 后加的标量字段，于是「导出 -> 重建 -> 回填」在回填那一步炸掉
#: （InsertMissedField），而集合已经被 drop 了——备份在、但恢复要绕路。
#: 改成从 schema 动态取：新增字段自动跟上，不会再有人忘了改这里。
_EXPORT_FIELDS_FALLBACK = [
    "chunk_id", "doc_id", "text", "text_hash", "created_at", "updated_at",
    "source", "parent_chunk_id", "acl", "tenant_id", "dataset_id",
    "metadata", "dense_vector",
]

_PAGE = 500


def _export_fields(client: Any, collection: str) -> list[str]:
    """要导出的字段名 = 集合 schema 全部字段 - 不可写的那些。

    拿不到 schema 时（权限不足 / 集合刚被删）退回历史清单，让 dry-run 至少
    还能跑完——但会在输出里明确提示走了兜底，因为那意味着可能又漏字段。
    """
    try:
        info = client.describe_collection(collection)
        names = [f["name"] for f in info.get("fields", []) if f.get("name")]
    except Exception:  # noqa: BLE001 - 拿不到就退回，不在这里把流程掐断
        return list(_EXPORT_FIELDS_FALLBACK)
    usable = [n for n in names if n not in _SKIP_EXPORT_FIELDS]
    return usable or list(_EXPORT_FIELDS_FALLBACK)


def _export(client: Any, collection: str) -> list[dict[str, Any]]:
    """分页导出整个集合。

    用 ``chunk_id > 上一页最后一个`` 做游标而不是 offset 分页：Milvus 的
    offset 深分页有上限，集合稍大就会被拒；主键游标没有这个限制，且主键
    有序保证不重不漏。
    """
    fields = _export_fields(client, collection)
    rows: list[dict[str, Any]] = []
    cursor = ""
    while True:
        expr = f'chunk_id > "{cursor}"' if cursor else "chunk_id != ''"
        page = client.query(
            collection,
            filter=expr,
            output_fields=fields,
            limit=_PAGE,
        )
        if not page:
            break
        page.sort(key=lambda r: r["chunk_id"])
        rows.extend(page)
        cursor = page[-1]["chunk_id"]
        print(f"  已导出 {len(rows)} 行…")
        if len(page) < _PAGE:
            break
    return rows


#: 标量字段的空串兜底。这些字段在线加过时，旧行上可能是 NULL，而 insert
#: 不接受 NULL（非 nullable 且无 default_value）。
_SCALAR_FALLBACKS = ("dataset_id", "tenant_id", "parent_chunk_id", "acl", "source")

#: 整数字段的 0 兜底。同上，缺字段时 Milvus 报 InsertMissedField。
_INT_FALLBACKS = ("document_revision", "content_revision", "created_at", "updated_at")


def _normalise(
    row: dict[str, Any],
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """把导出的行整理成可直接 insert 的形状。

    Milvus 查询结果可能夹带 ``id`` 之类的额外键，也可能把 JSON 字段以字符串
    形式返回；insert 时多余的键会被拒，所以按 schema 白名单重建一份。

    Args:
        row: 导出的原始行。
        fields: 目标集合的字段名（``_export_fields`` 的结果）。``None`` 时退回
            历史清单——只适用于回填**旧备份**：那批行里本来就没有后加的字段，
            按老清单过滤恰好对得上。

    补齐缺失字段而不是只做白名单过滤，是这次踩坑的直接修复：导出清单漏了
    ``document_revision`` / ``content_revision``，于是回填时每行都缺必填字段、
    整批被拒，而集合那时已经被 drop 了。改用目标 schema 当白名单之后，
    备份里没有的字段会按类型补上默认值，而不是把整批数据卡死。
    """
    allow = fields if fields is not None else _EXPORT_FIELDS_FALLBACK
    out = {k: row[k] for k in allow if k in row}
    meta = out.get("metadata")
    if isinstance(meta, str):
        try:
            out["metadata"] = json.loads(meta)
        except json.JSONDecodeError:
            out["metadata"] = {}
    if out.get("metadata") is None:
        out["metadata"] = {}
    for k in _SCALAR_FALLBACKS:
        if out.get(k) is None:
            out[k] = ""
    for k in _INT_FALLBACKS:
        if out.get(k) is None:
            out[k] = 0
    return out


def _insert_all(
    client: Any,
    collection: str,
    rows: list[dict[str, Any]],
    fields: list[str] | None = None,
) -> int:
    done = 0
    for i in range(0, len(rows), _PAGE):
        batch = [_normalise(r, fields) for r in rows[i : i + _PAGE]]
        client.insert(collection, batch)
        done += len(batch)
        print(f"  已回填 {done}/{len(rows)} 行…")
    client.flush(collection)
    return done


def _invalidate_answer_cache(reason: str) -> None:
    """让所有租户的已缓存答案作废。

    重建换掉的是分词器 / schema，同一句话在新集合上召回的 chunk 与旧集合不同，
    旧答案不再是"当前这套检索"的产物。用全域代次而不是逐租户：本脚本是全库
    操作，而租户名单只有 Milvus 知道——为了失效去扫一遍库不值当。

    失败只打印不抛：重建已经成功了，为了一个可选的缓存动作把退出码变成非零，
    会让运维以为重建出了问题。
    """
    try:
        from core import cache_epoch

        cache_epoch.bump_all(reason=reason)
        print("  答案缓存已按新代次全量失效。")
    except Exception as exc:  # noqa: BLE001
        print(f"  [WARN] 答案缓存失效失败（旧答案可能残留至 TTL 到期）: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser(description="重建 Milvus 集合（导出 -> 重建 -> 回填）")
    ap.add_argument("--yes", action="store_true", help="真正执行；不加则只导出备份并预览")
    ap.add_argument("--restore-from", default="", help="跳过导出与重建，直接用该备份回填")
    ap.add_argument(
        "--collection",
        default="",
        help="目标集合名；缺省用 milvus.collection_name（配置里的生产集合）。"
        "F2.1 这类假设实验必须显式指定评测集合——生产集合全程只读。",
    )
    ap.add_argument(
        "--analyzer",
        default="",
        help="覆盖本次重建使用的 BM25 分词器（chinese / standard / JSON）。"
        "缺省用 milvus.bm25_analyzer。实验常用它对比不同分词器，而不想改配置。",
    )
    args = ap.parse_args()

    settings = get_settings()
    if args.analyzer:
        # 只改本次进程内的副本：实验参数不该污染配置文件。bm25_analyzer 是
        # schema 级属性，改完就固化在集合里，不存在"用完还原"的问题。
        settings.milvus.bm25_analyzer = args.analyzer
    if args.collection:
        # 必须改**配置对象**本身，不能只改下面那个局部变量：ensure_collection()
        # 与 _insert_all() 都从 settings.milvus.collection_name 取集合名。
        # 之前只改了局部变量，于是重建时 MilvusClient 照样去动生产集合
        # rag4c_chunks —— 评测集合被 drop 掉了，新集合却建在生产名字上。
        settings.milvus.collection_name = args.collection
    mc = RagMilvusClient(settings.milvus)
    client = mc._ensure_client()  # noqa: SLF001 - 运维脚本，需要原生客户端
    collection = args.collection or settings.milvus.collection_name

    print("=" * 68)
    print("Milvus 集合重建")
    print("=" * 68)
    print(f"  Milvus     : {settings.milvus.uri}")
    print(f"  集合       : {collection}")
    print(f"  BM25 分词器: {settings.milvus.bm25_analyzer}"
          f"  -> analyzer_params={mc._resolve_analyzer_params()}")  # noqa: SLF001
    print()

    # ---------- 分支：直接从备份回填 ----------
    if args.restore_from:
        backup = Path(args.restore_from)
        rows = json.loads(backup.read_text(encoding="utf-8"))
        print(f"从备份回填：{backup}（{len(rows)} 行）")
        if not args.yes:
            print("\n这是预览。确认后加 --yes 执行。")
            return 0
        # 恢复必须**先删后建**：ensure_collection() 在集合已存在时是幂等跳过，
        # 于是 analyzer 不会变（schema 属性改不了），旧行还在，再 insert 一次
        # 就变成 2N 行。此前"恢复失败"留下的正是这样一个集合：数据在、但分词器
        # 还是旧的，而行数已经翻倍。恢复的语义是"回到备份所描述的那个集合"，
        # 不是"往现有集合里再塞一份"。
        if client.has_collection(collection):
            print(f"删除已存在的集合 {collection}（恢复语义 = 重建，不是追加）…")
            client.drop_collection(collection)
        mc.ensure_collection()
        n = _insert_all(client, collection, rows, _export_fields(client, collection))
        actual = client.get_collection_stats(collection).get("row_count")
        print(f"\n回填完成：{n} 行（集合实际 {actual} 行）")
        if actual != n:
            print(
                f"警告：集合行数 {actual} 与回填行数 {n} 不一致，"
                "通常是上一次失败残留的行；重建一次即可对齐。"
            )
        _invalidate_answer_cache("从备份回填")
        return 0

    # ---------- 1. 导出 ----------
    if not client.has_collection(collection):
        print(f"集合 {collection} 不存在，无需重建；直接建新集合即可。")
        if args.yes:
            mc.ensure_collection()
            print("已按当前 schema 创建。")
        return 0

    print("== 1. 导出现有数据 ==")
    rows = _export(client, collection)
    docs = sorted({r.get("doc_id", "") for r in rows})
    datasets = sorted({r.get("dataset_id") or "" for r in rows})
    print(f"  共 {len(rows)} 行，{len(docs)} 个文档，知识库: {datasets}")
    for d in docs[:20]:
        cnt = sum(1 for r in rows if r.get("doc_id") == d)
        sample = next((r["text"][:50] for r in rows if r.get("doc_id") == d), "")
        print(f"    - {d}  ({cnt} chunk)  {sample!r}")
    if len(docs) > 20:
        print(f"    …… 另有 {len(docs) - 20} 个文档")

    backup_dir = _PROJECT_ROOT / "backups"
    backup_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_dir / f"{collection}-{stamp}.json"
    backup.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(f"  备份已写入: {backup}  ({backup.stat().st_size / 1024:.1f} KB)")

    if not args.yes:
        print()
        print("这是预览：备份已生成，集合未做任何改动。")
        print("确认上面的文档列表无误后，加 --yes 执行重建。")
        return 0

    # ---------- 2. 重建 ----------
    print()
    print("== 2. 重建集合 ==")
    client.drop_collection(collection)
    print(f"  已删除旧集合 {collection}")
    mc.ensure_collection()
    print("  已按当前 schema 重建（含新的 BM25 分词器）")

    # ---------- 3. 回填 ----------
    print()
    print("== 3. 回填数据 ==")
    if not rows:
        print("  原集合为空，无需回填。")
    else:
        n = _insert_all(client, collection, rows, _export_fields(client, collection))
        print(f"  回填完成：{n} 行")

    print()
    print("=" * 68)
    print(f"重建完成。备份保留在 {backup}")
    print("建议接着跑 scripts/e2e_live.py 验证检索。")
    print("=" * 68)
    _invalidate_answer_cache("集合重建")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
