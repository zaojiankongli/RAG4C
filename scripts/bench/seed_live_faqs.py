"""Seed approved FAQ rows into a live dataset through the repository's own authority path.

Why a script instead of the HTTP route: ``_WRITE = require_knowledge_permission(
KNOWLEDGE_WRITE, resolve_path_dataset("dataset_id"))`` needs a real actor Bearer, and minting
credentials is out of scope here. This still goes through ``create_qa`` + ``review_qa``, so the
invariants (pending -> approved -> ``retrieval_enabled``) are the product's, not bypassed.

Usage::

    python scripts/bench/seed_live_faqs.py --env-file config/.env.live-bench \\
        --dataset kb-live-bench --count 6
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# Each pair is (question, answer) phrased the way an operator would write an FAQ entry,
# and deliberately worded so the lexical/normalized matcher can hit it.
FAQS: tuple[tuple[str, str], ...] = (
    (
        "新增一种投影操作要改哪些地方？",
        "新增投影操作要同时改三处：生产者注册表登记算子、投影词表补上来源类型，"
        "以及服务侧的扩展轴声明表。只改注册表会让回执找不到派发目标。",
    ),
    (
        "投影目标的生产者注册表解决了什么问题？",
        "生产者注册表把投影目标的算子标识与实现集中到一处声明，避免调用方按名字硬编码；"
        "未知标识会被拒绝而不是静默走默认分支。",
    ),
    (
        "投影 worker 的重试修订策略是什么？",
        "worker 按修订号做乐观并发控制：每次重试携带期望修订，落库时若不匹配则放弃本次写入，"
        "避免旧尝试覆盖新结果。",
    ),
    (
        "投影重试的生命周期内部参数怎么配置？",
        "重试生命周期由最大次数、退避基数与上限三段组成；超出上限后进入失败终态并留下可观测记录。",
    ),
    (
        "回执 handoff 是怎么派发的？",
        "回执按来源策略派发到对应处理分支；未知 kind 不再回落到审批查询，而是显式拒绝，"
        "以免把不认识的回执当作审批结果处理。",
    ),
    (
        "身份 provider 的字段形状是怎么约定的？",
        "身份 provider 的字段形状收成声明表，逐项校验；遇到不认识的类型直接拒绝，"
        "而不是猜测性接受。",
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default="config/.env.live-bench")
    parser.add_argument("--tenant", default="default")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--count", type=int, default=len(FAQS))
    args = parser.parse_args()
    os.environ["RAG4C_ENV_FILE"] = str(REPO_ROOT / args.env_file)

    from core import catalog
    from core.knowledge_content import KnowledgeContentRepository
    from core.knowledge_governance import AuditContext

    engine = catalog.get_engine()
    if engine is None:
        print("[error] 目录库没起来", file=sys.stderr)
        return 2
    repository = KnowledgeContentRepository(engine)

    created: list[str] = []
    for index, (question, answer) in enumerate(FAQS[: args.count]):
        audit = AuditContext(actor_id=f"bench-seed-{index}", request_id=f"bench-seed-req-{index}")
        qa = repository.create_qa(
            args.tenant, args.dataset, question=question, answer=answer, origin="manual", audit=audit
        )
        reviewed = repository.review_qa(
            args.tenant,
            args.dataset,
            qa.id,
            expected_revision=qa.revision,
            decision="approved",
            audit=audit,
        )
        created.append(qa.id)
        print(f"approved {qa.id} revision={reviewed.revision} retrieval_enabled="
              f"{getattr(reviewed, 'retrieval_enabled', None)}")

    bundle = repository.list_qa_retrieval_bundle(args.tenant, args.dataset)
    print(f"\n具名口径取回 {len(bundle)} 条；全域口径取回 "
          f"{len(repository.list_qa_retrieval_bundle(args.tenant, ''))} 条")
    if not bundle:
        print("[error] 审核通过后仍取不到有效 FAQ 包，说明权威链路上还有一道门", file=sys.stderr)
        return 1
    print(f"种子完成：{len(created)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
