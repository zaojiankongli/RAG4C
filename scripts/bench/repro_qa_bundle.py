"""Reproduce the swallowed QA-bundle error against the live PostgreSQL catalog."""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default="config/.env.live-bench")
    parser.add_argument("--tenant", default=None)
    parser.add_argument("--dataset", default=None)
    args = parser.parse_args()
    os.environ["RAG4C_ENV_FILE"] = str(REPO_ROOT / args.env_file)

    from sqlalchemy import inspect, text

    from core import catalog
    from core.knowledge_content import KnowledgeContentRepository

    engine = catalog.get_engine()
    print(f"engine url = {engine.url.render_as_string(hide_password=True)}")

    tables = set(inspect(engine).get_table_names())
    wanted = ("qa_knowledge", "qa_alternative_questions", "qa_negative_questions", "datasets")
    print("missing tables:", [t for t in wanted if t not in tables] or "none")

    with engine.begin() as conn:
        rows = conn.execute(text("select id, tenant_id, name from datasets order by id")).all()
    for row in rows:
        print("dataset:", tuple(row))

    pairs = [(str(r[1]), str(r[0])) for r in rows]
    if args.tenant and args.dataset:
        pairs.insert(0, (args.tenant, args.dataset))
    else:
        pairs.insert(0, ("default", "default"))

    repo = KnowledgeContentRepository(engine)
    failures = 0
    for tenant_id, dataset_id in pairs:
        try:
            bundle = repo.list_qa_retrieval_bundle(tenant_id, dataset_id)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"\nFAIL tenant={tenant_id!r} dataset={dataset_id!r}: {type(exc).__name__}: {exc}")
            traceback.print_exc()
        else:
            print(f"OK   tenant={tenant_id!r} dataset={dataset_id!r}: {len(bundle)} rows")
    print(f"\n{failures} of {len(pairs)} combos raised")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
