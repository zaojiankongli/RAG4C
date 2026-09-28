"""Reverse-mutation check for the QA-bundle scope, error-classification and acl guards.

Each mutation breaks one side of a two-sided guard; the paired test must go red.
Restores the sources in ``finally`` so an interrupted run cannot leave them mutated.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOADER = ROOT / "retrieval" / "qa_retrieval.py"
REPO = ROOT / "core" / "knowledge_content.py"
RAG = ROOT / "rag.py"
BUNDLE_TESTS = "tests/test_qa_retrieval_bundle.py"
MATCHER_TESTS = "tests/test_qa_matcher.py"
WIRING_TESTS = "tests/test_qa_retrieval_wiring.py"

SKIP = "test_answer_sequential_injects_global_qa_when_acl_absent"

MUTATIONS = [
    (
        "missing-dataset treated as a catalog failure again",
        LOADER,
        "    except ContentNotFound:  # noqa: BLE001 - 数据集未建 = 本就没有 FAQ，调用方会记 no_bundle\n"
        "        return []\n",
        "",
        BUNDLE_TESTS,
        "test_missing_dataset_is_not_reported_as_catalog_failure",
    ),
    (
        "read failure swallowed into the expected-miss branch",
        LOADER,
        "    except ContentNotFound:",
        "    except Exception:",
        BUNDLE_TESTS,
        "test_catalog_read_failure_still_reports_catalog_error",
    ),
    (
        "empty dataset_id invents a dataset named default again",
        LOADER,
        'return repo.list_qa_retrieval_bundle(tenant_id, (dataset_id or "").strip())',
        'return repo.list_qa_retrieval_bundle(tenant_id, dataset_id or "default")',
        BUNDLE_TESTS,
        "test_loader_passes_global_scope_through",
    ),
    (
        "scope always treated as named",
        REPO,
        "        scoped = bool(dataset_id)",
        "        scoped = True",
        BUNDLE_TESTS,
        "test_empty_dataset_scope_spans_the_tenant_not_a_dataset_named_default",
    ),
    (
        "scope always treated as global (named scope stops filtering)",
        REPO,
        "        scoped = bool(dataset_id)",
        "        scoped = False",
        BUNDLE_TESTS,
        "test_named_scope_still_limits_to_one_dataset",
    ),
    (
        "tenant filter widened so another tenant's FAQ leaks in",
        REPO,
        "            qa_filters = [\n"
        "                QAKnowledge.tenant_id == tenant_id,\n"
        "                QAKnowledge.review_status == \"approved\",",
        "            qa_filters = [\n"
        '                QAKnowledge.tenant_id.in_([tenant_id, "tenant-2"]),\n'
        "                QAKnowledge.review_status == \"approved\",",
        BUNDLE_TESTS,
        "test_empty_dataset_scope_never_reaches_another_tenant",
    ),
    (
        "acl guard disabled",
        LOADER,
        '        if acl and not (dataset_id or "").strip():',
        "        if False:",
        MATCHER_TESTS,
        "test_global_scope_with_acl_does_not_inject_faq",
    ),
    (
        "acl guard ignores whether the scope is global",
        LOADER,
        '        if acl and not (dataset_id or "").strip():',
        "        if acl:",
        MATCHER_TESTS,
        "test_named_scope_with_acl_still_loads_that_dataset_bundle",
    ),
    (
        "entry point stops forwarding acl",
        RAG,
        "                acl=acl,\n",
        "",
        WIRING_TESTS,
        "test_answer_sequential_blocks_acl_filtered_global_qa",
    ),
]


def main() -> int:
    originals = {path: path.read_bytes() for path in {item[1] for item in MUTATIONS}}
    failures: list[str] = []
    try:
        for label, target, old, new, test_file, expected_test in MUTATIONS:
            original = originals[target]
            eol = "\r\n" if b"\r\n" in original else "\n"
            text = original.decode("utf-8")
            probe = old.replace("\n", eol)
            want = new.replace("\n", eol)
            if text.count(probe) != 1:
                failures.append(
                    f"{label}: anchor matched {text.count(probe)} times in {target.name}"
                )
                continue
            target.write_bytes(text.replace(probe, want, 1).encode("utf-8"))
            try:
                proc = subprocess.run(
                    [
                        sys.executable, "-m", "pytest", test_file,
                        "-q", "-p", "no:cacheprovider", "--no-header", "-x",
                        "-k", expected_test,
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
            finally:
                target.write_bytes(original)
            if proc.returncode == 0:
                failures.append(f"{label}: {expected_test} still PASSED (guard is decorative)")
            else:
                lines = [ln for ln in proc.stdout.splitlines() if "passed" in ln or "failed" in ln]
                detail = lines[-1] if lines else "red"
                if "error" in detail.lower() and "failed" not in detail.lower():
                    failures.append(f"{label}: suite ERRORED instead of failing -- inconclusive")
                    continue
                print(f"caught  {label} -> {detail}")
    finally:
        for path, blob in originals.items():
            path.write_bytes(blob)
        restored = ", ".join(
            f"{p.name}={p.read_bytes() == originals[p]}" for p in sorted(originals, key=lambda x: x.name)
        )
        print(f"\nrestored: {restored}")
    for line in failures:
        print("MISSED:", line)
    print(f"\n{len(MUTATIONS) - len(failures)}/{len(MUTATIONS)} mutations caught "
          f"(paired control test kept out of scope: {SKIP})")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
