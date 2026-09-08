from fastapi.testclient import TestClient

from server.app import app


def test_cors_vite_loopback_origins_are_exactly_allowlisted() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    headers = {"Access-Control-Request-Method": "GET"}

    for origin in ("http://localhost:1420", "http://127.0.0.1:1420"):
        response = client.options("/api/health", headers={"Origin": origin, **headers})
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin

    rejected = client.options(
        "/api/health",
        headers={"Origin": "http://localhost:1421", **headers},
    )
    assert rejected.status_code == 400
    assert "access-control-allow-origin" not in rejected.headers
