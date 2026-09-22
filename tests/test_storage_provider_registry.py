"""对象存储 provider 注册表的扩展点守卫。

一个 provider 的差异（控制台字段、必填项、能否真测）此前散在 ``PROVIDERS`` 元组、
``validate_provider_config`` 的 if 链与 ``list_types`` 的字面量表里 —— 加一个云厂商要改三处。
现在它们集中在一条 ``StorageProviderSpec`` 声明里，本文件把"三处"钉成"一处"。
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from core import storage_backends as sb

SOURCE = Path(inspect.getfile(sb))

_PROBE_SPEC = sb.StorageProviderSpec(
    name="probe", label="Probe", fields=(), requirements=()
)


def teardown_function() -> None:
    sb.unregister_provider_spec("probe")


def test_registering_a_provider_needs_no_edit_to_shared_paths() -> None:
    """One declaration makes validation, connectivity test and console schema agree."""
    sb.register_provider_spec(_PROBE_SPEC)

    sb.validate_provider_config("probe", {})  # 无必填项 -> 不抛
    assert sb.test_storage_config("probe", {})["status"] == "validated_config_only"
    names = [item["provider"] for item in sb.StorageBackendRepository(None).list_types()["providers"]]
    assert names == list(sb.PROVIDERS) + ["probe"]


def test_unknown_provider_still_reports_as_unsupported() -> None:
    with pytest.raises(sb.StorageBackendInvalid, match="unsupported provider: nope"):
        sb.validate_provider_config("nope", {})


def test_duplicate_registration_is_refused_unless_explicitly_replaced() -> None:
    with pytest.raises(sb.StorageBackendInvalid, match="already registered"):
        sb.register_provider_spec(_PROBE_SPEC.__class__(**{**_PROBE_SPEC.__dict__, "name": "local"}))
    sb.register_provider_spec(
        sb.StorageProviderSpec(name="local", label="本地目录", fields=(), requirements=()),
        replace=True,
    )
    # 还原内置声明，避免污染同批其他测试
    sb.register_provider_spec(
        next(spec for spec in sb._PROVIDER_SPECS if spec.name == "local"), replace=True
    )


def test_validation_carries_no_provider_name_branches() -> None:
    """The per-provider if-chain must not come back into the shared validators."""
    source = SOURCE.read_text(encoding="utf-8")
    for name in sb.PROVIDERS:
        assert f'provider == "{name}"' not in source, f"残留按 provider 名分支: {name}"
    assert "provider in PROVIDERS" not in source


def test_local_is_the_only_provider_claiming_a_real_write_probe() -> None:
    """Honesty invariant: without an SDK we must not report remote write success."""
    for name in sb.PROVIDERS:
        result = sb.test_storage_config(
            name,
            {
                "root_path": ".",
                "endpoint": "http://127.0.0.1:9",
                "bucket": "b",
                "access_key_id": "ak",
                "secret_access_key": "sk",
            },
        )
        if name == "local":
            assert result["status"] == "ok", name
        else:
            assert result["status"] == "validated_config_only", name
            assert "not claiming remote write success" in result["detail"]


def test_bucket_name_alias_still_satisfies_the_bucket_requirement() -> None:
    """Stored configs predate the ``bucket`` naming; the alias is part of the contract."""
    sb.validate_provider_config(
        "s3",
        {"bucket_name": "legacy", "access_key_id": "ak", "secret_access_key": "sk"},
    )


def test_required_field_messages_are_unchanged() -> None:
    """Console copy and any string-matching clients depend on these exact messages."""
    cases = {
        "local": ("local provider requires root_path", {}),
        "minio": ("minio requires endpoint", {}),
        "cos": ("cos requires bucket", {"endpoint": "http://x"}),
        "obs": ("obs requires access_key_id", {"bucket": "b"}),
        "tos": ("tos requires secret_access_key", {"bucket": "b", "access_key_id": "ak"}),
    }
    for provider, (expected, config) in cases.items():
        with pytest.raises(sb.StorageBackendInvalid) as exc:
            sb.validate_provider_config(provider, config)
        assert str(exc.value) == expected
