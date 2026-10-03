# F1.5 基线接入发布门禁：验收通过（故意调坏阈值，门禁变红）

日期：2026-10-04
计划条目：`docs/plans/2026-09-29-rag4c-master-plan.md` F1.5
基线：`eval/baseline.json`（F1.2 产出，145 条）
阈值文件：`eval/.cache/thresholds/f12-proposed.json` / `f12-broken.json`

计划验收标准："**故意把阈值调坏一次，门禁变红。**"

## 一、验收结果

对同一份基线跑两组阈值：

| 阈值 | passed | failures |
|---|---|---|
| 按建议（幻觉率 ≤0.30、有据性 ≥0.60 …） | `false` | `hallucination_rate`, `degraded_rate` |
| **故意调坏**（有据性 ≥**0.95**） | `false` | `hallucination_rate`, `degraded_rate`, **`avg_groundedness`** |

**验收达成**：把 `avg_groundedness` 的门槛从 0.60 提到 0.95 之后，
`avg_groundedness`（实测 0.8298）从 PASS 变成 FAIL 并出现在 failures 里。
门禁确实会对"仍然达标但不再达标"的劣化变红——这正是计划要的那条防线。

## 二、这次补的能力：相对基线的劣化带

改造前的门禁只对**绝对阈值**判定，"作为之后每次改动的对照"这件事在绝对
阈值下做不到：有据性从 0.78 掉到 0.62 仍高于 0.60 那条线，门禁照样放行。
一次"仍然达标但明显变差"的改动就这么静默通过。

`release_gate(..., baseline=...)` 现在加第二道闸：

- `DEFAULT_REGRESSION_TOLERANCE`：质量类 0.02 / 比率类 0.05；
- **单边**：变好永远通过，只有变差才失败；
- 基线缺某指标时跳过而不是拿 0 当基线（`degraded_rate` 是 F1.2 之后才加进
  报告的，旧基线里必然没有，拿 0 当基线会误报成"上次是 0、这次涨了"）。

## 三、第一组阈值为什么仍然红——两个真实问题

按建议阈值跑，**幻觉率与降级率仍然不达标**。这不是阈值选得不对，是基线
本身揭示的两个问题（详见 `docs/2026-10-04-f12-answer-baseline.md`）：

### 幻觉率 0.6364 > 0.30

14 条不可答问题被放行。**11 条（79%）的相关性恰好是 0.00、而有据性 0.74**
——不是编造，是"答非所问"：检索到正确主题的文档，生成了确实有据的回答，
但那段回答没回答用户问的那个具体事实。

这决定了后续改动的方向：**弃权门该拦的是"相关性低"，不是"有据性低"**。

### 降级率 0.1034 > 0.05

15 条降级（`judge_failed` 9 + `entailment_unavailable` 9，有重叠）。
这一项**我不建议放宽**——它量的不是质量而是"这轮数字能不能签字"。
15 条降级意味着 `avg_groundedness` 的分母只有 110（而非 123），
`entailment_unavailable` 的 9 条更是**引用校验根本没跑过**、弃权门拿不到
第二道闸，直接推高幻觉率。

## 四、顺带修的可观测性缺口

查 9 条 `judge_failed` 的原因时发现：报告只有 `score=None`，
**没有说失败原因**。而有据性裁判只有三种失败路径
（`judge_parse_failed` / `no_claims` / `no_verdicts`，见 `eval/judges.py`），
它们的处置完全不同——前者查模型输出，后两者查切分。

于是给 `CaseResult` 加了 `judge_failures` 字段，形如
`groundedness:no_claims`。写这个字段时还踩了一个 Python 遮蔽坑：
`_run_case` 里已有一个同名的局部变量（拼 `notes` 用），新列表被它重置，
测试立刻抓到了。

## 五、复现

```bash
# 验收（故意调坏）
python -c "
import json
from pathlib import Path
from eval.run_eval import load_report, release_gate
d = load_report('eval/baseline.json')
th = json.loads(Path('eval/.cache/thresholds/f12-broken.json').read_text(encoding='utf-8'))
g = release_gate(d, th, baseline=d)
print(g['passed'], [c['metric'] for c in g['checks'] if not c['passed']])
"
# -> False ['hallucination_rate', 'degraded_rate', 'avg_groundedness']

# 下一轮改动后跑门禁（劣化带生效）
python -m eval.run_eval --pipeline rag:answer_query \
  --dataset eval/answer_eval/gold_answer_production.py:ANSWER_GOLD \
  --out eval/results-next.json --gate --baseline eval/baseline.json \
  --gate-thresholds eval/.cache/thresholds/f12-proposed.json
```
