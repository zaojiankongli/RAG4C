"""向量小工具：只放不依赖任何业务模块的纯数值函数。

为什么单独开一个模块：余弦相似度此前只存在于 :mod:`verify.verifier` 里，
而 :mod:`core.milvus_client` 也要算同一个东西。让 core 反过来 import verify
是层级倒置；把实现抄一份过去则更糟——数值原语一旦有两份，迟早会在一份里
修了零向量、另一份没修，而这种偏差不会报错，只会让某条路径的相似度悄悄
偏一点点。所以抽到两边都能依赖的底层。
"""
from __future__ import annotations

import math
from typing import Optional, Sequence


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """余弦相似度（numpy 可用时用 numpy，否则纯 Python 实现）。

    零向量返回 0.0 而不是抛除零错：调用方拿到的是"完全不相似"，
    与"算不出来"在下游的处理方式一致。
    """
    try:
        import numpy as np

        va = np.asarray(a, dtype=float)
        vb = np.asarray(b, dtype=float)
        denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
        if denom == 0.0:
            return 0.0
        return float(float(np.dot(va, vb)) / denom)
    except ImportError:
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        denom = norm_a * norm_b
        if denom == 0.0:
            return 0.0
        return float(sum(x * y for x, y in zip(a, b)) / denom)


def safe_cosine(a: Sequence[float], b: object) -> Optional[float]:
    """算余弦，但对"另一边根本不是向量"这件事返回 None 而不是 0.0。

    用于从库里回读的向量：字段没取到、被存成 null、维度对不上，都属于
    **这个信号不可用**，而不是"相似度是 0"。两者必须区分——0.0 会被弃权闸
    当成"确实不相关"从而弃权，None 才是"这把尺子这次没有"。
    """
    if not isinstance(b, (list, tuple)):
        return None
    if not b or len(b) != len(a):
        return None
    return cosine_similarity(a, b)


__all__ = ["cosine_similarity", "safe_cosine"]
