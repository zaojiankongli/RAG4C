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

---

## 追记：路线 B 已实施 21 个文件（本节是实施结果，不是计划）

上面的「路线 B 未实施」是 2026-10-04 上午的状态。下午把它实施了。

### 已改写的文件与实测

| 批次 | 文件数 | 用例数 | 墙钟 | 代表性加速 |
|---|---|---|---|---|
| 二 | 1 | 22 | 15.4s | projection_handlers **83.6s → 15.4s（5.4x）** |
| 三 | 1 | 7 | 9.4s | chunk_revisions **2400s 超时 → 9.4s** |
| 四 | 3 | 27 | 43.0s | 含 `?timeout=20` 形态 |
| 五 | 4 | 5+1 | 57.5s | 单用例 48.9s → 33.8s |
| 六 | 3 | 26 | 26.6s | 覆盖 `schema_module().upgrade_catalog` 形态 |
| 七 | 7 | 106 | 129.0s | 脚本批量改写 + 逐个验证 |
| **合计** | **21** | **约 200** | — | — |

### 筛选判据（四条，缺一个就会误改）

1. **调用里没有 revision 参数**——`upgrade_catalog(url, DOWN_REVISION)` 这类是在
   测迁移路径本身，模板库会把被测行为整段跳过去。这一条是第六批才发现的：
   4 个 `*_migration.py` 文件按「名字 + BASELINE」筛选会通过，但它们
   `upgrade_catalog(url, DOWN_REVISION)` / `(url, "0025_...")`。
   **名字带 migration 只是线索，真正的判据是调用有没有第二个参数。**
2. 文件不含 `BASELINE_REVISION`。
3. 还没被改造过。
4. **不测库的元状态**。这一条是实施中才发现的：`test_knowledge_governance_migration.py`
   里有一个用例是

   ```python
   url = head_db_url(...)                     # 模板库：已到 head
   engine = ...; engine.execute("DROP TABLE alembic_version")   # 手工拆掉戳
   catalog_schema.stamp_existing_catalog(url)  # 测「给无戳的库补戳」
   ```

   模板库给的库**已经有戳**，整个被测行为被跳过。判据是：出现
   `stamp_existing` / 手工 `DROP alembic_version` / 对 `inspect_catalog_schema`
   的状态断言，就不能用模板库——那是在测「库的元状态」而不是「表结构」。

   发现方式也值得记：改到它时 ruff 报 F821（``catalog_schema`` 未定义），
   因为我把那个 import 删掉了。**ruff 的 F821 在这里是意外收获**——
   若那个 import 恰好只被 upgrade_catalog 用，它会静静通过，误改就过去了。

### 「模板库能加速」的边界

它**只压缩「建库」这一段**。第五批给出反例：
`test_document_delete_generation_fences_v2.py` 的**单个用例**要 33~49 秒，
而建库只占其中 4ms——时间全花在 durable-delete 编排 / 投影 worker 上。
所以这批不给「快了多少倍」的数字，只给同用例改造前后的可比值。

**归因要分开算**，否则就是把优化成果和无关的慢测试混在一起报。

### 两条操作教训

1. **脚本改写必须逐文件验证**。第五批之前试过 `scripts/apply_catalog_template.py`
   做通用改写，两个 bug（替换行丢换行符、插入 import 后行号偏移）都不会报错，
   只会**静默吞掉相邻代码行**——「看起来成功了」正是最危险的地方。
   后来改成：形态统一时用带正则的一次性脚本（第七批 7 个文件），
   **但仍然逐个跑 pytest**。
2. **`git stash` 会丢掉「尚未提交、接下来还要用」的改动**。第五批改写做完
   后做了 stash 对比实验，pop 回来时改动已经没了（grep `head_db_url` 全 0），
   重做一遍才提交上。做对比实验前要先确认工作区里没有待用的未提交改动。

### 当前状态

- 分片名单里 49 个「迁移型」文件，现在约 **21 个已用模板库**；
- 剩下的多数是**测迁移本身**（`*_migration.py` / `test_catalog_schema.py` /
  `test_catalog_integrity.py`），它们**不该**用模板库——那些正是路线 B 要
  覆盖的对象之外的部分。
