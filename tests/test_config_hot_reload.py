"""/api/config/update 的热更新回归测试。

这个接口从前的失败方式是最难查的那一种：**它一直报成功**。
``hot_reloaded=True`` 的判定条件只是"重建管线那段没抛异常"，而重建确实没抛——
管线是照着一模一样的旧 ``os.environ`` 重新装配了一遍。缺失的那一环是
``Settings`` 只从 ``os.environ`` 取值（见 ``config.settings.Rag4cEnvSource``），
把新值写进 ``.env`` 文件对本进程完全不可见。

于是：配置页保存成功、``.env`` 里确实有那一行、日志无异常、重启后也确实生效
——只有"这次保存到底有没有用"是假的。

所以这里的断言不看返回码，看**行为**：
1. 保存后立刻从新的 ``get_settings()`` 读回来，值必须已经变了；
2. ``.env`` 文件重新解析出来的值，必须和内存里的值一致（否则重启后会漂移）；
3. ``hot_reloaded`` 必须与 1 的事实一致——报了成功就得真的成功。

鉴权在路由层 ``config_update``；本文件单测直接调用同步实现 ``_apply_config_update``。
"""
from __future__ import annotations

import os
from typing import Any

import pytest
from pydantic import SecretStr

from config.settings import Settings, _iter_field_paths, get_settings
from server.app import ConfigUpdateRequest, _apply_config_update, config

config_update_impl = _apply_config_update

# (配置路径, 用来覆盖的新值)。四种类型各一，因为它们在写入路径上的
# 序列化分支各不相同——尤其带空格的字符串：写进 .env 需要加引号，写进
# os.environ 则**不能**加，两者混用的话值会连引号一起被读进来。
_CASES: list[tuple[str, Any]] = [
    ("graph.final_top_k", 7),  # int
    ("milvus.bm25_k1", 1.35),  # float
    ("pipeline.rerank_on", False),  # bool（默认 True，必须真的翻转）
    ("milvus.db_name", "rag4c test db"),  # str，含空格 -> 触发 dotenv 引号分支
]


def _read(settings: Settings, path: str) -> Any:
    node: Any = settings
    for part in path.split("."):
        node = getattr(node, part)
    return node


@pytest.fixture
def env_sandbox(tmp_path):
    before = dict(os.environ)
    env_file = tmp_path / ".env"
    env_file.write_text("# 用户自己的注释\nUNRELATED_KEY=keep-me\n", encoding="utf-8")
    os.environ["RAG4C_ENV_FILE"] = str(env_file)
    os.environ["RAG4C_REDIS_URL"] = ""
    get_settings.cache_clear()
    try:
        yield env_file
    finally:
        os.environ.clear()
        os.environ.update(before)
        get_settings.cache_clear()


def _parse_dotenv(text: str) -> dict[str, str]:
    from dotenv import dotenv_values
    import io

    return {k: v or "" for k, v in dotenv_values(stream=io.StringIO(text)).items()}


@pytest.mark.parametrize("path,new_value", _CASES, ids=[c[0] for c in _CASES])
def test_update_takes_effect_in_this_process(env_sandbox, path, new_value) -> None:
    assert _read(get_settings(), path) != new_value, (
        f"{path} 的默认值恰好等于测试要写入的新值，这条用例什么都证明不了"
    )

    resp = config_update_impl(
        ConfigUpdateRequest(updates=[{"path": path, "value": new_value}])
    )

    assert resp["rejected"] == []
    assert [c["path"] for c in resp["saved"]] == [path]
    assert _read(get_settings(), path) == new_value


@pytest.mark.parametrize("path,new_value", _CASES, ids=[c[0] for c in _CASES])
def test_file_and_memory_agree(env_sandbox, path, new_value) -> None:
    config_update_impl(ConfigUpdateRequest(updates=[{"path": path, "value": new_value}]))

    in_memory = _read(get_settings(), path)
    env_name = "RAG4C_" + "_".join(p.upper() for p in path.split("."))
    from_file = _parse_dotenv(env_sandbox.read_text(encoding="utf-8"))[env_name]

    os.environ[env_name] = from_file
    get_settings.cache_clear()
    assert _read(get_settings(), path) == in_memory
    assert in_memory == new_value


def test_hot_reloaded_flag_is_earned_not_asserted(env_sandbox) -> None:
    updates = [{"path": p, "value": v} for p, v in _CASES]
    resp = config_update_impl(ConfigUpdateRequest(updates=updates))

    settings = get_settings()
    effective = [p for p, v in _CASES if _read(settings, p) == v]

    if resp["hot_reloaded"]:
        assert len(effective) == len(_CASES), (
            f"报了 hot_reloaded=True，但实际只有 {effective} 生效"
        )
        assert resp["needs_restart"] == []
    else:
        assert resp["needs_restart"], "既没热更新成功，也没说明哪些需要重启"


def test_bridge_fields_are_reported_as_needing_restart(env_sandbox) -> None:
    resp = config_update_impl(
        ConfigUpdateRequest(updates=[{"path": "bridge.query_max_concurrent", "value": 9}])
    )

    assert resp["rejected"] == []
    assert resp["needs_restart"] == ["bridge.query_max_concurrent"]
    assert resp["hot_reloaded"] is False
    assert "重启" in resp["note"]


