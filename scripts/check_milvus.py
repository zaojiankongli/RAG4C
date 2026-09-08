"""Milvus 联调检查 —— 对着**真实** Milvus 服务逐层验证向量库这一层。

与 scripts/ 下其它 smoke 脚本的区别：那些是纯离线、用确定性桩；本脚本
**需要一个真实可达的 Milvus**，用来验证只有真实服务才能暴露的问题。

设计原则：逐层递进 + 每层单独报错。真实基础设施的失败往往报错含糊
（一句 "connection error" 可能是网络不通、端口不对、版本不兼容或
schema 冲突），所以这里把连接 / 建集合 / 写入 / 检索拆成独立步骤，
任一步失败都明确指出是哪一层、下一步该查什么。

**不碰你的真实数据**：全程使用独立的临时集合（默认 rag4c_conncheck），
结束时删除。不读写业务集合。

用法::

    # 指向你的 Milvus（也可写进 .env）
    $env:RAG4C_MILVUS_URI = "http://192.168.100.128:19530"
    .venv\\Scripts\\python.exe scripts\\check_milvus.py

    # 保留测试集合以便手动检查
    .venv\\Scripts\\python.exe scripts\\check_milvus.py --keep

退出码：全部通过 0，任一层失败 1。
"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import get_settings  # noqa: E402
from core.milvus_client import RagMilvusClient, RagMilvusError  # noqa: E402
from indexing.hashing import text_hash  # noqa: E402
from models.schemas import Chunk  # noqa: E402

# 独立的检查用集合，避免污染业务数据
CHECK_COLLECTION = "rag4c_conncheck"
# 迁移检查专用：故意建成"缺 dataset_id 的旧版集合"，用来验证自动迁移
MIGRATE_COLLECTION = "rag4c_conncheck_mig"

_PASS = "[PASS]"
_FAIL = "[FAIL]"
_INFO = "[INFO]"


def _fake_vector(dim: int, seed: int) -> list[float]:
    """确定性伪向量（不依赖嵌入服务，把检查范围限制在 Milvus 这一层）。"""
    return [((seed * 7 + i * 13) % 100) / 100.0 for i in range(dim)]


def _make_chunk(idx: int, text: str, tenant: str = "conncheck") -> Chunk:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return Chunk(
        chunk_id=f"conncheck-{idx:03d}",
        doc_id="conncheck-doc",
        text=text,
        text_hash=text_hash(text),
        created_at=now,
        updated_at=now,
        source="conncheck",
        tenant_id=tenant,
        metadata={"acl": "public"},
    )


SAMPLE_TEXTS = [
    "Milvus 是一个开源的向量数据库，支持稠密向量与稀疏向量的混合检索。",
    "HNSW 是一种基于图的近似最近邻索引，检索时用 ef 参数控制候选宽度。",
    "IVF_FLAT 索引把向量空间划分成若干分桶，检索时用 nprobe 控制探测的分桶数。",
    "BM25 是经典的关键词检索算法，Milvus 通过内置 Function 在写入时自动生成稀疏向量。",
    "RRF（Reciprocal Rank Fusion）用于把稠密检索与稀疏检索的排名融合成最终结果。",
]


def _check_schema_migration(cfg: Any, raw: Any) -> tuple[bool, str]:
    """验证存量集合的字段自动迁移真的能跑通。

    为什么单独建一张集合：``ensure_collection`` 只有在集合**已存在**时才
    走 ``_migrate_schema``，而前面几步每次都是从零建集合，永远命中的是
    "创建"分支。迁移分支因此长期无人问津——它里面曾经调用一个
    ``MilvusClient`` 上根本不存在的方法（``add_field``），异常又被
    ``except Exception`` 兜住转写成"自动迁移失败，请删除集合重建"，
    于是从来没人发现它一次都没成功过。这一步就是为了堵住这个盲区。

    做法：手工建一张**故意缺 dataset_id** 的最小集合（模拟旧版本留下的
    数据），再让 ``ensure_collection`` 去迁移它，最后回查 schema 确认
    字段真的补上了。
    """
    from pymilvus import DataType

    mig_cfg = cfg.model_copy(update={"collection_name": MIGRATE_COLLECTION})
    mig_client = RagMilvusClient(mig_cfg)
    try:
        if raw.has_collection(MIGRATE_COLLECTION):
            raw.drop_collection(MIGRATE_COLLECTION)

        schema = raw.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field(
            field_name="chunk_id", datatype=DataType.VARCHAR,
            is_primary=True, max_length=256,
        )
        schema.add_field(
            field_name="dense_vector", datatype=DataType.FLOAT_VECTOR, dim=cfg.dim
        )
        schema.add_field(
            field_name="text", datatype=DataType.VARCHAR, max_length=65535
        )
        index_params = raw.prepare_index_params()
        index_params.add_index(
            field_name="dense_vector",
            index_type="FLAT",
            metric_type=cfg.metric_type,
        )
        raw.create_collection(
            collection_name=MIGRATE_COLLECTION,
            schema=schema,
            index_params=index_params,
        )

        # 走"集合已存在"分支 -> _migrate_schema -> add_collection_field
        mig_client.ensure_collection()

        desc = raw.describe_collection(MIGRATE_COLLECTION)
        names = {f.get("name") for f in (desc.get("fields") or [])}
        if "dataset_id" not in names:
            return False, f"迁移后仍无 dataset_id 字段，现有字段：{sorted(n for n in names if n)}"
        return True, ""
    except Exception as exc:  # noqa: BLE001 - 由调用方统一打印分诊建议
        return False, str(exc)
    finally:
        try:
            mig_client.close()
        except Exception:
            pass
        try:
            raw.drop_collection(MIGRATE_COLLECTION)
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Milvus 联调检查")
    parser.add_argument("--keep", action="store_true", help="结束后保留检查集合")
    args = parser.parse_args()

    settings = get_settings()
    cfg = settings.milvus

    print("=" * 68)
    print("Milvus 联调检查")
    print("=" * 68)
    print(f"{_INFO} URI        : {cfg.uri}")
    print(f"{_INFO} 索引类型   : {cfg.index_type}   相似度: {cfg.metric_type}")
    print(f"{_INFO} 向量维度   : {cfg.dim}")
    print(f"{_INFO} 检索参数   : ef={cfg.ef}  nprobe={cfg.nprobe}  候选倍数={cfg.candidate_factor}")
    print(f"{_INFO} 检查集合   : {CHECK_COLLECTION}（独立集合，不影响业务数据）")
    print()

    if "://" not in cfg.uri:
        print(f"{_FAIL} 当前 URI 是本地文件模式（Milvus Lite），不是要联调的服务端。")
        print("        请设置 RAG4C_MILVUS_URI=http://192.168.100.128:19530 后重试。")
        return 1

    # 用独立集合名构造客户端，其余配置沿用真实设置
    check_cfg = cfg.model_copy(update={"collection_name": CHECK_COLLECTION})
    client = RagMilvusClient(check_cfg)
    # 只有真的建过集合才需要清理。否则前面几步失败时，finally 会去连一个
    # 连不上的服务，再打印一句"清理失败，可手动删除"——一个根本不存在的
    # 集合，让人白查一轮。
    collection_created = False

    try:
        # ---- 1. 依赖与连通性 ----------------------------------------- #
        try:
            import pymilvus  # noqa: F401
        except ImportError:
            print(f"{_FAIL} 1/7 pymilvus 未安装")
            print("        客户端版本必须与 Milvus 服务端版本线对齐：")
            print('          Milvus 3.0 服务端 -> pip install "pymilvus>=3.0,<4"')
            print("          Milvus 2.x 服务端 -> 装对应的 2.x 客户端")
            print("        版本不匹配会报 this version of sdk is incompatible with server。")
            print("        （[milvus-lite] 附加依赖只有本地文件模式才需要，且不支持 Windows）")
            return 1
        print(f"{_PASS} 1/7 pymilvus 已安装（{getattr(pymilvus, '__version__', '版本未知')}）")

        try:
            raw = client._ensure_client()
            # 触发一次真实网络往返：list_collections 是最轻量的只读调用
            existing = raw.list_collections()
        except Exception as exc:
            print(f"{_FAIL} 2/7 连接失败：{exc}")
            print("        排查顺序：")
            print("        - 虚拟机 IP 是否正确、宿主机能否 ping 通")
            print("        - Milvus 是否监听 19530，且未被虚拟机防火墙拦截")
            print("        - 若 Milvus 跑在容器里，端口是否映射到了虚拟机网卡而非 127.0.0.1")
            return 1
        print(f"{_PASS} 2/7 连接成功，服务端现有集合 {len(existing)} 个")
        if existing:
            print(f"{_INFO}     {', '.join(map(str, existing[:8]))}")
        # 打印服务端版本：客户端与服务端必须版本线对齐，把两边都摆出来
        # 才好判断「不兼容」类报错到底是谁的问题。
        # 用 client.get_server_version()，不要用 utility.get_server_version——
        # 后者依赖 connections.connect() 注册的连接别名，而 MilvusClient 走的
        # 是另一套连接管理，不注册别名，调用会直接抛
        # ConnectionNotExistException。
        try:
            server_ver = raw.get_server_version()
            print(f"{_INFO}     服务端 {server_ver} / 客户端 {getattr(pymilvus, '__version__', '?')}")
        except Exception as exc:
            print(f"{_INFO}     服务端版本获取失败（不影响后续检查）：{exc}")

        # ---- 2. 建集合（含 BM25 Function + 索引）---------------------- #
        if CHECK_COLLECTION in existing:
            print(f"{_INFO}     检查集合已存在，先删除以保证本次从零验证")
            raw.drop_collection(CHECK_COLLECTION)
        try:
            client.ensure_collection()
        except RagMilvusError as exc:
            print(f"{_FAIL} 3/7 建集合失败：{exc}")
            print("        这一步会创建 schema + BM25 Function + 向量索引。")
            print("        若报 Function 相关错误，多半是 Milvus 版本过低")
            print("        （内置 BM25 Function 需要 Milvus 2.5+）。")
            return 1
        print(f"{_PASS} 3/7 集合创建成功（schema + BM25 Function + {cfg.index_type} 索引）")
        collection_created = True

        # ---- 3. 写入（验证分批逻辑与 schema 匹配）--------------------- #
        chunks = [_make_chunk(i, t) for i, t in enumerate(SAMPLE_TEXTS)]
        vectors = [_fake_vector(cfg.dim, i) for i in range(len(chunks))]
        try:
            ids = client.insert_chunks(chunks, vectors)
        except RagMilvusError as exc:
            print(f"{_FAIL} 4/7 写入失败：{exc}")
            print("        若报字段缺失，说明服务端已有同名集合但 schema 是旧版；")
            print("        本脚本已先删除同名集合，正常不该出现。")
            return 1
        print(f"{_PASS} 4/7 写入 {len(ids)} 行成功（稀疏向量由 BM25 Function 自动生成）")

        # 让写入对检索可见
        try:
            raw.flush(CHECK_COLLECTION)
        except Exception:
            pass  # 部分版本无需显式 flush
        raw.load_collection(CHECK_COLLECTION)

        # ---- 4. 混合检索（本次联调的重点：RERANK Function + 检索参数）--- #
        # 这是离线测试**无法**验证的部分。离线只能断言"代码构造出了某个
        # 形状"，而"服务端是否接受这个形状"只有真实 Milvus 能回答。
        # 本步同时压到三件事：
        #   1) Function(function_type=RERANK, params={"reranker":"rrf"})
        #      这个 3.0 写法被服务端接受（旧写法是 RRFRanker 类）；
        #   2) ef / nprobe 检索参数被接受且值域合法；
        #   3) BM25 分支能用查询文本直接检索 Function 生成的稀疏向量。
        query_vec = _fake_vector(cfg.dim, 1)
        try:
            hits = client.hybrid_search(
                query_dense=query_vec,
                top_k=3,
                query_text="HNSW 索引的 ef 参数",
            )
        except RagMilvusError as exc:
            print(f"{_FAIL} 5/7 混合检索失败：{exc}")
            print("        按报错关键词分诊：")
            print("        - 提到 rrf / reranker / function：服务端不接受 3.0 的")
            print("          RERANK Function 写法，多半是服务端并非 3.0；")
            print("          改回 RRFRanker 需要改 milvus_client.py::_build_ranker。")
            print("        - 提到 ef / nprobe / search param：检索参数值域不合法，")
            print("          检查 .env 里的 RAG4C_MILVUS_EF / NPROBE。")
            print("        - 提到 sparse / BM25：稀疏索引或 Function 配置问题。")
            return 1
        print(f"{_PASS} 5/7 混合检索成功，返回 {len(hits)} 条")
        for h in hits:
            preview = h.chunk.text[:34].replace("\n", " ")
            print(f"{_INFO}     score={h.score:.4f}  {preview}…")
        if not hits:
            print(f"{_FAIL}     检索返回空——写入可能尚未对检索可见，或索引未加载")
            return 1

        # ---- 5. 纯稠密降级路径（hybrid_search_on=False 时走这条）------ #
        try:
            dense_only = client.hybrid_search(
                query_dense=query_vec, top_k=3, query_text=None
            )
        except RagMilvusError as exc:
            print(f"{_FAIL} 6/7 纯稠密检索失败：{exc}")
            return 1
        print(f"{_PASS} 6/7 纯稠密检索成功，返回 {len(dense_only)} 条（关闭混合检索时的降级路径）")

        # ---- 6. 存量集合字段迁移 -------------------------------------- #
        ok, detail = _check_schema_migration(cfg, raw)
        if not ok:
            print(f"{_FAIL} 7/7 存量集合字段迁移失败：{detail}")
            print("        这一步模拟「旧版本留下的、缺 dataset_id 的集合」，")
            print("        验证 ensure_collection 能自动补上字段。")
            print("        若报方法不存在，说明客户端版本不支持在已有集合上加字段；")
            print("        若报字段必须 nullable，检查 _migrate_schema 的 nullable 参数。")
            print("        兜底方案：删除旧集合后重新入库（本项目数据可重建）。")
            return 1
        print(f"{_PASS} 7/7 存量集合字段迁移成功（缺 dataset_id 的旧集合被自动补齐）")

        print()
        print("=" * 68)
        print("MILVUS 联调检查通过 —— 向量库这一层可用")
        print("=" * 68)
        print()
        print("已验证：连通性 / 集合与 BM25 Function 创建 / 分批写入 /")
        print("       混合检索（含 3.0 的 RERANK Function 融合排序器被服务端接受、")
        print("       ef 检索参数被接受）/ 纯稠密降级路径 / 存量集合字段迁移")
        print()
        print("下一步可以启动后端做全链路联调：")
        print("  uv run uvicorn server.app:app --host 127.0.0.1 --port 8000")
        return 0

    except Exception:
        print(f"{_FAIL} 未预期的异常：")
        traceback.print_exc()
        return 1
    finally:
        if collection_created and not args.keep:
            try:
                client._ensure_client().drop_collection(CHECK_COLLECTION)
                print(f"{_INFO} 已清理检查集合 {CHECK_COLLECTION}")
            except Exception:
                print(f"{_INFO} 检查集合 {CHECK_COLLECTION} 清理失败，可手动删除")
        client.close()


if __name__ == "__main__":
    sys.exit(main())
