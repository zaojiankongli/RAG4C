"""The controlled source-preview endpoint: what it will and will not hand back.

Every test here is about a refusal boundary, because that is the whole content of the
feature: the bytes are a locally-resolved file the operator pointed at during ingest, and
the only thing standing between that and an arbitrary-file read is this endpoint's insistence
on tenant scope, configured roots, and a declared content type.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from config.settings import KnowledgeSecuritySettings, RunHistorySettings, TenantSettings
from core import catalog
from core.source_previews import SourcePreviewSpec, register_source_preview, unregister_source_preview
from models.orm import Account, Base, Dataset, Document, Tenant, TenantMember
from server import knowledge_source_preview_api as preview
from server.knowledge_auth import issue_knowledge_actor_token

PDF_BYTES = b"%PDF-1.4\n% guide\ntrailer\n%%EOF\n"
DOCX_BYTES = b"PK\x03\x04not-really-a-zip-but-bytes"


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        run_history=RunHistorySettings(ops_bearer_token=SecretStr("ops-secret")),
        knowledge_security=KnowledgeSecuritySettings(
            actor_signing_secret=SecretStr("preview-api-secret"), actor_max_ttl_s=900
        ),
        tenant=TenantSettings(enforced=True, default_tenant="tenant-a"),
    )


def _headers(settings: SimpleNamespace, actor: str, tenant: str = "tenant-a") -> dict[str, str]:
    token = issue_knowledge_actor_token(actor, tenant, 300, int(time.time()), settings=settings)
    return {"Authorization": f"Bearer {token}", "X-RAG4C-Tenant": tenant}


def _document(doc_id: str, tenant: str, dataset: str, name: str, file_path: str, file_hash: str, revision: int) -> Document:
    return Document(
        id=doc_id,
        tenant_id=tenant,
        dataset_id=dataset,
        name=name,
        status="completed",
        content_revision=revision,
        desired_index_revision=revision,
        file_path=file_path,
        file_hash=file_hash,
    )


@pytest.fixture()
def source_tree(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "kb originals"
    elsewhere = tmp_path / "elsewhere"
    root.mkdir()
    elsewhere.mkdir()
    (root / "guide.pdf").write_bytes(PDF_BYTES)
    (root / "plan.docx").write_bytes(DOCX_BYTES)
    (root / "payload.html").write_bytes(b"<script>alert(1)</script>")
    (root / "tiny.tiny").write_bytes(b"0123456789")
    (elsewhere / "stolen.pdf").write_bytes(b"%PDF-1.4 secret")
    return {"root": root, "elsewhere": elsewhere, "tmp": tmp_path}


@pytest.fixture()
def api(monkeypatch: pytest.MonkeyPatch, source_tree: dict[str, Path]):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    root = source_tree["root"]
    with Session(engine) as session:
        session.add_all([
            Tenant(id="tenant-a", name="A", status="active"),
            Tenant(id="tenant-b", name="B", status="active"),
            Dataset(id="dataset-a", tenant_id="tenant-a", name="A", status="active"),
            Dataset(id="dataset-b", tenant_id="tenant-b", name="B", status="active"),
            Account(id="owner-a", name="Owner", email="owner@example.test"),
            Account(id="member-a", name="Member", email="member@example.test"),
            Account(id="owner-b", name="Owner B", email="owner-b@example.test"),
            TenantMember(account_id="owner-a", tenant_id="tenant-a", role="owner"),
            TenantMember(account_id="member-a", tenant_id="tenant-a", role="member"),
            TenantMember(account_id="owner-b", tenant_id="tenant-b", role="owner"),
            _document("doc-pdf", "tenant-a", "dataset-a", "指南.pdf", str(root / "guide.pdf"),
                      "a" * 64, 3),
            _document("doc-docx", "tenant-a", "dataset-a", 'evil"; x=1\r\nSet-Cookie: a=b.docx',
                      str(root / "plan.docx"), "b" * 64, 1),
            _document("doc-html", "tenant-a", "dataset-a", "page", str(root / "payload.html"),
                      "c" * 64, 1),
            _document("doc-outside", "tenant-a", "dataset-a", "escaped",
                      str(source_tree["elsewhere"] / "stolen.pdf"), "d" * 64, 1),
            _document("doc-nofile", "tenant-a", "dataset-a", "gone", str(root / "missing.pdf"),
                      "e" * 64, 1),
            _document("doc-tiny", "tenant-a", "dataset-a", "small", str(root / "tiny.tiny"),
                      "f" * 64, 1),
            _document("doc-b-pdf", "tenant-b", "dataset-b", "other tenant",
                      str(root / "guide.pdf"), "a" * 64, 1),
        ])
        session.commit()
    monkeypatch.setattr(catalog, "get_engine", lambda: engine)
    monkeypatch.setattr(preview, "_configured_preview_roots", lambda: [str(root)])
    register_source_preview(
        SourcePreviewSpec(suffix=".tiny", kind="probe", media_type="text/plain",
                          inline_renderable=True, max_bytes=4)
    )
    app = FastAPI()
    app.state.knowledge_auth_engine = engine
    app.state.knowledge_auth_settings = _settings()
    app.include_router(preview.router)
    client = TestClient(app, client=("10.0.0.2", 50000))
    try:
        yield client, engine, app.state.knowledge_auth_settings, source_tree
    finally:
        unregister_source_preview(".tiny")


def _path(doc_id: str, dataset: str = "dataset-a") -> str:
    return f"/api/knowledge-bases/{dataset}/documents/{doc_id}/source-preview"


# --------------------------------------------------------------------------- #
# 授权与范围：这份端点只能看见"自己这一行"记下的那个路径
# --------------------------------------------------------------------------- #


def test_it_needs_a_bearer_and_conceals_anything_not_yours(api) -> None:
    client, _, settings, _ = api
    assert client.get(_path("doc-pdf")).status_code == 401
    # 同数据集里不是这份租户的文档：404 掩掉，不承认它存在。
    hidden = client.get(_path("doc-b-pdf", "dataset-a"), headers=_headers(settings, "owner-a"))
    assert hidden.status_code == 404
    assert hidden.json()["detail"]["code"] == "knowledge_resource_not_found"
    # 换一个你没有读权限的数据集：这是授权层先拒绝（403），和"这份不存在"是两件事。
    forbidden = client.get(_path("doc-pdf", "dataset-b"), headers=_headers(settings, "owner-a"))
    assert forbidden.status_code == 403
    # 同租户内的成员也有读权限：可查看不是管理员专属，也不该是。
    assert client.get(_path("doc-pdf"), headers=_headers(settings, "member-a")).status_code == 200


def test_openapi_declares_the_bearer_on_this_operation(api) -> None:
    client = api[0]
    operation = client.get("/openapi.json").json()["paths"][
        "/api/knowledge-bases/{dataset_id}/documents/{doc_id}/source-preview"
    ]["get"]
    assert operation["security"] == [{"KnowledgeBearerAuth": []}]


# --------------------------------------------------------------------------- #
# roots 只能来自配置；没配置就是关闭，而不是"什么都能读"
# --------------------------------------------------------------------------- #


def test_roots_come_from_settings_and_a_broken_config_means_off(monkeypatch) -> None:
    import config.settings as settings_module

    monkeypatch.setattr(
        settings_module,
        "get_settings",
        lambda: SimpleNamespace(catalog=SimpleNamespace(source_preview_roots=["/srv/kb"])),
    )
    assert preview._configured_preview_roots() == ["/srv/kb"]  # noqa: SLF001

    def explode() -> None:
        raise RuntimeError("no config file")

    monkeypatch.setattr(settings_module, "get_settings", explode)
    assert preview._configured_preview_roots() == []  # noqa: SLF001


def test_unconfigured_roots_report_disabled_rather_than_serving_bytes(
    monkeypatch, api
) -> None:
    client, _, settings, _ = api
    monkeypatch.setattr(preview, "_configured_preview_roots", lambda: [])
    response = client.get(_path("doc-pdf"), headers=_headers(settings, "owner-a"))
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "source_preview_disabled"


# --------------------------------------------------------------------------- #
# 声明表决定 content type 与 inline/disposition
# --------------------------------------------------------------------------- #


def test_a_pdf_comes_back_as_a_pdf_with_the_headers_that_matter(api) -> None:
    client, _, settings, _ = api
    response = client.get(_path("doc-pdf"), headers=_headers(settings, "owner-a"))
    assert response.status_code == 200
    assert response.content == PDF_BYTES
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "sandbox"
    assert response.headers["cache-control"] == "private, must-revalidate"
    assert response.headers["etag"] == '"3-aaaaaaaaaaaa"'
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("inline;")
    assert "filename*=UTF-8''%E6%8C%87%E5%8D%97.pdf" in disposition


def test_a_download_only_row_never_becomes_inline(api) -> None:
    client, _, settings, _ = api
    inline_ask = client.get(_path("doc-docx"), headers=_headers(settings, "owner-a"))
    assert inline_ask.status_code == 200
    assert inline_ask.headers["content-disposition"].startswith("attachment;")
    assert inline_ask.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def test_attachment_downgrades_an_inlineable_type_and_nothing_else(api) -> None:
    client, _, settings, _ = api
    asked = client.get(
        _path("doc-pdf") + "?disposition=attachment", headers=_headers(settings, "owner-a")
    )
    assert asked.status_code == 200
    assert asked.headers["content-disposition"].startswith("attachment;")
    junk = client.get(
        _path("doc-pdf") + "?disposition=inline%0D%0AX-Forged: 1",
        headers=_headers(settings, "owner-a"),
    )
    assert junk.status_code in {400, 422}


def test_a_filename_cannot_write_extra_response_headers(api) -> None:
    """文档名里带 CRLF 与引号：它必须被消毒成"只是一个文件名"，不能变成另一个头或另一个
    参数。RFC 5987 的扩展值只允许 pct-encoded / attr-char，所以冒号与空格必须被百分号编码。
    """
    import re

    client, _, settings, _ = api
    response = client.get(_path("doc-docx"), headers=_headers(settings, "owner-a"))
    disposition = response.headers["content-disposition"]
    assert "\r" not in disposition and "\n" not in disposition
    assert response.headers.get("set-cookie") is None
    ascii_form = disposition.split('filename="')[1].split('"')[0]
    # 引号与反斜杠才能提前终结 quoted-string；分号在引号内是无害字符。
    assert '"' not in ascii_form and "\\" not in ascii_form
    assert ascii_form == ascii_form.strip() and not any(c in ascii_form for c in "\r\n\x00")
    extended = re.search(r"filename\*=UTF-8''([^;]*)", disposition).group(1)  # type: ignore[union-attr]
    assert ":" not in extended and " " not in extended and '"' not in extended
    assert "%3A" in extended and "%20" in extended, "冒号/空格没被百分号编码就不是合法的 ext-value"
    assert extended.endswith(".docx")


def test_an_unlisted_suffix_is_refused_instead_of_being_sniffed(api) -> None:
    client, _, settings, _ = api
    response = client.get(_path("doc-html"), headers=_headers(settings, "owner-a"))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "source_preview_unsupported_kind"
    assert b"<script>" not in response.content


def test_a_path_outside_every_configured_root_is_refused(api) -> None:
    client, _, settings, _ = api
    response = client.get(_path("doc-outside"), headers=_headers(settings, "owner-a"))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "source_preview_out_of_scope"
    assert b"secret" not in response.content


def test_a_missing_file_says_so_and_is_not_reported_as_a_policy_refusal(api) -> None:
    client, _, settings, _ = api
    response = client.get(_path("doc-nofile"), headers=_headers(settings, "owner-a"))
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "source_preview_missing"


def test_the_declared_ceiling_is_enforced_without_truncating(api) -> None:
    client, _, settings, _ = api
    response = client.get(_path("doc-tiny"), headers=_headers(settings, "owner-a"))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "source_preview_too_large"
    assert b"0123456789" not in response.content, "超限必须整份不给，不是给前 4 个字节"


def test_the_etag_is_the_revision_and_hash_so_a_stale_view_is_detectable(api) -> None:
    client, engine, settings, tree = api
    first = client.get(_path("doc-pdf"), headers=_headers(settings, "owner-a")).headers["etag"]
    with Session(engine) as session:
        row = session.get(Document, "doc-pdf")
        row.content_revision = 4
        row.file_hash = "c" * 64
        session.commit()
    second = client.get(_path("doc-pdf"), headers=_headers(settings, "owner-a")).headers["etag"]
    assert first != second
    assert second == '"4-cccccccccccc"'
    assert tree  # keep the fixture dependency explicit


def test_a_file_swapped_between_the_size_check_and_the_read_is_not_served(
    api, monkeypatch
) -> None:
    client, _, settings, _ = api
    real = api[3]["root"] / "guide.pdf"
    original = Path.read_bytes

    def swapped(self: Path) -> bytes:
        # 绕过放行判定之后，文件被人换大了：返回的字节数必须和当初核过的尺寸一致。
        real.write_bytes(original(real) + b"x" * 500)
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", swapped)
    response = client.get(_path("doc-pdf"), headers=_headers(settings, "owner-a"))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "source_preview_unavailable"
    assert b"trailer" not in response.content