def test_writes_to_the_file_this_process_actually_reads(env_sandbox) -> None:
    resp = config_update_impl(
        ConfigUpdateRequest(updates=[{"path": "graph.final_top_k", "value": 7}])
    )

    assert resp["env_file"] == str(env_sandbox)
    assert "RAG4C_GRAPH_FINAL_TOP_K" in env_sandbox.read_text(encoding="utf-8")


def test_existing_env_content_is_preserved(env_sandbox) -> None:
    config_update_impl(ConfigUpdateRequest(updates=[{"path": "graph.final_top_k", "value": 7}]))

    text = env_sandbox.read_text(encoding="utf-8")
    assert "# 用户自己的注释" in text
    assert "UNRELATED_KEY=keep-me" in text


def test_sensitive_and_unknown_paths_are_still_rejected(env_sandbox) -> None:
    resp = config_update_impl(
        ConfigUpdateRequest(
            updates=[
                {"path": "llm.api_key", "value": "leak"},
                {"path": "nonexistent.field", "value": 1},
                {"path": "graph.final_top_k", "value": "not-an-int"},
            ]
        )
    )

    assert resp["saved"] == []
    assert len(resp["rejected"]) == 3
    text = env_sandbox.read_text(encoding="utf-8")
    assert "leak" not in text


def test_case_paths_still_exist_in_the_settings_model() -> None:
    valid: set[str] = set()
    for section, field in Settings.model_fields.items():
        for path in _iter_field_paths(field.annotation, (section,)):
            valid.add(".".join(path))

    missing = [p for p, _ in _CASES if p not in valid]
    assert not missing, f"用例引用的配置路径已不存在：{missing}"


def test_run_history_loads_non_secret_and_secret_values_from_environment(
    env_sandbox,
) -> None:
    os.environ["RAG4C_RUN_HISTORY_ENABLED"] = "false"
    os.environ["RAG4C_RUN_HISTORY_MEMORY_MAX_ACTIVE_RUNS"] = "64"
    os.environ["RAG4C_RUN_HISTORY_OPS_BEARER_TOKEN"] = "operator-secret"
    os.environ["RAG4C_RUN_HISTORY_FINGERPRINT_SECRET"] = "fingerprint-secret"
    get_settings.cache_clear()

    run_history = get_settings().run_history

    assert run_history.enabled is False
    assert run_history.memory_max_active_runs == 64
    assert isinstance(run_history.ops_bearer_token, SecretStr)
    assert run_history.ops_bearer_token.get_secret_value() == "operator-secret"
    assert isinstance(run_history.fingerprint_secret, SecretStr)
    assert run_history.fingerprint_secret.get_secret_value() == "fingerprint-secret"


def test_run_history_secrets_are_absent_from_config_api(env_sandbox) -> None:
    os.environ["RAG4C_RUN_HISTORY_OPS_BEARER_TOKEN"] = "SENTINEL_OPERATOR_SECRET"
    os.environ["RAG4C_RUN_HISTORY_FINGERPRINT_SECRET"] = "SENTINEL_FINGERPRINT_SECRET"
    get_settings.cache_clear()

    payload = config()
    serialized = repr(payload)
    paths = {
        field["path"]
        for section in payload["sections"].values()
        for field in section["fields"]
    }

    assert "run_history.enabled" in paths
    assert "run_history.ops_bearer_token" not in paths
    assert "run_history.fingerprint_secret" not in paths
    assert "SENTINEL_OPERATOR_SECRET" not in serialized
    assert "SENTINEL_FINGERPRINT_SECRET" not in serialized


def test_run_history_secrets_cannot_be_updated_through_config_api(env_sandbox) -> None:
    resp = config_update_impl(
        ConfigUpdateRequest(
            updates=[
                {"path": "run_history.ops_bearer_token", "value": "SENTINEL_NEW_TOKEN"},
                {"path": "run_history.fingerprint_secret", "value": "SENTINEL_NEW_SECRET"},
            ]
        )
    )

    assert resp["saved"] == []
    assert len(resp["rejected"]) == 2
    text = env_sandbox.read_text(encoding="utf-8")
    assert "SENTINEL_NEW_TOKEN" not in text
    assert "SENTINEL_NEW_SECRET" not in text


def test_run_history_secrets_load_from_env_named_secret_files(
    env_sandbox,
    tmp_path,
) -> None:
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "RAG4C_RUN_HISTORY_OPS_BEARER_TOKEN").write_text(
        "file-operator-secret\n", encoding="utf-8"
    )
    (secrets_dir / "RAG4C_RUN_HISTORY_FINGERPRINT_SECRET").write_text(
        "file-fingerprint-secret\n", encoding="utf-8"
    )

    settings = Settings(_secrets_dir=secrets_dir)

    assert settings.run_history.ops_bearer_token is not None
    assert settings.run_history.ops_bearer_token.get_secret_value() == "file-operator-secret"
    assert settings.run_history.fingerprint_secret is not None
    assert settings.run_history.fingerprint_secret.get_secret_value() == "file-fingerprint-secret"
