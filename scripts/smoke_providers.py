"""Provider registry regressions for pluggable backend services."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import (  # noqa: E402
    LLM_SLOT_NAMES,
    LlmProviderSettings,
    LlmSlotSettings,
    LlmSlotsSettings,
    resolve_slot,
)
from core.embedding import EMBEDDING_PROVIDERS, create_embedder  # noqa: E402
from core.llm import LLM_PROVIDERS, create_client  # noqa: E402
from core.providers import ProviderRegistry  # noqa: E402
from core.reranker import RERANKER_PROVIDERS, create_reranker  # noqa: E402

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}: {detail}")


print("== Built-in provider families ==")
check("embedding providers", EMBEDDING_PROVIDERS.names() == ("api", "local"))
check("reranker providers", RERANKER_PROVIDERS.names() == ("api", "local"))
check("llm providers", LLM_PROVIDERS.names() == ("openai", "openai_compatible"))

print("== Dynamic registration through public factories ==")
cases = [
    (EMBEDDING_PROVIDERS, create_embedder, "smoke_embedding"),
    (RERANKER_PROVIDERS, create_reranker, "smoke_reranker"),
    (LLM_PROVIDERS, create_client, "smoke_llm"),
]
for registry, factory, name in cases:
    marker = object()
    registry.register(name, lambda _cfg, result=marker: result)
    try:
        result = factory(SimpleNamespace(provider=name))
        check(f"{name} factory dispatch", result is marker)
    finally:
        registry.unregister(name)

print("== Registry safety ==")
registry: ProviderRegistry[object, object] = ProviderRegistry("smoke")
registry.register("one", lambda cfg: cfg)
try:
    registry.register("one", lambda cfg: cfg)
    check("duplicate registration rejected", False)
except ValueError:
    check("duplicate registration rejected", True)
try:
    registry.create("missing", object())
    check("unknown provider lists choices", False)
except ValueError as exc:
    check("unknown provider lists choices", "one" in str(exc), str(exc))

print("== Named LLM providers (provider_ref inheritance) ==")
# 这一节守的是一个真实回归：provider_ref 的继承原本要求取用方调
# resolve_slot，而全项目 14 个 create_client 调用点没有一个调它，于是
# 「配了 provider_ref + model」只有 model 生效、base_url 还指着本机 Ollama，
# 拼出「拿本机要云端模型」的 404。所以下面的断言**刻意直接读
# slots.<槽位>.base_url**，不经 resolve_slot——那正是生产代码走的路径。
_DS = "https://dashscope.aliyuncs.com/compatible-mode/v1"
check(
    "dashscope 内置且指向兼容模式端点",
    LlmSlotsSettings().providers.get("dashscope", LlmProviderSettings()).base_url == _DS,
)

_slots = LlmSlotsSettings(
    providers={"acme": LlmProviderSettings(base_url="http://acme:1/v1", api_key="sk-acme")},
    generation=LlmSlotSettings(provider_ref="acme", model="acme-large"),
)
check("直接读槽位即得继承后的 base_url", _slots.generation.base_url == "http://acme:1/v1",
      _slots.generation.base_url)
check("直接读槽位即得继承后的 api_key", _slots.generation.api_key == "sk-acme")
check("未引用的槽位保持默认（不被误伤）",
      _slots.judge.base_url == "http://localhost:11434/v1", _slots.judge.base_url)

# 槽位显式写的必须压过 provider——否则「就是要让这一个槽位走别处」没法表达。
_ovr = LlmSlotsSettings(
    providers={"acme": LlmProviderSettings(base_url="http://acme:1/v1", api_key="sk-acme")},
    generation=LlmSlotSettings(provider_ref="acme", base_url="http://mine:2/v1"),
)
check("槽位显式 base_url 压过 provider", _ovr.generation.base_url == "http://mine:2/v1",
      _ovr.generation.base_url)
check("未显式写的字段仍继承", _ovr.generation.api_key == "sk-acme")

# 写错一个提供商名不该让整条问答链路崩掉，静默退回槽位自身取值。
_bad = LlmSlotsSettings(judge=LlmSlotSettings(provider_ref="no-such-provider"))
check("provider_ref 名字错误时静默退回",
      _bad.judge.base_url == "http://localhost:11434/v1", _bad.judge.base_url)

# 幂等：配置中心改一次配置会重新校验一遍，二次解析不能把已生效的值改掉。
_again = LlmSlotsSettings.model_validate(_slots.model_dump())
check("二次校验结果不变（幂等）", _again.generation.base_url == "http://acme:1/v1",
      _again.generation.base_url)

# 按类型遍历而不是照着槽位清单写死：将来加第 12 个槽位时，若解析逻辑漏了它，
# 这条会直接失败，而不是等到线上打出 404 才发现。
_all = LlmSlotsSettings(
    providers={"acme": LlmProviderSettings(base_url="http://acme:1/v1", api_key="sk-acme")},
    **{n: LlmSlotSettings(provider_ref="acme") for n in LLM_SLOT_NAMES},
)
_unresolved = [n for n in LLM_SLOT_NAMES if getattr(_all, n).base_url != "http://acme:1/v1"]
check("全部槽位都参与继承（新增槽位不会被漏掉）", not _unresolved, f"漏了 {_unresolved}")

# resolve_slot 仍是可用的公开入口，且与直接读结果一致。
check("resolve_slot 与直接读一致",
      resolve_slot(_slots, "generation").base_url == _slots.generation.base_url)
try:
    resolve_slot(_slots, "providers")
    check("resolve_slot 拒绝非槽位字段", False)
except ValueError as exc:
    check("resolve_slot 拒绝非槽位字段", "providers" in str(exc), str(exc))

print("== Token 用量记账（缓存省了多少 token 的唯一凭据）==")
# 缓存的对外承诺是"省 token"。没有实测用量，省了多少只能靠"命中数 × 拍脑袋
# 的平均值"估算。这里钉死两件事：非流式与**流式**都要记账（流式是前端主路径，
# 也是最容易被漏掉的一条），以及端点不认 stream_options 时要能降级而不是崩。
from core.llm import LLMClient, _record_usage  # noqa: E402
from core.metrics import get_metrics  # noqa: E402


class _FakeCompletions:
    """最小可用的 chat.completions 桩：按 stream_options 决定认不认。"""

    def __init__(self, *, accept_stream_options: bool = True) -> None:
        self.accept_stream_options = accept_stream_options
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if "stream_options" in kwargs and not self.accept_stream_options:
            raise ValueError("Unrecognized request argument: stream_options")
        usage = SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18)
        if kwargs.get("stream"):
            chunks = [
                SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="嗨"))],
                    usage=None,
                ),
            ]
            if "stream_options" in kwargs:
                # 真实服务端的收尾块：choices 为空，只带 usage
                chunks.append(SimpleNamespace(choices=[], usage=usage))
            return iter(chunks)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="嗨"))],
            usage=usage,
        )


def _client_with(completions) -> LLMClient:
    c = LLMClient(LlmSlotSettings(model="fake-model"))
    c._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return c


def _tokens() -> float:
    """已记账的 total token 之和。

    取 ``sum`` 而不是 ``count``：incr(value=N) 在 metrics 里是"记一次 N 的采样"，
    count 是调用次数、sum 才是 token 数。拿 count 当 token 数会得到一条恒等于
    请求数的曲线，看着像在工作，其实什么都没测。
    """
    snap = get_metrics().snapshot()
    return sum(v.get("sum", 0.0)
               for k, v in snap.items() if k.startswith("llm.tokens.total"))


_before = _tokens()
_c = _client_with(_FakeCompletions())
check("非流式调用返回内容", _c.chat([{"role": "user", "content": "hi"}]) == "嗨")
check("非流式记了 token 账", _tokens() > _before, f"{_before} -> {_tokens()}")

_before = _tokens()
_fake = _FakeCompletions()
_c = _client_with(_fake)
_text = "".join(_c.chat_stream([{"role": "user", "content": "hi"}]))
check("流式产出正文（收尾块不混进正文）", _text == "嗨", repr(_text))
check("流式默认要 usage", _fake.calls[0].get("stream_options") == {"include_usage": True},
      str(_fake.calls[0].get("stream_options")))
check("流式也记了 token 账（前端主路径不再是账本盲区）",
      _tokens() > _before, f"{_before} -> {_tokens()}")

# 端点不认 stream_options 时：宁可没有账本，也不能让流式路径整个不可用。
_fake = _FakeCompletions(accept_stream_options=False)
_c = _client_with(_fake)
_text = "".join(_c.chat_stream([{"role": "user", "content": "hi"}]))
check("端点不认 stream_options 时降级重试而不是报错", _text == "嗨", repr(_text))
check("降级后的那次请求确实去掉了 stream_options",
      len(_fake.calls) == 2 and "stream_options" not in _fake.calls[1],
      str([sorted(c.keys()) for c in _fake.calls]))

# usage 缺失是常态（不少兼容端点根本不返回），不能因此抛异常。
_record_usage("fake-model", None, streamed=False)
_record_usage("fake-model", SimpleNamespace(), streamed=True)
check("usage 缺失/残缺时静默跳过", True)

print(f"summary: {passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
