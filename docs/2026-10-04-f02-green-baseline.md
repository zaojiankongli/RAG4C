# F0.2 全量回归绿基线：路线 A 已落地，路线 B 的收益已实测

日期：2026-10-04
前置诊断：`docs/2026-10-03-regression-suite-slowdown-diagnosis.md`
工具：`scripts/f02_sharded_baseline.py`

## 一句话

**路线 A（分片基线）已落地，当天可拿绿；路线 B（模板库）的收益实测为
3432 倍、覆盖 96% 的迁移型文件——但它要动测试基础设施，是产品/工程决策，
本文只给数据。**

## 根因回顾

210 次 `upgrade_catalog` 调用散在 50 个测试文件里（308 个文件的 16%，
但**用例数**占比高得多），每个用例都要建库 + 跑 40 个 alembic 迁移。
单条迁移型用例实测 **19 秒**，于是全量是数小时级。

「开并行跑全量」已被否：xdist 在 Windows 上 gw 节点崩溃
（`node down: Not properly terminated`），且并行会污染性能预算断言。

## 路线 A：分片绿基线（已落地）

`scripts/f02_sharded_baseline.py` 按「文件内容是否出现 `upgrade_catalog`」
把套件分成两片，各跑一次、各记一次结果与耗时。

**分片名单是动态算出来的，不写死。** 写死的名单会随新测试加入而腐烂，
而"这个文件属于哪片"是能机械判定的属性，没有理由手写。

| 片 | 文件数 | 说明 |
|---|---|---|
| 迁移型 | **49** | 每个用例都建库 + 跑 40 个迁移 |
| 非迁移型 | **259** | 其余 |
| 合计 | 308 | |

**刻意不判定通过/不通过**——只产出「跑完了、结果是什么、耗时多少」。
理由与 `run_full_gate.py --real-upstream` 相同：没有可信阈值时给出
「门禁通过」的错觉比不给更糟。建基线时用 `--assert-no-loss` 把
「退出码为 0」变成硬要求。

路线 A 的代价要说清：**它防不住跨分片的回归**。一个既改迁移型又改非迁移型
文件的提交，两片各自都绿、组合起来却坏了。这是"先有基线"与"基线完整"
之间的取舍，不是两全。

## 路线 B：模板库（收益已实测，未实施）

### 核心假设验证通过

「建一次已迁移的 SQLite 模板库，各用例拷文件」——实测：

| 操作 | 耗时 | 校验 |
|---|---|---|
| 建模板库（`upgrade_catalog` 到 head） | **13,855.8ms** | 118 张表 |
| 拷贝 + 校验（`inspect` 表数 + 查 `alembic_version`） | **4.0ms/次** | 5 次全部表数一致、可查 |
| **加速比** | **3432x** | — |

**拷贝出来的库是可用的**：表数一致、`alembic_version` 可查。也就是说
"拷文件"确实能替代"重跑迁移"。

### 覆盖 96%

| 类别 | 文件数 | 模板库能否替代 |
|---|---|---|
| 调 `upgrade_catalog` 且只要 head（默认） | **48** | ✓ 能 |
| 调 `upgrade_catalog(url, BASELINE_REVISION)` | **2** | ✗ 不能（必须真跑迁移） |

**48/50 = 96% 的迁移型文件可被模板库覆盖。**

### 一个必须说清的坑

「从已迁移的模板库再跑 `upgrade_catalog`」**不是免费的**：实测 **6,108.3ms**。

原因在 `core/catalog_schema.py:9230` 的 `upgrade_catalog`——它先
`inspect_catalog_schema`，再无条件 `command.upgrade(...)`，**没有"已在 head
就早退"的分支**。所以：

- 对只要 head 的用例：**直接拷模板、别再调 upgrade**（4ms vs 13.9s）；
- 对要 baseline 的用例：必须真跑（那 2 个文件）。

也就是说模板库的正确用法是**替换** `upgrade_catalog(url)`，不是在其后追加。

### 预估收益

迁移型 49 个文件、210 次调用，按单条 19s 算约 **66 分钟**。
替换成 4ms 拷贝后约 **1 秒**（模板库建一次 14s）。

**全量回归从数小时降到分钟级**，F0.2 才真正成立——这与前置诊断里
「预计能把全量从数小时压到分钟级」的预判一致，现在它有了实测数字。

## 建议

**先立路线 A 的基线，今天就能用；路线 B 排进下一个迭代。**

理由：

1. 路线 A 零代码改动、当天可用，且让「今天有没有回归」这个问题**有答案**；
2. 路线 B 的收益已实测充分（3432x / 96% 覆盖），不存在"要不要做"的疑问，
   只剩"什么时候做"；
3. 路线 B 要触及 50 个文件里的测试基础设施，属于工程投入决策。

**如果只做一件事**：做路线 B。它把 F0.2 从"分片基线"变成"真全量基线"，
而两者的区别正是「跨分片回归能不能被发现」。

## 复现

```bash
# 路线 A：跑非迁移型那一片
python scripts/f02_sharded_baseline.py --shard plain

# 路线 A：两片都跑 + 硬要求退出码为 0
python scripts/f02_sharded_baseline.py --shard both --assert-no-loss

# 路线 B 的收益实测
python - <<'PY'
import shutil, tempfile, time
from pathlib import Path
from sqlalchemy import create_engine, inspect
from core.catalog_schema import upgrade_catalog
tmp = Path(tempfile.mkdtemp()); tpl = tmp / "t.db"
t0 = time.perf_counter(); upgrade_catalog(f"sqlite:///{tpl.as_posix()}")
print("建模板", round((time.perf_counter()-t0)*1000, 1), "ms")
ts = []
for i in range(5):
    d = tmp / f"c{i}.db"; t0 = time.perf_counter(); shutil.copyfile(tpl, d)
    ts.append((time.perf_counter()-t0)*1000)
print("拷贝", round(sum(ts)/len(ts), 1), "ms/次")
PY
```
