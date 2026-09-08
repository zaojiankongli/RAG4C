"""验证与弃权模块。

- :mod:`verify.claims`     ：答案声明切分（确定性纯函数）
- :mod:`verify.verifier`   ：三层引用验证（L1 存在性 / L2 文本哈希 /
  L3 蕴含判定）+ 事后引用指派
- :mod:`verify.abstention` ：双重阈值弃权门 + 阈值分位数校准

全部实现可离线运行：不依赖网络、模型与 Milvus（milvus 可传 None）。
"""
from __future__ import annotations

from verify.abstention import AbstentionGate, calibrate_retrieval_threshold
from verify.claims import split_claims
from verify.verifier import (
    CitationVerifier,
    NliNotImplemented,
    VerificationResult,
    create_verifier,
)

__all__ = [
    "split_claims",
    "CitationVerifier",
    "VerificationResult",
    "NliNotImplemented",
    "create_verifier",
    "AbstentionGate",
    "calibrate_retrieval_threshold",
]
