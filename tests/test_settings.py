from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from config.settings import KnowledgeSecuritySettings, Settings, SourcesSettings, _iter_field_paths


def test_knowledge_security_defaults_and_bounds() -> None:
    value = KnowledgeSecuritySettings()

    assert value.actor_signing_secret is None
    assert value.actor_max_ttl_s == 900
    for invalid in (59, 3601):
        with pytest.raises(ValidationError):
            KnowledgeSecuritySettings(actor_max_ttl_s=invalid)
    assert KnowledgeSecuritySettings(actor_max_ttl_s=60).actor_max_ttl_s == 60
    assert KnowledgeSecuritySettings(actor_max_ttl_s=3600).actor_max_ttl_s == 3600


def test_knowledge_security_environment_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "RAG4C_KNOWLEDGE_SECURITY_ACTOR_SIGNING_SECRET",
        "dedicated-actor-signing-secret",
    )
    monkeypatch.setenv("RAG4C_KNOWLEDGE_SECURITY_ACTOR_MAX_TTL_S", "1200")

    value = Settings().knowledge_security

    assert isinstance(value.actor_signing_secret, SecretStr)
    assert value.actor_signing_secret.get_secret_value() == "dedicated-actor-signing-secret"
    assert value.actor_max_ttl_s == 1200


def test_knowledge_security_secret_is_hidden_and_redacted() -> None:
    sentinel = "SENTINEL_KNOWLEDGE_ACTOR_SECRET"
    settings = Settings(knowledge_security=KnowledgeSecuritySettings(actor_signing_secret=sentinel))
    public_paths = {".".join(path) for path in _iter_field_paths(Settings)}
    all_paths = {".".join(path) for path in _iter_field_paths(Settings, include_hidden=True)}

    assert "knowledge_security.actor_max_ttl_s" in public_paths
    assert "knowledge_security.actor_signing_secret" not in public_paths
    assert "knowledge_security.actor_signing_secret" in all_paths
    assert sentinel not in repr(settings)
    assert sentinel not in settings.model_dump_json()
    assert "**********" in settings.model_dump_json()



def test_source_control_allowlists_default_fail_closed_and_normalize() -> None:
    defaults = SourcesSettings()
    assert defaults.local_allowed_roots == []
    assert defaults.local_allowed_extensions == []
    assert defaults.github_allowed_repositories == []
    assert defaults.github_allowed_organizations == []

    configured = SourcesSettings(
        local_allowed_roots=[" ./data/docs "],
        local_allowed_extensions=["MD", ".Txt"],
        github_allowed_repositories=[" OpenAI/OpenAI-Python "],
        github_allowed_organizations=[" Trusted-Org "],
    )
    assert configured.local_allowed_roots == ["./data/docs"]
    assert configured.local_allowed_extensions == [".md", ".txt"]
    assert configured.github_allowed_repositories == ["openai/openai-python"]
    assert configured.github_allowed_organizations == ["trusted-org"]


def test_source_control_allowlists_reject_malformed_entries() -> None:
    for kwargs in (
        {"local_allowed_roots": [""]},
        {"local_allowed_extensions": ["../md"]},
        {"github_allowed_repositories": ["https://github.com/openai/openai-python"]},
        {"github_allowed_organizations": ["owner/repo"]},
    ):
        with pytest.raises(ValidationError):
            SourcesSettings(**kwargs)



def test_source_execution_dispatch_settings_are_bounded() -> None:
    defaults = SourcesSettings()
    assert defaults.dispatch_poll_interval_seconds > 0
    assert defaults.execution_heartbeat_seconds < defaults.execution_lease_seconds
    assert defaults.execution_workers >= 1
    assert defaults.dispatch_batch_size >= 1

    with pytest.raises(ValidationError):
        SourcesSettings(execution_lease_seconds=1, execution_heartbeat_seconds=1)


def test_oidc_redirect_allowlist_defaults_fail_closed_and_normalizes() -> None:
    defaults = KnowledgeSecuritySettings()
    assert defaults.oidc_redirect_uri_allowlist == []

    configured = KnowledgeSecuritySettings(
        oidc_redirect_uri_allowlist=[
            " HTTPS://APP.EXAMPLE.TEST:443/auth/callback ",
            "https://app.example.test/auth/callback",
            "http://LOCALHOST:1420/auth/callback",
            "http://127.0.0.1:1420/auth/callback",
        ]
    )
    assert configured.oidc_redirect_uri_allowlist == [
        "https://app.example.test/auth/callback",
        "http://localhost:1420/auth/callback",
        "http://127.0.0.1:1420/auth/callback",
    ]


def test_oidc_redirect_allowlist_environment_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "RAG4C_KNOWLEDGE_SECURITY_OIDC_REDIRECT_URI_ALLOWLIST",
        '["https://app.example.test/auth/callback", "http://127.0.0.1:1420/callback"]',
    )
    value = Settings().knowledge_security
    assert value.oidc_redirect_uri_allowlist == [
        "https://app.example.test/auth/callback",
        "http://127.0.0.1:1420/callback",
    ]


@pytest.mark.parametrize(
    "value",
    [
        "http://app.example.test/callback",
        "ftp://app.example.test/callback",
        "https://user:password@app.example.test/callback",
        "https://app.example.test/callback#fragment",
        "https:///missing-host",
        "http://192.168.1.10/callback",
        "x" * 1025,
    ],
)
def test_oidc_redirect_allowlist_rejects_unsafe_entries(value: str) -> None:
    with pytest.raises(ValidationError):
        KnowledgeSecuritySettings(oidc_redirect_uri_allowlist=[value])


def test_oidc_redirect_allowlist_is_bounded() -> None:
    allowed = [f"https://app-{index}.example.test/callback" for index in range(32)]
    assert (
        len(
            KnowledgeSecuritySettings(
                oidc_redirect_uri_allowlist=allowed
            ).oidc_redirect_uri_allowlist
        )
        == 32
    )
    with pytest.raises(ValidationError):
        KnowledgeSecuritySettings(
            oidc_redirect_uri_allowlist=[
                f"https://app-{index}.example.test/callback" for index in range(33)
            ]
        )
