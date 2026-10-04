"""弃权门独立判定 answer_status 的守卫测试。

F1.2 的 145 条基线：14 条幻觉样本里 11 条（79%）``relevance`` 恰好 0.00 而
``groundedness`` 在 0.71~1.00——**不是编造，是答非所问**。

## 为什么 answer_status 必须独立成一路

弃权门对逐条蕴含分数取 ``max``（``verify/abstention.py`` 的第 4 步）。而这批
样本的形态是：

    5 条声明：4 条 supported(1.0) + 整段答非所问

把 topic_only 压成"某一条的 0.3"，``max`` 仍是 1.0 -> 门照样放行。实测确认：

    >>> gate.decide([0.9], [1.0, 1.0, 1.0, 1.0, 0.3])
    (False, '')

主题相关性是**整段**的属性，不该参与逐条聚合。守卫的重点就是这一条。

## 几条不能搞错的边界

1. ``topic_only`` / ``irrelevant`` 必拦；
2. ``answered`` 与 ``""``（v1 模板 / 模型没给）**都不拦**——后者尤其重要，
   把"没给"当成"不合格"会让所有 v1 路径全被拦；
3. 拦截要发生在**蕴含分数检查之前**：F1.2 那批样本的 max 是 1.0，若先查蕴含
   分数就放行了，根本走不到 answer_status；
4. 弃权原因要能区分 topic_only 与 irrelevant——前者是"没用"，后者是"跑题"，
   事后分析要能分开。
"""
from __future__ import annotations


from verify.abstention import AbstentionGate


def _gate() -> AbstentionGate:
    return AbstentionGate(
        retrieval_threshold=0.30,
        entailment_threshold=0.60,
        dense_cosine_threshold=0.52,
    )


# ---------------------------------------------------------------------------
# 1：拦得住
# ---------------------------------------------------------------------------


def test_topic_only_abstains_despite_all_supported_claims() -> None:
    """核心场景：4 条 supported + 整段答非所问。"""
    gate = _gate()
    abstain, reason = gate.decide(
        [0.9], [1.0, 1.0, 1.0, 1.0], answer_status="topic_only"
    )
    assert abstain is True
    assert "主题相关" in reason


def test_irrelevant_abstains() -> None:
    gate = _gate()
    abstain, reason = gate.decide([0.9], [1.0], answer_status="irrelevant")
    assert abstain is True
    assert "无关" in reason


def test_topic_only_abstains_even_when_entailment_is_not_evaluated() -> None:
    """L3 降级（entailment=None）时 answer_status 仍是独立一路。"""
    gate = _gate()
    abstain, _ = gate.decide([0.9], None, answer_status="topic_only")
    assert abstain is True


# ---------------------------------------------------------------------------
# 2：不拦
# ---------------------------------------------------------------------------


def test_answered_does_not_abstain() -> None:
    gate = _gate()
    assert gate.decide([0.9], [1.0], answer_status="answered") == (False, "")


def test_empty_status_means_not_participating() -> None:
    """v1 模板 / 模型没给 answer_status 时行为必须与改动前逐字一致。

    这是最不能搞错的一条：若把"没给"当成"不合格"，评测侧（仍用 v1）与
    显式退回旧口径的生产配置会被全量误拒。
    """
    gate = _gate()
    assert gate.decide([0.9], [1.0]) == (False, "")
    assert gate.decide([0.9], [1.0], answer_status="") == (False, "")
    # 且与传 None 等价（编排层可能拿到 None）
    assert gate.decide([0.9], [1.0], answer_status=None) == (False, "")


def test_status_does_not_override_strong_retrieval_pass() -> None:
    """answer_status 拦得住，但不该把"检索都没过"的原因改写成主题相关。"""
    gate = _gate()
    abstain, reason = gate.decide([0.10], [1.0], answer_status="topic_only")
    assert abstain is True
    # 检索那道闸在前（第 2 步），短路在前
    assert reason == "知识库无相关内容"


# ---------------------------------------------------------------------------
# 3：顺序
# ---------------------------------------------------------------------------


def test_answer_status_is_checked_before_entailment_scores() -> None:
    """F1.2 那批样本 max=1.0；若先查蕴含分数就放行、走不到 answer_status。"""
    gate = _gate()
    # 检索分高（过第 2 步）、蕴含分全是 1.0（过第 4 步）——只有 answer_status 能拦
    abstain, reason = gate.decide([0.9], [1.0] * 6, answer_status="topic_only")
    assert abstain is True
    assert "主题相关" in reason, "拒答原因必须来自 answer_status 而不是蕴含分数"


# ---------------------------------------------------------------------------
# 4：原因可区分
# ---------------------------------------------------------------------------


def test_reasons_differ_between_topic_only_and_irrelevant() -> None:
    gate = _gate()
    _, reason_topic = gate.decide([0.9], [1.0], answer_status="topic_only")
    _, reason_irrelevant = gate.decide([0.9], [1.0], answer_status="irrelevant")
    assert reason_topic != reason_irrelevant


def test_unknown_status_value_is_ignored_not_abstained() -> None:
    """模型跑飞吐出未知值时按"没给"处理，不能凭它拒答。"""
    gate = _gate()
    assert gate.decide([0.9], [1.0], answer_status="maybe") == (False, "")
