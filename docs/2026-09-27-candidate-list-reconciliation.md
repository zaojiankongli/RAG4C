# 候选清单收口审计（2026-09-27）

## 目的

交接审查条目 36–38 每条末尾的「还开着的」清单（#8 `_safe_route`/两个物化函数、
#3 投影目标 × 操作、#17 capability 生产者、#10 存储 CHECK）与库存清单头部
「后来的更正」（`backend-extensibility-inventory.md` 第 72 行）已经互相矛盾。
本轮逐项对着 live 代码与测试复核，给出最终结论，避免下一位继续追过期候选。

## 逐项结论

| 候选 | 结论 | 证据 |
|------|------|------|
| #8 `_safe_route` | **已收口**（2026-09-23 §AR） | `core/enterprise_notification_receipts.py:266` 走 `notification_route_adapters` 注册表按 `source_kind:route_code` 派发，未知/错配 fail-closed；无写死分支残留 |
| #8 `_handoff` | **已收口**（§AO，`08953c2`） | `core/notification_receipt_kinds.py` 的 `NotificationReceiptKindSpec`；未知 kind 拒绝，不再查审批 |
| #8 两个物化函数 | **已收口**（§AW，2026-09-23） | `core/notification_materializers.py` 的 `NotificationMaterializerPolicy` + `ProviderRegistry`；`materialize_quality_alert_notifications` / `materialize_pending_approval_notifications` 只是兼容壳（`enterprise_notification_materializer.py:896/919`），真分派在 `materialize_notification_source`；内置策略 `_install_builtin_notification_materializers` 冻结安装 |
| #10 身份 provider 存储 CHECK | **刻意闭合，无可做片** | 迁移 `catalog_migrations/versions/0021_enterprise_identity_federation.py:155` `provider_type IN ('oidc','saml')`；没有真实第三种 provider 类型需求时加宽 CHECK 违反本仓 fail-closed 纪律。投影侧已收（§AN）。真要新增可持久化类型时，按红线单独做 CHECK+migration+契约切片 |
| #3 投影目标 × 操作 剩余边界 | **被 authority 契约阻塞，不可实现** | §BS/§BW 明确：Graph comparator、可验证 Catalog mutation generation、target-specific atomic repair 缺权威数据契约；「下一段代码工作应在取得明确的 Catalog/target authority 契约后」，现在动手会伪造对账能力，违反「投影不是 Catalog authority」约束。已落的部分（repair adapter、enumerators、候选审计）本轮 live 复验通过 |
| #17 capability 生产者 | **当前范围已完成** | Stage17/23–32 共用 `CatalogCapabilityPolicy` + `ProviderRegistry`，frozen 兼容矩阵 follow-up review PASS（§BP）；未来新增 capability 沿同一矩阵接入，无具体待做片 |

## 本轮实跑读数（本机新跑，非历史日志）

- `pytest tests/test_notification_route_adapters.py tests/test_notification_receipt_kinds.py tests/test_notification_source_kinds.py -q` → **16 passed / 1.72s**（#8 投影 + route + handoff）
- `pytest tests/test_enterprise_notification_materializer.py -q` → **21 passed / 105.21s**（#8 物化）
- `pytest tests/test_projection_consistency_repairs.py tests/test_projection_consistency_enumerators.py tests/test_catalog_deleted_target_candidate_audit.py -q` → **21 passed / 26.24s**（#3 已落部分）
- `pytest tests/test_catalog_capability_producers.py tests/test_task_source_kind_registry.py tests/test_automation_rule_strategy_registry.py -q` → **186 passed / 33.82s**（#17 + #12）

环境备注：权威 MySQL 仍连不上，以上为 sqlite 上的 pytest；本轮未重测 OpenAPI（无契约改动）。

## 结论

**候选清单已清空**。三项开放轴中：#8 全部收口、#17 当前范围完成、#10 与 #3 剩余边界
均被「需要真实产品需求 / 权威契约先行」的前置条件阻塞——它们不是待实现的候选，
而是待裁定的门槛。下一步推进点：第三种身份 provider、Graph authority 契约、或 §9
各「待裁定」项——其中第 8/27/28/29/31 条已于同日按用户授权裁定落地（见
`docs/2026-09-27-pending-adjudications-batch.md`）；第 21a 条经用户质疑后复核，
**原「跨仓」前提被推翻**：`langchain4j-sister-project/` 只是物理放在仓库里的独立
Java 应用（Python 侧无任何调用点），证据由 `rag_stream._result_payload` 纯 Python
组装，且 `Chunk` 模型本就带 `dataset_id`（`models/schemas.py:44`）——真实缺口只是
`_result_payload` 挑字段时把它落下。21a 因此降级为普通仓内切片并已落地（见交接
条目 40 与 `docs/2026-09-27-evidence-dataset-id-deeplink.md`）。

## 本片范围

只对账文档与现状：新增本记录 + 交接审查追加条目 39/40、更正条目 21a。没有修改任何业务代码、
数据库 schema、migration、API/OpenAPI、授权或前端。
