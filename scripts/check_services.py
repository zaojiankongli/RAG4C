"""服务探活 —— 对着**真实**外部服务逐个验证 Milvus 以外的依赖。

与 ``check_milvus.py`` 是一对：那个管向量库，这个管其余三样
（嵌入 / 重排 / LLM）加上目录库。都需要真实服务，都不碰业务数据。

**关键设计：走项目自己的工厂，不裸发 HTTP。**
``curl`` 能通只说明端点活着，不说明本项目的客户端配置是对的——
base_url 少个 ``/v1``、模型名拼错、超时太短、响应字段对不上，
这些都只有走 ``create_embedder`` / ``create_client`` / ``create_reranker``
才能暴露。所以这里每一步都用生产代码路径发一次最小真实请求。

逐层递进 + 每层单独报错：外部服务的失败往往报得含糊
（一句 connection error 可能是没启动、端口不对、模型没拉），
所以拆成独立步骤，任一步失败都指明是哪一层、下一步该查什么。

用法::

    .venv\\Scripts\\python.exe scripts\\check_services.py

    # 只查某一项
    .venv\\Scripts\\python.exe scripts\\check_services.py --only embedding

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

_PASS = "[PASS]"
_FAIL = "[FAIL]"
_INFO = "[INFO]"
_SKIP = "[SKIP]"


def _mask(secret: str) -> str:
    """密钥脱敏：只留头尾，中间打码。

    探活脚本的输出经常被复制到聊天/工单里，明文密钥不该出现在那儿。
    保留首尾是为了还能肉眼对比"是不是我以为的那把 key"。
    """
    if not secret:
        return "(空)"
    if len(secret) <= 10:
        return secret[:2] + "***"
    return f"{secret[:6]}...{secret[-4:]}（长度 {len(secret)}）"


def _endpoint_hint(url: str) -> None:
    """连接类失败的通用排查提示。"""
    print("        排查顺序：")
    print(f"        - 服务是否已启动、{url} 是否可达")
    print("        - base_url 结尾是否需要 /v1（OpenAI 兼容端点通常需要）")
    print("        - 若是本机服务，确认监听的是 127.0.0.1 还是 0.0.0.0")


# 缺依赖和连不上服务是**完全不同**的两类失败，给的排查方向也完全相反。
# 把"openai 未安装"配上"检查服务是否启动、端口是否可达"，只会让人白查
# 一轮网络。所以这里先判别错误类别，再决定给什么提示。
_DEP_MARKERS = (
    "未安装",
    "not installed",
    "no module named",
    "modulenotfounderror",
    "importerror",
)


def _is_dependency_error(exc: BaseException) -> bool:
    """判断异常是否属于「Python 包没装」而不是「服务连不上」。"""
    if isinstance(exc, ImportError):  # ModuleNotFoundError 是其子类
        return True
    blob = f"{type(exc).__name__} {exc}".lower()
    return any(marker in blob for marker in _DEP_MARKERS)


# ---------------------------------------------------------------- #
# 各层检查：统一返回 (ok, detail)，由 main 打印
# ---------------------------------------------------------------- #
def check_embedding(s: Any) -> tuple[bool, str]:
    """嵌入服务：发一次真实 embed，并校验维度与 Milvus 配置一致。

    维度校验是重点。嵌入维度和 Milvus 集合的 ``dim`` 必须相等，
    否则写入时才会报错——那时候文档已经解析、切分、过了一遍 LLM，
    代价高得多。这里提前一秒钟发现。
    """
    from core.embedding import create_embedder

    cfg = s.embedding
    print(f"{_INFO}     provider={cfg.provider}  model={cfg.api_model}")
    print(f"{_INFO}     base_url={cfg.api_base_url}  key={_mask(cfg.api_key)}")

    embedder = create_embedder(cfg)
    vec = embedder.embed_query("Milvus 是一个向量数据库。")
    if not vec:
        return False, "embed_query 返回空向量"

    dim = len(vec)
    expected = s.milvus.dim
    print(f"{_INFO}     实际维度 {dim}，Milvus 集合配置 {expected}")
    if dim != expected:
        return False, (
            f"维度不匹配：嵌入模型产出 {dim} 维，但 RAG4C_MILVUS_DIM={expected}。"
            f"两者必须相等，否则写入 Milvus 时才会失败。"
            f"处理：改用 {expected} 维的模型，或把 milvus.dim 改成 {dim} 并重建集合。"
        )

    # 批量路径与单条路径是两套代码，分别验证
    vecs = embedder.embed_texts(["第一段文本", "第二段文本"])
    if len(vecs) != 2:
        return False, f"embed_texts 返回 {len(vecs)} 条，期望 2 条"
    if any(len(v) != dim for v in vecs):
        return False, "embed_texts 各条维度不一致"

    return True, f"{dim} 维，单条 + 批量路径均正常"


def check_llm(s: Any) -> tuple[bool, str]:
    """LLM 槽位：拿 generation 槽位发一次最小 chat。

    11 个槽位默认同一个 base_url/model，探通一个基本就够；
    若你把某个槽位指到了别的服务，那个得单独验。
    """
    from core.llm import create_client

    cfg = s.llm.generation
    print(f"{_INFO}     base_url={cfg.base_url}  model={cfg.model}")
    print(f"{_INFO}     key={_mask(cfg.api_key)}  timeout={cfg.timeout}s")

    client = create_client(cfg)
    reply = client.chat(
        [{"role": "user", "content": "只回复两个字：收到"}]
    )
    if not reply or not reply.strip():
        return False, "chat 返回空字符串"

    preview = reply.strip().replace("\n", " ")[:40]

    # 各槽位指向是否一致：不一致的话上面这次探活只覆盖了其中一个。
    # 遍历 LLM_SLOT_NAMES 而不是在这里抄一份槽位清单——原来抄的那份漏了
    # metadata_filter 和 classifier，于是把它们指到别处时这里不会提醒，
    # 「探活全部通过」却有两个槽位从没被验过。
    from config.settings import LLM_SLOT_NAMES

    slots: dict[tuple[str, str], list[str]] = {}
    for name in LLM_SLOT_NAMES:
        slot = getattr(s.llm, name, None)
        if slot is not None:
            slots.setdefault((slot.base_url, slot.model), []).append(name)
    if len(slots) > 1:
        print(f"{_INFO}     注意：各槽位指向不完全相同，本次只验证了 generation")
        for (url, model), names in slots.items():
            print(f"{_INFO}       {url} / {model} <- {', '.join(names)}")

    return True, f"回复: {preview}"


def check_reranker(s: Any) -> tuple[bool, str]:
    """重排服务：真发一次 rerank，并验证分数确实区分了相关与不相关。

    只看"没报错"不够——重排若返回全 0 或顺序不变，管线不会崩，
    只会悄悄失去重排收益。这里用一条明显相关、一条明显无关的候选，
    断言相关的那条分数更高。
    """
    from core.reranker import create_reranker
    from models.schemas import Chunk
    from indexing.hashing import text_hash
    from datetime import datetime, timezone

    cfg = s.reranker
    print(f"{_INFO}     provider={cfg.provider}  model={cfg.api_model}")
    print(f"{_INFO}     base_url={cfg.api_base_url}  key={_mask(cfg.api_key)}")

    now = datetime.now(timezone.utc)

    def _chunk(idx: int, text: str) -> Chunk:
        return Chunk(
            chunk_id=f"probe-{idx}", doc_id="probe", text=text,
            text_hash=text_hash(text), created_at=now, updated_at=now,
        )

    query = "Milvus 的混合检索怎么做？"
    candidates = [
        _chunk(0, "今天天气不错，适合出门散步，公园里的花都开了。"),
        _chunk(1, "Milvus 支持稠密向量与 BM25 稀疏向量的混合检索，用 RRF 融合两路排名。"),
    ]

    reranker = create_reranker(cfg)
    scores = reranker.rerank(query, candidates)
    if len(scores) != len(candidates):
        return False, f"rerank 返回 {len(scores)} 个分数，期望 {len(candidates)} 个"

    print(f"{_INFO}     无关候选={scores[0]:.4f}  相关候选={scores[1]:.4f}")
    if scores[1] <= scores[0]:
        return False, (
            f"重排未能区分相关性（相关={scores[1]:.4f} <= 无关={scores[0]:.4f}）。"
            "服务能连通但结果无判别力，检查模型名是否正确、"
            "或响应字段是否按 index 正确对齐。"
        )

    return True, f"判别正常（相关 {scores[1]:.4f} > 无关 {scores[0]:.4f}）"


def check_catalog(s: Any) -> tuple[bool, str]:
    """目录库：连一次并做一次幂等写入（租户 upsert）。

    只连不写不够——MySQL 常见的失败是连得上但没有建库/建表权限，
    或字符集不对导致中文写入报错。这里做一次真实的幂等写。
    """
    from core import catalog

    url = str(getattr(s.catalog, "db_url", "") or "")
    # URL 里带密码，脱敏后再打印
    shown = url
    if "://" in url and "@" in url:
        head, tail = url.split("://", 1)
        creds, host = tail.split("@", 1)
        user = creds.split(":", 1)[0]
        shown = f"{head}://{user}:***@{host}"
    print(f"{_INFO}     db_url={shown}")

    # get_engine() 内部就是「惰性建 Engine + 幂等建表」，没有单独的 init_db。
    # 调它本身就是一次真实连接 + DDL 探活。
    catalog.get_engine()
    catalog.ensure_tenant("probe-tenant", name="探活租户")
    # 注意参数顺序：ensure_dataset(tenant_id, dataset_id, ...)，租户在前
    catalog.ensure_dataset("probe-tenant", "probe-dataset", name="探活知识库")
    docs = catalog.list_documents("probe-dataset")
    return True, f"连接与幂等写入正常（探活知识库现有文档 {len(docs)} 篇）"


_CHECKS = [
    ("embedding", "嵌入服务", check_embedding),
    ("llm", "LLM 槽位", check_llm),
    ("reranker", "重排服务", check_reranker),
    ("catalog", "目录库", check_catalog),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG4C 外部服务探活")
    parser.add_argument(
        "--only", default="",
        help=f"只检查指定项（{'/'.join(k for k, _, _ in _CHECKS)}）",
    )
    args = parser.parse_args()

    s = get_settings()

    checks = _CHECKS
    if args.only:
        checks = [c for c in _CHECKS if c[0] == args.only]
        if not checks:
            print(f"{_FAIL} 未知的检查项: {args.only}")
            return 1

    print("=" * 68)
    print("RAG4C 外部服务探活（Milvus 单独用 check_milvus.py）")
    print("=" * 68)

    failures: list[str] = []
    for idx, (key, label, fn) in enumerate(checks, start=1):
        print()
        print(f"---- {idx}/{len(checks)} {label} ({key}) " + "-" * 24)
        try:
            ok, detail = fn(s)
        except Exception as exc:  # noqa: BLE001 - 分层报错是本脚本的核心价值
            print(f"{_FAIL} {label}失败：{type(exc).__name__}: {exc}")
            if _is_dependency_error(exc):
                # 缺包：给装包命令就够了，别扯网络排查
                print("        这是**依赖缺失**，与服务连通性无关。装上即可：")
                print("          uv pip install --python .venv\\Scripts\\python.exe \\")
                print('            ".[llm,milvus]" pymysql')
                print("        （嵌入 / LLM 都走 openai 兼容客户端，共用 [llm] 这一个 extra；")
                print("          pymysql 是 MySQL 目录库的驱动，未在 extras 里声明）")
            elif key == "embedding":
                _endpoint_hint(s.embedding.api_base_url)
            elif key == "llm":
                _endpoint_hint(s.llm.generation.base_url)
            elif key == "reranker":
                _endpoint_hint(s.reranker.api_base_url)
                print("        - 若是 401/403：API key 失效或额度用尽")
                print("        - 重排非必需：可在配置里关掉 rerank_on 先跑通全链路")
            elif key == "catalog":
                print("        排查顺序：")
                print("        - MySQL 是否启动、端口是否可达")
                print("        - 账号是否有建库建表权限")
                print("        - 库是否存在且字符集为 utf8mb4（中文写入会失败）")
            failures.append(label)
            continue

        if ok:
            print(f"{_PASS} {label}正常 —— {detail}")
        else:
            print(f"{_FAIL} {label}异常：{detail}")
            failures.append(label)

    print()
    print("=" * 68)
    if not failures:
        print("外部服务探活全部通过")
        print("=" * 68)
        print()
        print("下一步：")
        print("  1) Milvus 层  ->  scripts\\check_milvus.py")
        print("  2) 启动后端    ->  uv run uvicorn server.app:app --port 8000")
        return 0
    print(f"失败 {len(failures)} 项: {', '.join(failures)}")
    print("=" * 68)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中断")
        sys.exit(130)
    except Exception:
        print(f"{_FAIL} 未预期的异常：")
        traceback.print_exc()
        sys.exit(1)
