# F0.2 全量回归基线：慢因已定位，绿基线待一个决策（2026-10-03）

> 结论先说：**全量套件跑不完，不是外部依赖不可达，是每个用例都在重跑
> 40 个 Alembic 迁移。** 本文给出可复现的证据、量化、以及两条可选路线。
> 修法涉及测试架构取舍，我没有擅自改——见 §6 需要拍板的地方。

## 1. 事实：跑了多久、走到哪

| 跑法 | 结果 |
|---|---|
| 串行 `pytest tests/ -q` | 20 分钟到 **9%**，被超时杀掉，**零失败** |
| 串行 + 排除最慢文件 | 25 分钟到 **11%**，被超时杀掉，零失败 |
| `-n 4` 并行 | 25 分钟到 21%，随后 gw 节点崩溃（Windows 序列化问题） |
| 收集阶段 | **8.35s 收集 3934 个用例**（291 个测试文件） |

收集只占 8 秒——**慢的不是 import/collection，是执行**。
（本项目无 `pytest-timeout`，`--timeout` 不可用，防挂只能靠 shell `timeout`。）

## 2. 根因：每个用例重跑 40 个迁移

单文件实测（`--durations=10`）：

```
tests/test_source_schedule_dispatch.py  11 passed in 164.93s
  18.05s / 17.54s / 17.16s / 16.72s / 16.62s / 16.61s / 16.39s / 16.29s / 15.65s
```

**9 个用例各占 ~16.5s，而它们的等待上限只有 3s**（`_wait_completed` 里
`deadline = time.time() + 3`）。所以它们不是在轮询里慢慢耗掉的时间，
是**每个用例开头各自跑了一遍完整迁移**。

`upgrade_catalog` 做了什么（`core/catalog_schema.py:9230`）：
先 `inspect_catalog_schema` 探一遍 schema，再 `command.upgrade(..., "head")`
把 `catalog_migrations/versions/` 下 **40 个迁移全部执行一遍**。

规模：

- **50 个测试文件**引用 `upgrade_catalog`，静态调用点 **141 处**
- 调用点密度最高：`test_catalog_schema.py` 31 处、
  `test_enterprise_catalog_upgrade.py` 10 处、`test_catalog_capability_producers.py` 6 处
- 单文件墙钟对照：`test_catalog_integrity.py` 4 个用例 = 22.88s（约 5.7s/用例）

**换算**：3934 个用例里，只要相当一部分走迁移路径，串行总时长就是数小时量级。
这与 9/27 记的「15 分钟到 13%」一致，也解释了为什么 9/28 的快速失败
**没有**解决它——快速失败治的是"单次外部调用挂住"，这里是"每个用例重复做
昂贵初始化"，两回事。

## 3. 为什么不能靠并行绕过

`-n 4` 确实更快（21% vs 9%），但：

- `pyproject.toml:68-73` 明确写了**并行与性能预算断言互斥**
  （`test_run_history_store` / `test_run_registry` / `test_run_ops_api` /
  `test_run_observability_parity` / `test_source_dispatch_runtime`
  断言 p95 墙钟与 1000 事件预算）
- 本机 Windows 上 `-n 4` 跑 25 分钟后 **gw 节点崩溃**
  （`node down: Not properly terminated` + `OSError: [Errno 22]`），
  像是 xdist 在 Windows 上序列化结果集的问题，不是用例失败

所以"开并行跑全量"既拿不到可信基线（性能预算断言会被并行污染）也不稳定。

## 4. 已知项登记（与本次改动无关）

- `scripts/export_openapi.py --check` 报「后端契约已漂移」。
  已用 `git stash` 在**干净树**上复现，确认是既有问题，不在本轮范围内。
  按 F0.2 的验收口径，这一条登记为已知并写明原因。

## 5. 已取得的绿（局部）

覆盖本轮 ef 改动的四个文件：

```
tests/test_settings.py + test_config_hot_reload.py
+ test_milvus_store.py + test_search_candidate_factor.py
=> 60 passed, 1 skipped in 11.27s
```

另外 `test_circuit_breaker.py` 3s、`test_rate_limit.py` 3s、
`test_catalog_integrity.py` 4 passed/22.88s 均为绿。
**这些证明改动没破坏东西，但不等于全量基线。**

## 6. 需要拍板：绿基线怎么拿

两条路，代价不同：

**路线 A：接受"分片绿基线"**（零代码改动）
把套件按"迁移型 / 非迁移型"分两批跑，各记一次结果与耗时，
在 `docs/` 里立成常规基线口径。**当天就能拿到绿**，但它防不住跨分片的回归。

**路线 B：让迁移只跑一次**（改测试架构，收益大）
典型做法是把 `upgrade_catalog` 做成 session 级 fixture——建一次模板库，
各用例从模板库拷 SQLite 文件，取代每个用例重跑 40 个迁移。
预计能把全量从数小时压到分钟级，之后 F0.2 才真正成立。

> B 动的是测试基础设施，**不碰生产代码**，但会触及 50 个文件里的
> fixture 组织方式。改错了可能让某些用例"看着绿、其实没真正迁移到 head"——
> 这类假绿比慢更危险。所以我没有直接做。

我的建议：**先 A 拿住基线，再排 B**。理由是 A 零风险且立刻解决
"后面每一项的改动前后对比没有判据"这个燃眉问题；B 需要单独一轮
像 F4.2 那样"风险最高需三处同改"的谨慎对待。
