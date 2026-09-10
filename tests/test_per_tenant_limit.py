"""R2-B：按租户并发限流测试（server.app._inc_pending/_dec_pending）。

覆盖：
- per-tenant 上限未配置（0）时行为与旧版一致（仅全局队列上限）；
- 配置上限后，单租户达到上限返回 False（429）；
- 释放后恢复可准入；
- 不同租户互不影响（租户 A 占满不影响 B）。
"""
from __future__ import annotations

import pytest


@pytest.fixture
def app_module():
    import server.app as m

    return m


def _reset_state(m) -> None:
    m._pending = 0
    m._pending_per_tenant.clear()


def test_no_per_tenant_limit_behaves_like_before(app_module, monkeypatch) -> None:
    m = app_module
    _reset_state(m)
    monkeypatch.setattr(m, "QUERY_MAX_CONCURRENT_PER_TENANT", 0)
    # 只受全局队列上限约束
    assert m._inc_pending("tenant-a") is True
    m._dec_pending("tenant-a")
    assert m._pending == 0


def test_per_tenant_limit_rejects_when_exceeded(app_module, monkeypatch) -> None:
    m = app_module
    _reset_state(m)
    monkeypatch.setattr(m, "QUERY_MAX_CONCURRENT_PER_TENANT", 2)
    assert m._inc_pending("tenant-a") is True
    assert m._inc_pending("tenant-a") is True
    assert m._inc_pending("tenant-a") is False  # 第 3 个被拒
    assert m._pending == 2  # 全局也只计成功的


def test_release_frees_tenant_slot(app_module, monkeypatch) -> None:
    m = app_module
    _reset_state(m)
    monkeypatch.setattr(m, "QUERY_MAX_CONCURRENT_PER_TENANT", 1)
    assert m._inc_pending("tenant-a") is True
    m._dec_pending("tenant-a")
    assert m._inc_pending("tenant-a") is True  # 释放后可再准入


def test_tenants_are_independent(app_module, monkeypatch) -> None:
    m = app_module
    _reset_state(m)
    monkeypatch.setattr(m, "QUERY_MAX_CONCURRENT_PER_TENANT", 1)
    assert m._inc_pending("tenant-a") is True
    assert m._inc_pending("tenant-b") is True  # 不同租户互不影响
    assert m._inc_pending("tenant-a") is False
    assert m._inc_pending("tenant-b") is False
    m._dec_pending("tenant-a")
    assert m._inc_pending("tenant-a") is True
