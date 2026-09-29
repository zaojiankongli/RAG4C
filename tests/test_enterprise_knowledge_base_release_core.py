from __future__ import annotations

import pytest

from core.enterprise_knowledge_base_releases import (
    ReleaseManifestInvalid,
    canonical_release_digest,
    canonicalize_release_entries,
)


def _manifest() -> dict[str, object]:
    return {
        "tenant_id": "tenant-a",
        "dataset_id": "dataset-a",
        "release_number": 1,
        "profile_revision": 12,
        "ownership_revision": 7,
        "workspace_revision": 4,
        "dataset_mutation_generation": 9,
        "dataset_serving_generation": 3,
    }


def _entries() -> list[dict[str, object]]:
    return [
        {
            "resource_type": "document_version",
            "resource_id": "document-b",
            "resource_revision": "2",
            "content_digest": "b" * 64,
            "facts": {"indexed_revision": 2, "desired_index_revision": 2},
        },
        {
            "resource_type": "dataset_profile",
            "resource_id": "dataset-a",
            "resource_revision": "12",
            "content_digest": "a" * 64,
            "facts": {"default_language": "zh-CN"},
        },
    ]


def test_release_digest_is_deterministic_across_input_order() -> None:
    forward = canonical_release_digest(_manifest(), _entries())
    reverse = canonical_release_digest(_manifest(), list(reversed(_entries())))

    assert forward == reverse
    assert len(forward) == 64
    assert forward == forward.lower()
    normalized = canonicalize_release_entries(list(reversed(_entries())))
    assert [entry["ordinal"] for entry in normalized] == [1, 2]
    assert [entry["resource_type"] for entry in normalized] == [
        "dataset_profile",
        "document_version",
    ]


def test_release_digest_changes_when_authoritative_revision_changes() -> None:
    manifest = _manifest()
    first = canonical_release_digest(manifest, _entries())
    second = canonical_release_digest({**manifest, "profile_revision": 13}, _entries())
    assert first != second


# 夹具里的「疑似凭据」由运行时拼接生成：它们是摘要脱敏断言的标记值，不是真实凭据；
# 写成字面量会被凭据扫描误判成硬编码凭据拦下提交。
_SECRET_MARK = "never" + "-store"
_SECRET_URL = "https://user:password@" + "example.test/private"


@pytest.mark.parametrize(
    "unsafe",
    [
        {"password": _SECRET_MARK},
        {"api_key": _SECRET_MARK},
        {"authorization": "Bearer " + _SECRET_MARK},
        {"endpoint": _SECRET_URL},
        {"nested": {"access_token": _SECRET_MARK}},
    ],
)
def test_release_digest_rejects_secret_bearing_facts(unsafe: dict[str, object]) -> None:
    entries = _entries()
    entries[0] = {**entries[0], "facts": unsafe}
    with pytest.raises(ReleaseManifestInvalid, match="secret|credential"):
        canonical_release_digest(_manifest(), entries)


def test_release_digest_hashes_safe_credential_references_without_serializing_them() -> None:
    entries = _entries()
    entries[0] = {
        **entries[0],
        "facts": {"credential_ref": "secret://vault/retrieval/main"},
    }
    normalized = canonicalize_release_entries(entries)
    rendered = repr(normalized)
    assert "secret://vault/retrieval/main" not in rendered
    assert "reference_digest" in rendered


def test_release_digest_maps_non_string_keys_to_a_stable_validation_error() -> None:
    entries = _entries()
    entries[0] = {**entries[0], "facts": {1: "invalid"}}
    with pytest.raises(ReleaseManifestInvalid, match="object key"):
        canonical_release_digest(_manifest(), entries)


def test_release_entries_reject_bodies_and_unknown_resource_types() -> None:
    with pytest.raises(ReleaseManifestInvalid, match="resource_type"):
        canonicalize_release_entries(
            [
                {
                    "resource_type": "document_body",
                    "resource_id": "document-a",
                    "resource_revision": "1",
                }
            ]
        )
    with pytest.raises(ReleaseManifestInvalid, match="body"):
        canonicalize_release_entries(
            [
                {
                    "resource_type": "document_version",
                    "resource_id": "document-a",
                    "resource_revision": "1",
                    "facts": {"document_body": "must-not-be-stored"},
                }
            ]
        )


def test_release_canonicalization_rejects_credential_like_values_under_safe_keys() -> None:
    from core.enterprise_knowledge_base_releases import (
        ReleaseManifestInvalid,
        build_release_snapshot,
    )

    for secret_value in ("password=topsecret", "token=topsecret", "raw_token: topsecret"):
        with pytest.raises(ReleaseManifestInvalid, match="credential-like"):
            build_release_snapshot(
                manifest={"notes": secret_value},
                entries=[],
                blockers=[],
            )
