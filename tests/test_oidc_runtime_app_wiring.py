from __future__ import annotations

from typing import Any

from server.app import app


def _route_paths(router: Any) -> set[str]:
    paths: set[str] = set()
    for route in router.routes:
        path = getattr(route, "path", None)
        if isinstance(path, str):
            paths.add(path)
        included = getattr(route, "original_router", None)
        if included is not None:
            paths.update(_route_paths(included))
    return paths


def test_main_app_mounts_oidc_start_callback_and_session_revoke_routes() -> None:
    paths = _route_paths(app)
    assert {
        "/api/enterprise/sso/oidc/start",
        "/api/enterprise/sso/oidc/callback",
        "/api/enterprise/sso/sessions/{session_id}/revoke",
    } <= paths
