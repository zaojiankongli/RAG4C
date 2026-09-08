"""RAG4C 评测模块。

包含两个独立裁判（有据性 / 相关性）、内置评测数据集（含不可答脏数据）、
评测执行器与 CLI。

离线自检入口：``python scripts/smoke_eval.py``。
"""
from __future__ import annotations

__version__ = "0.1.0"

# 惰性导出：避免包导入时立即加载 run_eval（消除 `python -m eval.run_eval`
# 时的重复执行告警），且保证 import 本身完全离线。
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "SAMPLE_DATASET": ("eval.dataset_sample", "SAMPLE_DATASET"),
    "GroundednessJudge": ("eval.judges", "GroundednessJudge"),
    "RelevanceJudge": ("eval.judges", "RelevanceJudge"),
    "create_judges": ("eval.judges", "create_judges"),
    "split_claims": ("eval.judges", "split_claims"),
    "Evaluator": ("eval.run_eval", "Evaluator"),
    "EvalReport": ("eval.run_eval", "EvalReport"),
    "CaseResult": ("eval.run_eval", "CaseResult"),
    "extract_evidence": ("eval.run_eval", "extract_evidence"),
    "load_dataset": ("eval.run_eval", "load_dataset"),
}


def __getattr__(name: str):
    import importlib

    entry = _LAZY_EXPORTS.get(name)
    if entry is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr = entry
    return getattr(importlib.import_module(module_name), attr)


def __dir__():
    return sorted(list(globals()) | set(_LAZY_EXPORTS))


__all__ = list(_LAZY_EXPORTS)
