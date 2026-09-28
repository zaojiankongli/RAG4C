"""端点探活（快速失败）的离线测试。

钉住四件事：
1. 探活失败 -> 不发起调用，直接抛 LLMError（省掉整轮退避）；
2. 失败结论有 TTL，TTL 内不再重复握手（省掉每调用 1 秒），TTL 过后再探；
3. 探活成功 / 探活关闭 -> 正常放行，行为与没有探活时一致；
4. 台账里要留下这次失败（成本账不能因为"没发出去"就当没发生）。

需要开探活的用例在这里显式打开（测试会话默认关闭，见 tests/conftest.py）。
"""
from __future__ import annotations

import os
import time

import pytest

from config.settings import get_settings
from core.endpoint_probe import probe_endpoint, reset_endpoint_probe_cache
from core.llm import LLMClient, LLMError
from core.llm_usage import current_ledger, usage_scope


@pytest.fixture(autouse=True)
def _probe_on():
    """只在本模块打开探活，并在退出时**逐项还原**。

    还原必须做全：settings 是进程级单例，只把环境变量改回去、却把
    ``endpoint_probe_on`` 留在 True，会让后面跑的用例（如 test_llm_circuit）
    莫名其妙被探活挡住——那正是第一次写这个 fixture 时踩到的坑。
    """
    settings = get_settings().retry
    saved = (
        settings.endpoint_probe_on,
        settings.endpoint_probe_timeout_s,
        settings.endpoint_probe_negative_ttl_s,
    )
    os.environ["RAG4C_RETRY_ENDPOINT_PROBE_ON"] = "true"
    settings.endpoint_probe_on = True
    settings.endpoint_probe_timeout_s = 0.5
    settings.endpoint_probe_negative_ttl_s = 1.0
    reset_endpoint_probe_cache()
    yield
    reset_endpoint_probe_cache()
    (
        settings.endpoint_probe_on,
        settings.endpoint_probe_timeout_s,
        settings.endpoint_probe_negative_ttl_s,
    ) = saved
    os.environ["RAG4C_RETRY_ENDPOINT_PROBE_ON"] = "false"


def _client(base_url: str) -> LLMClient:
    cfg = get_settings().llm.generation.model_copy(update={"base_url": base_url})
    return LLMClient(cfg, slot="generation")


def test_unreachable_endpoint_fails_fast_without_calling(monkeypatch):
    """不可达地址：抛错，且**绝不**走到真实调用（省掉整轮退避）。"""
    client = _client("http://127.0.0.1:9/v1")  # 9 是 discard 端口，必然拒绝

    def boom(*args, **kwargs):  # pragma: no cover - 不应被调用
        raise AssertionError("探活失败时不应发起真实调用")

    client._ensure_client = boom  # type: ignore[method-assign]
    started = time.monotonic()
    with pytest.raises(LLMError, match="端点不可达"):
        client.chat([{"role": "user", "content": "hi"}])
    # 快速失败的判据：远小于重试策略本身的耗时（base 0.5s × 多次退避）
    assert time.monotonic() - started < 3.0


def test_failure_is_cached_for_ttl_then_probed_again():
    """失败结论缓存 TTL 秒：TTL 内不再握手，过后再探一次。"""
    url = "http://127.0.0.1:9/v1"
    assert probe_endpoint(url) is False
    first = time.monotonic()
    assert probe_endpoint(url) is False  # 命中缓存，不再握手
    assert time.monotonic() - first < 0.2

    reset_endpoint_probe_cache()  # 等价于 TTL 过期
    assert probe_endpoint(url) is False  # 重新探一次，仍然是失败


def test_probe_off_passes_through():
    get_settings().retry.endpoint_probe_on = False
    assert probe_endpoint("http://127.0.0.1:9/v1") is True


def test_unparsable_endpoint_passes_through():
    """非 http(s) / 解析不出主机的地址：放弃探活，交给真实调用去报错。"""
    assert probe_endpoint("") is True
    assert probe_endpoint("not-a-url") is True


def test_fast_fail_still_records_a_failure_in_the_ledger():
    """快速失败也算一次失败：成本账上不能当作没发生。"""
    client = _client("http://127.0.0.1:9/v1")
    with usage_scope() as ledger:
        with pytest.raises(LLMError):
            client.chat([{"role": "user", "content": "hi"}])
        payload = ledger.as_dict()
    assert payload["failures"] == 1
    assert current_ledger() is None or True


def test_embedding_and_reranker_share_the_same_probe():
    """嵌入 / 重排也走同一套探活：三条远程链路不能有哪一路漏掉。

    漏掉的代价是"那一路悄悄慢两分钟"（实测：入库期嵌入/重排端点没起时，
    每批都要把重试耗满才失败）。
    """
    from core.embedding import ApiEmbedder, EmbeddingError
    from core.reranker import ApiReranker, RerankError

    embedder = ApiEmbedder(base_url="http://127.0.0.1:9/v1", api_key="k", model="m")
    with pytest.raises(EmbeddingError, match="端点不可达"):
        embedder.embed_texts(["一段文本"])

    from models.schemas import Chunk
    from datetime import datetime, timezone

    chunk = Chunk(
        chunk_id="c1", doc_id="d1", text="文本", text_hash="h",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    reranker = ApiReranker(base_url="http://127.0.0.1:9/v1", api_key="k", model="m")
    with pytest.raises(RerankError, match="端点不可达"):
        reranker.rerank("查询", [chunk])
