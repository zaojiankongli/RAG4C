from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from server.middleware import http_exception_handler, validation_exception_handler


class SecretPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    count: int
    mapping: dict[str, int] = Field(default_factory=dict)


def _app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    @app.get("/structured")
    def structured() -> None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "knowledge_conflict",
                "message": "revision changed",
                "current_revision": 4,
            },
        )

    @app.get("/plain")
    def plain() -> None:
        raise HTTPException(status_code=418, detail="plain failure")

    @app.get("/items/{item_id}")
    def item(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    @app.post("/payload")
    def payload(body: SecretPayload) -> dict[str, int]:
        return {"count": body.count}

    @app.post("/root-values")
    def root_values(body: dict[str, int]) -> dict[str, int]:
        return body

    @app.post("/root-keys")
    def root_keys(body: dict[int, int]) -> dict[int, int]:
        return body

    return app


def test_http_exception_handler_preserves_structured_business_detail() -> None:
    response = TestClient(_app()).get("/structured")
    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "knowledge_conflict",
            "message": "revision changed",
            "current_revision": 4,
        }
    }


def test_http_exception_handler_keeps_generic_fallback_contract() -> None:
    client = TestClient(_app())
    assert client.get("/plain").json() == {
        "error": {"code": "http_418", "message": "plain failure"}
    }
    assert client.get("/missing").json() == {"error": {"code": "http_404", "message": "Not Found"}}


def test_validation_handler_keeps_validation_error_contract() -> None:
    response = TestClient(_app()).get("/items/not-an-integer")
    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "validation_error",
            "message": "path.item_id: invalid integer",
        }
    }


def test_validation_handler_never_echoes_submitted_input_or_context() -> None:
    secret = "SENTINEL-VALIDATION-SECRET"
    response = TestClient(_app()).post(
        "/payload",
        json={"count": secret, "unexpected": {"token": secret}},
    )
    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "validation_error",
            "message": ("body.count: invalid integer; body.<extra>: unexpected field"),
        }
    }
    serialized = response.text.casefold()
    assert secret.casefold() not in serialized
    assert "input" not in serialized
    assert "ctx" not in serialized


def test_validation_handler_sanitizes_user_controlled_mapping_keys() -> None:
    secret = "SENTINEL-MAPPING-KEY"
    response = TestClient(_app()).post(
        "/payload",
        json={"count": 1, "mapping": {secret: "not-an-integer"}},
    )
    assert response.status_code == 422
    assert response.json()["error"]["message"] == ("body.mapping.<key>: invalid integer")
    assert secret not in response.text


def test_validation_handler_sanitizes_root_mapping_keys_and_key_markers() -> None:
    secret = "SENTINEL-ROOT-MAPPING-KEY"
    value_error = TestClient(_app()).post(
        "/root-values",
        json={secret: "not-an-integer"},
    )
    assert value_error.status_code == 422
    assert value_error.json()["error"]["message"] == "body.<key>: invalid integer"
    assert secret not in value_error.text

    key_error = TestClient(_app()).post(
        "/root-keys",
        json={secret: 1},
    )
    assert key_error.status_code == 422
    assert key_error.json()["error"]["message"] == "body.<key>: invalid integer"
    assert secret not in key_error.text
    assert "[key]" not in key_error.text
