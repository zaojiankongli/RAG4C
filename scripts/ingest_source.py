#!/usr/bin/env python
"""按清单同步官方文档源到知识库。

用法::

    # 看清单里有哪些源、各自会抓什么
    python scripts/ingest_source.py --list

    # 空跑：只抓取 + 比对哈希，不写库（先确认路径没抓错）
    python scripts/ingest_source.py --source milvus --dry-run

    # 小批量真入库，确认切分与检索质量
    python scripts/ingest_source.py --source milvus --limit 20

    # 全量同步指定源 / 清单里所有 enabled 的源
    python scripts/ingest_source.py --source milvus
    python scripts/ingest_source.py --all

    # 上游内容没变也强制重嵌入（换了嵌入模型时用）
    python scripts/ingest_source.py --source milvus --force

**默认不开 Contextual / 图谱增强。** 这两项都是「每个 chunk 调一次 LLM」，
官方文档动辄上千篇、几万个 chunk，开着跑完的代价远超收益，而且一旦中途失败
重来一次又是一遍。要开就显式 ``--contextual``，并且请先用 ``--limit`` 估时。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config.settings import get_settings  # noqa: E402
from sources.state_modes import source_state_mode  # noqa: E402
from sources import (  # noqa: E402
    SourceSpec,
    SourceSyncer,
    SourceError,
    list_source_plugins,
    load_manifest,
)


def _resolve(path_str: str) -> Path:
    """相对路径按项目根解析，不按当前工作目录。

    否则 ``cd scripts && python ingest_source.py`` 会找不到清单——这类
    「换个目录跑就挂」的坑不值得留给使用者。
    """
    p = Path(path_str).expanduser()
    return p if p.is_absolute() else _PROJECT_ROOT / p


def _build_pipeline(settings, contextual: bool):
    """装配入库管线（精简版，面向批量文档同步）。

    与 ``server.documents._build_ingest_pipeline`` 的差别是刻意的：
    那条路径服务于「用户上传单个文件」，默认打开配置里的全部增强；
    这里服务于「一次上千篇纯文本」，只保留必需的清洗与切分。
    """
    from core.embedding import create_embedder
    from core.milvus_client import RagMilvusClient
    from indexing.ingest import IngestPipeline

    cleaner = None
    if getattr(settings.pipeline, "clean_on", True):
        from indexing.cleaner import create_cleaner

        cleaner = create_cleaner()

    contextualizer = None
    if contextual:
        from core.llm import create_client
        from indexing.contextual import Contextualizer

        contextualizer = Contextualizer(
            llm_client=create_client(settings.llm.contextual, slot="contextual"),
            template_path=_PROJECT_ROOT / "prompts" / "contextual_v1.txt",
            enabled=True,
            concurrency=int(getattr(settings.pipeline, "contextual_concurrency", 4)),
            document_char_limit=int(
                getattr(settings.pipeline, "contextual_document_chars", 4000)
            ),
        )

    return IngestPipeline(
        embedder=create_embedder(settings.embedding),
        milvus=RagMilvusClient(settings.milvus),
        cleaner=cleaner,
        contextualizer=contextualizer,
        contextual_enabled=contextualizer is not None,
        chunking_mode=settings.pipeline.chunking_mode,
        simple_doc_max_chars=settings.pipeline.simple_doc_max_chars,
    )



def _build_syncer(settings, pipeline, cache_dir: Path) -> SourceSyncer:
    state_mode = str(getattr(settings.sources, "state_mode", "json"))
    ledger = None
    # 这曾经是第四处 `in {"dual", "database"}` 手抄（第十轮评审 B1）：新登记一种
    # uses_ledger=True 的模式时，CLI 这条路会建出 ledger=None，runner 里的 ledger 分支
    # 静默短路，状态就写到错的存储上，而全部测试照旧绿。现在它问声明。
    if source_state_mode(state_mode).uses_ledger:
        from core import catalog
        from core.source_sync_ledger import SourceSyncLedger

        ledger = SourceSyncLedger(catalog.get_engine())
    return SourceSyncer(
        pipeline,
        cache_dir,
        settings,
        ledger=ledger,
        state_mode=state_mode,
    )

def _print_list(specs: list[SourceSpec]) -> None:
    print("已注册的源类型：")
    for plugin in list_source_plugins():
        need = ", ".join(plugin["required_params"]) or "无"
        print(f"  - {plugin['name']:<14} {plugin['describe']}")
        print(f"    {'':<14} 必填参数: {need}")
    print()
    print(f"清单中的源（共 {len(specs)}）：")
    for spec in specs:
        flag = "启用" if spec.enabled else "停用"
        print(f"  [{flag}] {spec.name:<14} type={spec.type:<12} dataset={spec.dataset_id}")
        for key in ("repo", "ref", "path"):
            if key in spec.params:
                print(f"        {key}: {spec.params[key]}")
        for key in ("include", "exclude"):
            if spec.params.get(key):
                print(f"        {key}: {spec.params[key]}")


def _reset_dataset(settings, spec, cache_dir: Path) -> tuple[str, ...]:
    """Atomically fence a source/dataset and enqueue all durable delete batches."""
    del settings, cache_dir
    import hashlib

    from core import catalog
    from core.document_deletion import DocumentDeletionRepository
    from core.knowledge_governance import AuditContext
    from core.source_sync_ledger import SourceSyncLedger

    if not spec.tenant_id:
        raise ValueError("--reset requires tenant_id for durable deletion")
    engine = catalog.get_engine()
    source = SourceSyncLedger(engine).ensure_source(spec)
    identity = (
        f"{source.id}:{source.mutation_generation}:{spec.tenant_id}:"
        f"{spec.dataset_id}"
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    idempotency_prefix = f"source-reset-{digest[:48]}"
    request_id = f"source-reset:{digest[:48]}"
    result = DocumentDeletionRepository(engine).request_dataset_reset(
        source_id=source.id,
        actor=AuditContext.system("system:source-reset", request_id),
        reason=f"durable reset requested for source {spec.name}",
        idempotency_prefix=idempotency_prefix,
    )
    if not result.batch_ids:
        print(
            f"   [reset] generation fenced source={result.source_generation} "
            f"dataset={result.dataset_generation}; no documents require deletion"
        )
        return ()
    print(
        f"   [reset] durable batches={len(result.batch_ids)} "
        f"documents={result.document_count} source_generation={result.source_generation} "
        f"dataset_generation={result.dataset_generation}"
    )
    for batch_id in result.batch_ids:
        print(f"   [reset] batch_id={batch_id}")
    print("   [reset] external cleanup continues asynchronously")
    return result.batch_ids


def main() -> int:
    ap = argparse.ArgumentParser(
        description="按清单同步文档源到知识库",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--manifest", default="", help="源清单路径（默认取 settings.sources.manifest_path）")
    ap.add_argument("--source", action="append", default=[], help="只同步指定源，可重复")
    ap.add_argument("--all", action="store_true", help="同步清单里所有 enabled 的源")
    ap.add_argument("--list", action="store_true", help="列出源类型与清单内容后退出")
    ap.add_argument("--dry-run", action="store_true", help="只抓取比对，不写库、不改状态")
    ap.add_argument("--force", action="store_true", help="忽略内容哈希，全部重新入库")
    ap.add_argument("--limit", type=int, default=0, help="每个源最多入库多少篇（0=不限）")
    ap.add_argument("--contextual", action="store_true", help="开启 Contextual 增强（很慢，先估时）")
    ap.add_argument(
        "--reset", action="store_true",
        help="为该源对应知识库创建异步 Durable Delete 批次（不等待外部清理）",
    )
    args = ap.parse_args()

    settings = get_settings()
    manifest_path = _resolve(args.manifest or settings.sources.manifest_path)

    try:
        specs = load_manifest(manifest_path)
    except SourceError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2

    if args.list:
        _print_list(specs)
        return 0

    if args.source:
        wanted = set(args.source)
        selected = [s for s in specs if s.name in wanted]
        unknown = wanted - {s.name for s in specs}
        if unknown:
            print(
                f"[错误] 清单里没有这些源: {sorted(unknown)}。"
                f"可选: {[s.name for s in specs]}",
                file=sys.stderr,
            )
            return 2
    elif args.all:
        selected = [s for s in specs if s.enabled]
    else:
        ap.print_usage(sys.stderr)
        print("\n[错误] 请指定 --source <name> 或 --all（或用 --list 先看看）", file=sys.stderr)
        return 2

    if not selected:
        print("[提示] 没有需要同步的源（清单里都是 enabled=false？）")
        return 0

    mode = "空跑" if args.dry_run else "写库"
    print(f"清单: {manifest_path}")
    print(f"模式: {mode}"
          f"{'（强制重嵌入）' if args.force else ''}"
          f"{f'  limit={args.limit}' if args.limit else ''}")
    print(f"集合: {settings.milvus.collection_name} @ {settings.milvus.uri}")
    print("-" * 68)

    # 管线里含 Milvus 连接与嵌入客户端，空跑时不需要，也就不建——
    # 这样 --dry-run 可以在没有 Milvus 的机器上验证清单写得对不对。
    pipeline = None if (args.dry_run or args.reset) else _build_pipeline(settings, args.contextual)
    cache_dir = _resolve(settings.sources.cache_dir)
    syncer = _build_syncer(settings, pipeline, cache_dir)

    reports = []
    failed_sources: list[str] = []
    incomplete: list[str] = []
    for spec in selected:
        print(f"\n>> {spec.name}  ({spec.type} -> dataset={spec.dataset_id})")
        if args.reset and not args.dry_run:
            _reset_dataset(settings, spec, cache_dir)
            continue
        try:
            report = syncer.sync(
                spec,
                force=args.force,
                dry_run=args.dry_run,
                limit=args.limit,
                on_progress=print,
            )
        except Exception as exc:  # noqa: BLE001 - 一个源崩了不该带走其余的源
            print(f"   [错误] {exc}", file=sys.stderr)
            failed_sources.append(spec.name)
            continue
        reports.append(report)
        if report.fetch_error:
            incomplete.append(spec.name)
        print(f"   {report.summary()}")
        for rel, err in report.failed[:5]:
            print(f"     ! {rel}: {err}")
        if len(report.failed) > 5:
            print(f"     … 另有 {len(report.failed) - 5} 篇失败")

    print("\n" + "=" * 68)
    total_docs = sum(r.ingested for r in reports)
    total_chunks = sum(r.chunks for r in reports)
    total_failed = sum(len(r.failed) for r in reports)
    print(f"合计: {len(reports)} 个源，入库 {total_docs} 篇 / {total_chunks} chunk，"
          f"单篇失败 {total_failed}")
    if failed_sources:
        print(f"整源失败: {failed_sources}")
    if incomplete:
        print(f"未走完（重跑可从断点续）: {incomplete}")
        for r in reports:
            if r.fetch_error:
                print(f"   {r.source}: {r.fetch_error}")
    # 单篇失败不算命令失败（下次运行会自动重试）；整源失败与「没走完」都算，
    # 否则 CI / 定时任务会把一次残缺同步当成成功，知识库缺内容却无人知晓。
    return 1 if (failed_sources or incomplete) else 0


if __name__ == "__main__":
    raise SystemExit(main())
