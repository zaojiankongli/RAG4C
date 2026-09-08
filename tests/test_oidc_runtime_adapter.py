from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest

from core.enterprise_oidc_runtime import AuthlibOidcRuntimeClient, OidcRuntimeError


class MemoryResolver:
    def __init__(self, addresses: dict[str, tuple[str, ...]] | None = None) -> None:
        self.addresses = addresses or {}
        self.calls: list[str] = []

    def resolve_host_addresses(self, host: str) -> tuple[str, ...]:
        self.calls.append(host)
        return self.addresses.get(host, ("93.184.216.34",))


def _provider(issuer: str = "https://issuer.example.test") -> dict[str, Any]:
    return {
        "issuer_url": issuer,
        "client_id": "rag4c-client",
        "secret_ref": "env://OIDC_CLIENT_SECRET",
        "scopes": ["openid", "email"],
    }


def _metadata(**overrides: str) -> dict[str, Any]:
    result = {
        "issuer": "https://issuer.example.test",
        "authorization_endpoint": "https://authorize.example.test/authorize",
        "token_endpoint": "https://token.example.test/token",
        "jwks_uri": "https://jwks.example.test/keys",
    }
    result.update(overrides)
    return result


def _factory(
    handler: Callable[[httpx.Request], httpx.Response],
    captured: list[dict[str, Any]],
):
    def create(**kwargs: Any) -> httpx.Client:
        captured.append(dict(kwargs))
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    return create


def _adapter(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    resolver: MemoryResolver | None = None,
    max_bytes: int = 16_384,
    captured: list[dict[str, Any]] | None = None,
) -> AuthlibOidcRuntimeClient:
    calls = captured if captured is not None else []
    return AuthlibOidcRuntimeClient(
        secret_resolver=lambda _reference: "client-secret",
        resolver=resolver or MemoryResolver(),
        http_client_factory=_factory(handler, calls),
        max_response_bytes=max_bytes,
    )


def test_adapter_rejects_private_or_mixed_dns_before_issuer_metadata_request() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, json=_metadata(), request=request)

    for addresses in (("127.0.0.1",), ("93.184.216.34", "10.0.0.7"), ()):
        adapter = _adapter(
            handler,
            resolver=MemoryResolver({"issuer.example.test": addresses}),
        )
        with pytest.raises(OidcRuntimeError) as captured:
            adapter.build_authorization_url(
                provider=_provider(),
                redirect_uri="https://app.example.test/callback",
                state="state",
                nonce="nonce",
                code_challenge="challenge",
            )
        assert captured.value.code == "oidc_runtime_endpoint_unsafe"
    assert requests == []


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://authorize.example.test/authorize",
        "https://user@authorize.example.test/authorize",
        "https://authorize.example.test/authorize#fragment",
    ],
)
def test_adapter_rejects_unsafe_authorization_endpoint(endpoint: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_metadata(authorization_endpoint=endpoint),
            request=request,
        )

    adapter = _adapter(handler)
    with pytest.raises(OidcRuntimeError) as captured:
        adapter.build_authorization_url(
            provider=_provider(),
            redirect_uri="https://app.example.test/callback",
            state="state",
            nonce="nonce",
            code_challenge="challenge",
        )
    assert captured.value.code == "oidc_runtime_endpoint_unsafe"


@pytest.mark.parametrize(
    ("field", "host"),
    [
        ("token_endpoint", "token.example.test"),
        ("jwks_uri", "jwks.example.test"),
    ],
)
def test_adapter_rejects_private_token_or_jwks_endpoint(field: str, host: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(200, json=_metadata(), request=request)
        return httpx.Response(200, json={"id_token": "a.b.c"}, request=request)

    adapter = _adapter(
        handler,
        resolver=MemoryResolver({host: ("192.168.1.20",)}),
    )
    with pytest.raises(OidcRuntimeError) as captured:
        adapter.exchange_and_validate(
            provider=_provider(),
            code="code",
            redirect_uri="https://app.example.test/callback",
            code_verifier="verifier",
            nonce="nonce",
        )
    assert captured.value.code == "oidc_runtime_endpoint_unsafe"


def test_adapter_rejects_content_length_before_reading_and_disables_redirects() -> None:
    captured_clients: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Length": "20000"},
            content=b"{}",
            request=request,
        )

    adapter = _adapter(handler, captured=captured_clients)
    with pytest.raises(OidcRuntimeError) as captured:
        adapter.build_authorization_url(
            provider=_provider(),
            redirect_uri="https://app.example.test/callback",
            state="state",
            nonce="nonce",
            code_challenge="challenge",
        )
    assert captured.value.code == "oidc_runtime_response_too_large"
    assert captured_clients and all(call.get("follow_redirects") is False for call in captured_clients)


def test_adapter_enforces_streaming_cap_for_metadata_token_and_jwks() -> None:
    large_json = json.dumps({"padding": "x" * 20_000}).encode()

    def metadata_large(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=large_json, request=request)

    with pytest.raises(OidcRuntimeError) as metadata_error:
        _adapter(metadata_large).build_authorization_url(
            provider=_provider(),
            redirect_uri="https://app.example.test/callback",
            state="state",
            nonce="nonce",
            code_challenge="challenge",
        )
    assert metadata_error.value.code == "oidc_runtime_response_too_large"

    def token_large(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(200, json=_metadata(), request=request)
        return httpx.Response(200, content=large_json, request=request)

    with pytest.raises(OidcRuntimeError) as token_error:
        _adapter(token_large).exchange_and_validate(
            provider=_provider(),
            code="code",
            redirect_uri="https://app.example.test/callback",
            code_verifier="verifier",
            nonce="nonce",
        )
    assert token_error.value.code == "oidc_runtime_response_too_large"

    def jwks_large(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(200, json=_metadata(), request=request)
        if request.url.host == "token.example.test":
            return httpx.Response(200, json={"id_token": "a.b.c"}, request=request)
        return httpx.Response(200, content=large_json, request=request)

    with pytest.raises(OidcRuntimeError) as jwks_error:
        _adapter(jwks_large).exchange_and_validate(
            provider=_provider(),
            code="code",
            redirect_uri="https://app.example.test/callback",
            code_verifier="verifier",
            nonce="nonce",
        )
    assert jwks_error.value.code == "oidc_runtime_response_too_large"
