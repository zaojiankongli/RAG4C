"""Tenant-scoped storage backend registry repository and connectivity tests."""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from models.orm import Dataset, StorageBackend, Tenant

PROVIDERS = ("local", "minio", "s3", "cos", "oss", "tos", "obs")
SECRET_FIELDS = frozenset({"secret_access_key", "access_key_id"})
_MASKED_SECRET = "***"


class StorageBackendError(RuntimeError):
    pass


class StorageBackendNotFound(StorageBackendError):
    pass


class StorageBackendConflict(StorageBackendError):
    pass


class StorageBackendInvalid(StorageBackendError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _new_id() -> str:
    return f"sb-{uuid.uuid4().hex[:16]}"


def mask_config(config: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(config or {})
    ak = str(out.get("access_key_id") or "")
    if ak:
        out["access_key_id"] = f"ak_***{ak[-4:]}" if len(ak) > 4 else "ak_***"
    if out.get("secret_access_key"):
        out["secret_access_key"] = _MASKED_SECRET
    return out


def validate_provider_config(provider: str, config: dict[str, Any]) -> None:
    provider = (provider or "").strip().lower()
    if provider not in PROVIDERS:
        raise StorageBackendInvalid(f"unsupported provider: {provider}")
    cfg = dict(config or {})
    if provider == "local":
        root = str(cfg.get("root_path") or "").strip()
        if not root:
            raise StorageBackendInvalid("local provider requires root_path")
        return
    if provider == "minio":
        if not str(cfg.get("endpoint") or "").strip():
            raise StorageBackendInvalid("minio requires endpoint")
    if not str(cfg.get("bucket") or cfg.get("bucket_name") or "").strip():
        if provider != "local":
            raise StorageBackendInvalid(f"{provider} requires bucket")
    if provider != "local":
        if not str(cfg.get("access_key_id") or "").strip():
            raise StorageBackendInvalid(f"{provider} requires access_key_id")
        if not str(cfg.get("secret_access_key") or "").strip():
            raise StorageBackendInvalid(f"{provider} requires secret_access_key")


def test_storage_config(provider: str, config: dict[str, Any]) -> dict[str, Any]:
    """Validate config; local does a real write probe; remotes are config-only when SDK absent."""
    try:
        validate_provider_config(provider, config)
    except StorageBackendInvalid as exc:
        return {"status": "error", "detail": str(exc)}
    provider = provider.lower()
    cfg = dict(config or {})
    if provider == "local":
        root = Path(str(cfg.get("root_path") or ""))
        try:
            root.mkdir(parents=True, exist_ok=True)
            probe = root / ".rag4c-storage-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return {"status": "ok", "detail": f"local root writable: {root}"}
        except OSError as exc:
            return {"status": "error", "detail": f"local root not writable: {exc}"}
    endpoint = str(cfg.get("endpoint") or "").strip()
    detail_parts = ["config fields valid"]
    if endpoint:
        parsed = urlparse(endpoint if "://" in endpoint else f"https://{endpoint}")
        host = parsed.hostname or ""
        if host and not re.match(r"^[A-Za-z0-9.\-]+$", host):
            return {"status": "error", "detail": f"invalid endpoint host: {host}"}
        detail_parts.append(f"endpoint={endpoint}")
    detail_parts.append("no object-store SDK wired; not claiming remote write success")
    return {"status": "validated_config_only", "detail": "; ".join(detail_parts)}


def payload(row: StorageBackend, *, is_default: bool = False) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "name": row.name,
        "provider": row.provider,
        "status": row.status,
        "source": row.source,
        "is_default": is_default,
        "config_masked": mask_config(row.config_json),
        "created_by": row.created_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class StorageBackendRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    def _tenant(self, session: Session, tenant_id: str) -> Tenant:
        row = session.scalar(select(Tenant).where(Tenant.id == tenant_id))
        if row is None:
            raise StorageBackendNotFound("tenant does not exist")
        return row

    def _live(
        self, session: Session, tenant_id: str, backend_id: str
    ) -> StorageBackend:
        row = session.scalar(
            select(StorageBackend).where(
                StorageBackend.tenant_id == tenant_id,
                StorageBackend.id == backend_id,
                StorageBackend.is_deleted == 0,
            )
        )
        if row is None:
            raise StorageBackendNotFound("storage backend does not exist in tenant scope")
        return row

    def list_types(self) -> dict[str, Any]:
        fields_common = [
            {"name": "endpoint", "label": "Endpoint", "required": False},
            {"name": "bucket", "label": "Bucket", "required": False},
            {"name": "region", "label": "Region", "required": False},
            {"name": "path_prefix", "label": "Path prefix", "required": False},
            {
                "name": "access_key_id",
                "label": "Access key",
                "required": False,
                "secret": True,
            },
            {
                "name": "secret_access_key",
                "label": "Secret key",
                "required": False,
                "secret": True,
            },
            {"name": "use_ssl", "label": "Use SSL", "required": False},
            {"name": "force_path_style", "label": "Force path style", "required": False},
        ]
        return {
            "providers": [
                {
                    "provider": "local",
                    "label": "本地目录",
                    "fields": [
                        {"name": "root_path", "label": "Root path", "required": True}
                    ],
                },
                {
                    "provider": "minio",
                    "label": "MinIO",
                    "fields": [
                        {"name": "endpoint", "label": "Endpoint", "required": True},
                        {"name": "bucket", "label": "Bucket", "required": True},
                        {
                            "name": "access_key_id",
                            "label": "Access key",
                            "required": True,
                            "secret": True,
                        },
                        {
                            "name": "secret_access_key",
                            "label": "Secret key",
                            "required": True,
                            "secret": True,
                        },
                        {"name": "use_ssl", "label": "Use SSL", "required": False},
                    ],
                },
                {
                    "provider": "s3",
                    "label": "S3",
                    "fields": [
                        {"name": "endpoint", "label": "Endpoint", "required": False},
                        {"name": "bucket", "label": "Bucket", "required": True},
                        {"name": "region", "label": "Region", "required": False},
                        {
                            "name": "access_key_id",
                            "label": "Access key",
                            "required": True,
                            "secret": True,
                        },
                        {
                            "name": "secret_access_key",
                            "label": "Secret key",
                            "required": True,
                            "secret": True,
                        },
                    ],
                },
                {"provider": "cos", "label": "COS", "fields": fields_common},
                {"provider": "oss", "label": "OSS", "fields": fields_common},
                {"provider": "tos", "label": "TOS", "fields": fields_common},
                {"provider": "obs", "label": "OBS", "fields": fields_common},
            ]
        }

    def list_backends(self, tenant_id: str) -> dict[str, Any]:
        with Session(self.engine, expire_on_commit=False) as session:
            tenant = self._tenant(session, tenant_id)
            rows = list(
                session.scalars(
                    select(StorageBackend)
                    .where(
                        StorageBackend.tenant_id == tenant_id,
                        StorageBackend.is_deleted == 0,
                    )
                    .order_by(StorageBackend.created_at, StorageBackend.id)
                )
            )
            default_id = tenant.default_storage_backend_id
            return {
                "items": [
                    payload(row, is_default=row.id == default_id) for row in rows
                ],
                "default_storage_backend_id": default_id,
                "count": len(rows),
            }

    def get_backend(self, tenant_id: str, backend_id: str) -> dict[str, Any]:
        with Session(self.engine, expire_on_commit=False) as session:
            tenant = self._tenant(session, tenant_id)
            row = self._live(session, tenant_id, backend_id)
            return payload(row, is_default=row.id == tenant.default_storage_backend_id)

    def create_backend(
        self,
        tenant_id: str,
        *,
        name: str,
        provider: str,
        config: dict[str, Any],
        actor_id: str = "",
        source: str = "user",
    ) -> dict[str, Any]:
        name = (name or "").strip()
        if not name or len(name) > 128:
            raise StorageBackendInvalid("name must be 1-128 characters")
        provider = (provider or "").strip().lower()
        validate_provider_config(provider, config)
        with Session(self.engine, expire_on_commit=False) as session:
            self._tenant(session, tenant_id)
            existing = session.scalar(
                select(StorageBackend.id).where(
                    StorageBackend.tenant_id == tenant_id,
                    StorageBackend.name == name,
                    StorageBackend.is_deleted == 0,
                )
            )
            if existing is not None:
                raise StorageBackendConflict("storage backend name already exists")
            row = StorageBackend(
                id=_new_id(),
                tenant_id=tenant_id,
                name=name,
                provider=provider,
                config_json=dict(config or {}),
                status="active",
                source=source if source in {"user", "env"} else "user",
                is_deleted=0,
                created_by=(actor_id or "")[:64],
            )
            session.add(row)
            session.commit()
            return payload(row, is_default=False)

    def update_backend(
        self,
        tenant_id: str,
        backend_id: str,
        *,
        name: str | None = None,
        status: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with Session(self.engine, expire_on_commit=False) as session:
            tenant = self._tenant(session, tenant_id)
            row = self._live(session, tenant_id, backend_id)
            if name is not None:
                name = name.strip()
                if not name or len(name) > 128:
                    raise StorageBackendInvalid("name must be 1-128 characters")
                clash = session.scalar(
                    select(StorageBackend.id).where(
                        StorageBackend.tenant_id == tenant_id,
                        StorageBackend.name == name,
                        StorageBackend.is_deleted == 0,
                        StorageBackend.id != backend_id,
                    )
                )
                if clash is not None:
                    raise StorageBackendConflict("storage backend name already exists")
                row.name = name
            if status is not None:
                if status not in {"active", "disabled"}:
                    raise StorageBackendInvalid("status must be active or disabled")
                if (
                    status == "disabled"
                    and tenant.default_storage_backend_id == row.id
                ):
                    raise StorageBackendConflict(
                        "cannot disable the tenant default storage backend"
                    )
                row.status = status
            if config is not None:
                merged = dict(row.config_json or {})
                for key, value in config.items():
                    if value is None or value == "":
                        continue
                    if key in SECRET_FIELDS:
                        text = str(value)
                        if text == _MASKED_SECRET or text.startswith("ak_***"):
                            continue
                    merged[key] = value
                validate_provider_config(row.provider, merged)
                row.config_json = merged
            row.updated_at = _utc_now()
            session.commit()
            return payload(row, is_default=row.id == tenant.default_storage_backend_id)

    def delete_backend(self, tenant_id: str, backend_id: str) -> None:
        with Session(self.engine, expire_on_commit=False) as session:
            tenant = self._tenant(session, tenant_id)
            row = self._live(session, tenant_id, backend_id)
            if tenant.default_storage_backend_id == row.id:
                raise StorageBackendConflict(
                    "cannot delete the tenant default storage backend"
                )
            bound = session.scalar(
                select(Dataset.id).where(
                    Dataset.tenant_id == tenant_id,
                    Dataset.storage_backend_id == backend_id,
                )
            )
            if bound is not None:
                raise StorageBackendConflict(
                    "storage backend is bound to a dataset and cannot be deleted"
                )
            row.is_deleted = 1
            row.deleted_at = _utc_now()
            row.updated_at = row.deleted_at
            session.commit()

    def set_default(self, tenant_id: str, backend_id: str) -> dict[str, Any]:
        with Session(self.engine, expire_on_commit=False) as session:
            tenant = self._tenant(session, tenant_id)
            row = self._live(session, tenant_id, backend_id)
            if row.status != "active":
                raise StorageBackendConflict("only active backends can be default")
            tenant.default_storage_backend_id = row.id
            session.commit()
            return payload(row, is_default=True)

    def test_saved(self, tenant_id: str, backend_id: str) -> dict[str, Any]:
        with Session(self.engine, expire_on_commit=False) as session:
            self._tenant(session, tenant_id)
            row = self._live(session, tenant_id, backend_id)
            result = test_storage_config(row.provider, row.config_json)
            return {**result, "id": row.id, "name": row.name, "provider": row.provider}

    def bind_dataset(
        self,
        tenant_id: str,
        *,
        dataset_id: str,
        storage_backend_id: str | None,
    ) -> dict[str, Any]:
        with Session(self.engine, expire_on_commit=False) as session:
            self._tenant(session, tenant_id)
            dataset = session.scalar(
                select(Dataset).where(
                    Dataset.tenant_id == tenant_id,
                    Dataset.id == dataset_id,
                )
            )
            if dataset is None:
                raise StorageBackendNotFound("dataset does not exist in tenant scope")
            if storage_backend_id:
                self._live(session, tenant_id, storage_backend_id)
            doc_count = dataset.doc_count or 0
            if doc_count > 0 and dataset.storage_backend_id != storage_backend_id:
                raise StorageBackendConflict(
                    "dataset already has documents; storage backend binding is frozen"
                )
            dataset.storage_backend_id = storage_backend_id
            session.commit()
            return {
                "dataset_id": dataset.id,
                "storage_backend_id": dataset.storage_backend_id,
            }


def provider_type() -> str:
    return os.environ.get("RAG4C_STORAGE_PROVIDER_HINT", "local")
