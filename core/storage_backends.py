"""Tenant-scoped storage backend registry repository and connectivity tests."""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from models.orm import Dataset, StorageBackend, Tenant

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


@dataclass(frozen=True)
class _FieldRequirement:
    """Config fields a provider cannot work without, plus their operator-facing messages.

    ``keys`` is plural because the historical contract accepted ``bucket_name`` as an
    alias of ``bucket``; dropping that alias would break already-stored configs.
    """

    keys: tuple[str, ...]
    message: str

    def satisfied(self, cfg: dict[str, Any]) -> bool:
        return any(str(cfg.get(key) or "").strip() for key in self.keys)


@dataclass(frozen=True)
class _FormField:
    name: str
    label: str
    required: bool
    secret: bool = False

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "label": self.label,
            "required": self.required,
        }
        if self.secret:
            payload["secret"] = True
        return payload


@dataclass(frozen=True)
class StorageProviderSpec:
    """Everything that differs between object-storage providers, in one declaration.

    ``probe`` is optional on purpose: only ``local`` can be verified for real without
    an SDK, and a provider must never claim a remote write succeeded.  Wiring a real
    client later means supplying a probe, not editing the shared paths.
    """

    name: str
    label: str
    fields: tuple[_FormField, ...]
    requirements: tuple[_FieldRequirement, ...]
    probe: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _probe_local(cfg: dict[str, Any]) -> dict[str, Any]:
    root = Path(str(cfg.get("root_path") or ""))
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".rag4c-storage-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return {"status": "ok", "detail": f"local root writable: {root}"}
    except OSError as exc:
        return {"status": "error", "detail": f"local root not writable: {exc}"}


_COMMON_REMOTE_FIELDS = (
    _FormField("endpoint", "Endpoint", False),
    _FormField("bucket", "Bucket", False),
    _FormField("region", "Region", False),
    _FormField("path_prefix", "Path prefix", False),
    _FormField("access_key_id", "Access key", False, secret=True),
    _FormField("secret_access_key", "Secret key", False, secret=True),
    _FormField("use_ssl", "Use SSL", False),
    _FormField("force_path_style", "Force path style", False),
)

#: 凭据类字段在远端 provider 上是必填的，``minio`` 还额外要求 endpoint。
_REMOTE_REQUIREMENTS = (
    _FieldRequirement(("bucket", "bucket_name"), "{p} requires bucket"),
    _FieldRequirement(("access_key_id",), "{p} requires access_key_id"),
    _FieldRequirement(("secret_access_key",), "{p} requires secret_access_key"),
)


def _remote_requirements(*, endpoint_required: bool = False) -> tuple[_FieldRequirement, ...]:
    prefix = (
        (_FieldRequirement(("endpoint",), "{p} requires endpoint"),)
        if endpoint_required
        else ()
    )
    return prefix + _REMOTE_REQUIREMENTS


_PROVIDER_SPECS: tuple[StorageProviderSpec, ...] = (
    StorageProviderSpec(
        name="local",
        label="本地目录",
        fields=(_FormField("root_path", "Root path", True),),
        requirements=(
            _FieldRequirement(("root_path",), "{p} provider requires root_path"),
        ),
        probe=_probe_local,
    ),
    StorageProviderSpec(
        name="minio",
        label="MinIO",
        fields=(
            _FormField("endpoint", "Endpoint", True),
            _FormField("bucket", "Bucket", True),
            _FormField("access_key_id", "Access key", True, secret=True),
            _FormField("secret_access_key", "Secret key", True, secret=True),
            _FormField("use_ssl", "Use SSL", False),
        ),
        requirements=_remote_requirements(endpoint_required=True),
    ),
    StorageProviderSpec(
        name="s3",
        label="S3",
        fields=(
            _FormField("endpoint", "Endpoint", False),
            _FormField("bucket", "Bucket", True),
            _FormField("region", "Region", False),
            _FormField("access_key_id", "Access key", True, secret=True),
            _FormField("secret_access_key", "Secret key", True, secret=True),
        ),
        requirements=_remote_requirements(),
    ),
    *[
        StorageProviderSpec(
            name=name,
            label=label,
            fields=_COMMON_REMOTE_FIELDS,
            requirements=_remote_requirements(),
        )
        for name, label in (
            ("cos", "COS"),
            ("oss", "OSS"),
            ("tos", "TOS"),
            ("obs", "OBS"),
        )
    ],
)

#: 注册顺序即控制台 provider 下拉顺序；不要在运行时改用 sorted(names())。
PROVIDERS: tuple[str, ...] = tuple(spec.name for spec in _PROVIDER_SPECS)
_PROVIDER_BY_NAME: dict[str, StorageProviderSpec] = {spec.name: spec for spec in _PROVIDER_SPECS}
_PROVIDER_ORDER: list[str] = list(PROVIDERS)


def registered_provider_specs() -> tuple[StorageProviderSpec, ...]:
    """Live provider list in registration order (built-ins first)."""
    return tuple(_PROVIDER_BY_NAME[name] for name in _PROVIDER_ORDER)


def register_provider_spec(spec: StorageProviderSpec, *, replace: bool = False) -> None:
    """Register an object-storage provider so validation, connectivity test and the
    console field schema all pick it up without editing this module's shared paths."""
    if spec.name in _PROVIDER_BY_NAME and not replace:
        raise StorageBackendInvalid(f"provider already registered: {spec.name}")
    if spec.name not in _PROVIDER_ORDER:
        _PROVIDER_ORDER.append(spec.name)
    _PROVIDER_BY_NAME[spec.name] = spec


def unregister_provider_spec(name: str) -> None:
    """Drop a provider registration (tests and teardown of optional integrations)."""
    key = (name or "").strip().lower()
    _PROVIDER_BY_NAME.pop(key, None)
    if key in _PROVIDER_ORDER:
        _PROVIDER_ORDER.remove(key)


def storage_provider_spec(provider: str) -> StorageProviderSpec:
    """Look up a provider spec; unknown names raise with the registered list."""
    key = (provider or "").strip().lower()
    spec = _PROVIDER_BY_NAME.get(key)
    if spec is None:
        raise StorageBackendInvalid(f"unsupported provider: {key}")
    return spec


def validate_provider_config(provider: str, config: dict[str, Any]) -> None:
    spec = storage_provider_spec(provider)
    cfg = dict(config or {})
    for requirement in spec.requirements:
        if not requirement.satisfied(cfg):
            raise StorageBackendInvalid(requirement.message.format(p=spec.name))


def test_storage_config(provider: str, config: dict[str, Any]) -> dict[str, Any]:
    """Validate config; providers with a probe get a real test, others are config-only."""
    try:
        spec = storage_provider_spec(provider)
        validate_provider_config(provider, config)
    except StorageBackendInvalid as exc:
        return {"status": "error", "detail": str(exc)}
    cfg = dict(config or {})
    if spec.probe is not None:
        return spec.probe(cfg)
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
        """Console schema, derived from the provider registry in declaration order."""
        return {
            "providers": [
                {
                    "provider": spec.name,
                    "label": spec.label,
                    "fields": [form_field.as_dict() for form_field in spec.fields],
                }
                for spec in registered_provider_specs()
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
