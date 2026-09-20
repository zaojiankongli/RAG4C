from __future__ import annotations

from pathlib import Path
import json

import pytest
from sqlalchemy import create_engine, text

from core import catalog_schema as manifest
from tests.test_enterprise_knowledge_serving_migration import REVISION, TABLES, sqlite_url
from tests.test_enterprise_knowledge_serving_migration import _upgrade as upgrade_0036

TIME = "2026-08-30T12:00:00.000000Z"


def _engine(tmp_path: Path, name: str):
    url = sqlite_url(tmp_path / name)
    upgrade_0036(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT OR IGNORE INTO dataset_workspace_ownerships "
                "(id,tenant_id,dataset_id,workspace_id,revision,created_at,created_by,updated_at,updated_by,last_transfer_at) "
                "VALUES ('ownership-a','tenant-a','dataset-a','workspace-a',1,CURRENT_TIMESTAMP,'owner-a',CURRENT_TIMESTAMP,'owner-a',NULL)"
            )
        )
    return engine


def _policy() -> dict[str, object]:
    return {
        "id": "policy-a",
        "tenant_id": "tenant-a",
        "profile_id": "profile-a",
        "revision": 1,
        "max_source_staleness_seconds": 3600,
        "max_parse_lag_seconds": 1800,
        "max_index_lag_seconds": 1800,
        "max_failed_document_count": 0,
        "max_pending_index_count": 0,
        "require_current_release": False,
        "require_passing_certification": False,
        "created_at": TIME,
        "created_by": "owner-a",
    }


def _seed_parent_scope(connection) -> None:
    connection.execute(
        text(
            "INSERT OR IGNORE INTO accounts (id,name,email,created_at) VALUES ('owner-a','Owner','owner@example.com',CURRENT_TIMESTAMP)"
        )
    )
    connection.execute(
        text(
            "INSERT OR IGNORE INTO tenants (id,name,plan,status,quota_documents,quota_chunks,doc_count,chunk_count,created_at) VALUES ('tenant-a','Tenant A','enterprise','active',100,1000,0,0,CURRENT_TIMESTAMP)"
        )
    )
    connection.execute(
        text(
            "INSERT OR IGNORE INTO tenant_members (id,account_id,tenant_id,role,status,revision,created_at,updated_at,updated_by) VALUES (1,'owner-a','tenant-a','owner','active',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'owner-a')"
        )
    )
    connection.execute(
        text(
            "INSERT OR IGNORE INTO tenant_workspaces (id,tenant_id,code,name,normalized_name,description,status,environment,is_default,active_default_slot,revision,created_at,created_by,updated_at,updated_by) VALUES ('workspace-a','tenant-a','default','Default','default','','active','production',1,'default',1,CURRENT_TIMESTAMP,'owner-a',CURRENT_TIMESTAMP,'owner-a')"
        )
    )
    connection.execute(
        text(
            "INSERT OR IGNORE INTO datasets (id,tenant_id,name,description,status,profile_revision,owner_id,visibility,profile_json,parser_policy,chunk_policy,retrieval_policy,retention_policy,metadata_policy,default_language,graph_enabled,qa_enabled,mutation_generation,serving_generation,release_revision,acl_mode,acl_revision,doc_count,chunk_count,created_at,updated_at) VALUES ('dataset-a','tenant-a','Dataset A','', 'active',1,'owner-a','private','{}','{}','{}','{}','{}','{}','zh-CN',0,1,0,0,1,'tenant_role',1,0,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
    )
    connection.execute(
        text(
            "INSERT OR IGNORE INTO dataset_workspace_ownerships "
            "(id,tenant_id,dataset_id,workspace_id,revision,created_at,created_by,updated_at,updated_by,last_transfer_at) "
            "VALUES ('ownership-a','tenant-a','dataset-a','workspace-a',1,CURRENT_TIMESTAMP,'owner-a',CURRENT_TIMESTAMP,'owner-a',NULL)"
        )
    )


def _canonical_snapshot_for_test(
    canonical_snapshot,
    value: dict[str, object],
    *,
    stage_facts: list[dict[str, object]],
    evidence_links: list[dict[str, object]],
) -> dict[str, object]:
    """Keep the seed ready for the Stage26 digest contract transition."""

    return canonical_snapshot(
        value,
        stage_facts=stage_facts,
        evidence_links=evidence_links,
    )


def _seed_valid_graph(
    engine,
    *,
    current_release_id: str | None = None,
    current_certification_id: str | None = None,
    include_evidence: bool = True,
    evidence_overrides: dict[str, tuple[str, str, str]] | None = None,
) -> None:
    from core.enterprise_knowledge_serving import (
        canonical_knowledge_serving_evidence_link,
        canonical_knowledge_serving_event,
        canonical_knowledge_serving_policy,
        canonical_knowledge_serving_snapshot,
        canonical_knowledge_serving_stage_fact,
    )

    policy = canonical_knowledge_serving_policy(_policy())
    stage_rows = [
        canonical_knowledge_serving_stage_fact(
            {
                "id": f"stage-{code}",
                "tenant_id": "tenant-a",
                "profile_id": "profile-a",
                "snapshot_id": "snapshot-a",
                "stage_code": code,
                "sequence": sequence,
                "state": "ready",
                "item_count": 1,
                "ready_count": 1,
                "warning_count": 0,
                "pending_count": 0,
                "error_count": 0,
                "lag_seconds": 0,
                "expected_revision": 1,
                "observed_revision": 1,
                "expected_digest": "a" * 64,
                "observed_digest": "a" * 64,
                "safe_error_code": None,
                "safe_error": None,
                "observed_at": TIME,
            }
        )
        for sequence, code in enumerate(("source", "parse", "chunk", "index", "serve"), 1)
    ]
    def _evidence_spec(stage_code: str) -> tuple[str, str, str]:
        default = (
            "source",
            f"resource-{stage_code}",
            "knowledge_sources",
        ) if stage_code == "source" else (
            "document",
            f"resource-{stage_code}",
            "knowledge_documents",
        )
        return (evidence_overrides or {}).get(stage_code, default)

    evidence_rows = []
    if include_evidence:
        for row in stage_rows:
            evidence_kind, resource_id, route_code = _evidence_spec(str(row["stage_code"]))
            evidence_rows.append(
                canonical_knowledge_serving_evidence_link(
                    {
                        "id": f"evidence-{row['stage_code']}",
                        "tenant_id": "tenant-a",
                        "profile_id": "profile-a",
                        "snapshot_id": "snapshot-a",
                        "stage_fact_id": f"stage-{row['stage_code']}",
                        "evidence_kind": evidence_kind,
                        "resource_id": resource_id,
                        "resource_revision": 1,
                        "resource_digest": "a" * 64,
                        "route_code": route_code,
                        "safe_label": f"{row['stage_code']} evidence",
                        "created_at": TIME,
                    }
                )
            )
    snapshot = _canonical_snapshot_for_test(
        canonical_knowledge_serving_snapshot,
        {
            "id": "snapshot-a",
            "tenant_id": "tenant-a",
            "profile_id": "profile-a",
            "policy_revision_id": "policy-a",
            "observation_key": "observation-a",
            "source_count": 1,
            "ready_source_count": 1,
            "stale_source_count": 0,
            "active_document_count": 1,
            "failed_document_count": 0,
            "pending_index_count": 0,
            "expected_serving_generation": 1,
            "observed_serving_generation": 1,
            "current_release_id": current_release_id,
            "current_certification_id": current_certification_id,
            "as_of": TIME,
            "created_at": TIME,
            "created_by": "owner-a",
        },
        stage_facts=stage_rows,
        evidence_links=evidence_rows,
    )
    event_rows = []
    previous = None
    for sequence, event_type in enumerate(
        ("profile_created", "policy_revision_created", "snapshot_recorded"), 1
    ):
        event = canonical_knowledge_serving_event(
            {
                "id": f"event-{sequence}",
                "tenant_id": "tenant-a",
                "profile_id": "profile-a",
                "snapshot_id": "snapshot-a",
                "stream_key": "profile:profile-a",
                "sequence": sequence,
                "event_type": event_type,
                "previous_event_digest": previous,
                "actor_id": "owner-a",
                "request_id": f"request-{sequence}",
                "safe_snapshot_json": {"kind": event_type},
                "occurred_at": TIME,
            }
        )
        event_rows.append(event)
        previous = event["event_digest"]

    with engine.begin() as connection:
        _seed_parent_scope(connection)
        connection.execute(
            text(
                "INSERT INTO tenant_knowledge_serving_profiles (id,tenant_id,workspace_id,dataset_id,name,normalized_name,status,active_profile_key,revision,current_policy_revision_id,current_snapshot_id,created_at,created_by,updated_at,updated_by,archived_at,archived_by) VALUES ('profile-a','tenant-a','workspace-a','dataset-a','Serving','serving','draft',NULL,1,NULL,NULL,:created_at,'owner-a',:updated_at,'owner-a',NULL,NULL)"
            ),
            {"created_at": TIME, "updated_at": TIME},
        )
        connection.execute(
            text(
                "INSERT INTO tenant_knowledge_serving_policy_revisions (id,tenant_id,profile_id,revision,max_source_staleness_seconds,max_parse_lag_seconds,max_index_lag_seconds,max_failed_document_count,max_pending_index_count,require_current_release,require_passing_certification,policy_digest,created_at,created_by) VALUES ('policy-a','tenant-a','profile-a',:revision,:source,:parse,:index,:failed,:pending,:release,:cert,:digest,:created_at,'owner-a')"
            ),
            {
                "revision": policy["revision"],
                "source": policy["max_source_staleness_seconds"],
                "parse": policy["max_parse_lag_seconds"],
                "index": policy["max_index_lag_seconds"],
                "failed": policy["max_failed_document_count"],
                "pending": policy["max_pending_index_count"],
                "release": policy["require_current_release"],
                "cert": policy["require_passing_certification"],
                "digest": policy["policy_digest"],
                "created_at": policy["created_at"],
            },
        )
        connection.execute(
            text(
                "INSERT INTO tenant_knowledge_serving_snapshots (id,tenant_id,profile_id,policy_revision_id,observation_key,state,source_count,ready_source_count,stale_source_count,active_document_count,failed_document_count,pending_index_count,expected_serving_generation,observed_serving_generation,current_release_id,current_certification_id,stage_count,ready_stage_count,blocked_stage_count,snapshot_digest,as_of,created_at,created_by) VALUES ('snapshot-a','tenant-a','profile-a','policy-a','observation-a',:state,1,1,0,1,0,0,1,1,:current_release_id,:current_certification_id,:stage_count,:ready_count,:blocked_count,:digest,:as_of,:created_at,'owner-a')"
            ),
            {
                "state": snapshot["state"],
                "stage_count": snapshot["stage_count"],
                "ready_count": snapshot["ready_stage_count"],
                "blocked_count": snapshot["blocked_stage_count"],
                "current_release_id": snapshot["current_release_id"],
                "current_certification_id": snapshot["current_certification_id"],
                "digest": snapshot["snapshot_digest"],
                "as_of": snapshot["as_of"],
                "created_at": snapshot["created_at"],
            },
        )
        for row in stage_rows:
            connection.execute(
                text(
                    "INSERT INTO tenant_knowledge_serving_stage_facts (id,tenant_id,profile_id,snapshot_id,stage_code,sequence,state,item_count,ready_count,warning_count,pending_count,error_count,lag_seconds,expected_revision,observed_revision,expected_digest,observed_digest,safe_error_code,safe_error,stage_digest,observed_at) VALUES (:id,'tenant-a','profile-a','snapshot-a',:stage_code,:sequence,:state,:item_count,:ready_count,:warning_count,:pending_count,:error_count,:lag_seconds,:expected_revision,:observed_revision,:expected_digest,:observed_digest,NULL,NULL,:stage_digest,:observed_at)"
                ),
                {
                    "id": f"stage-{row['stage_code']}",
                    **{
                        key: row[key]
                        for key in (
                            "stage_code",
                            "sequence",
                            "state",
                            "item_count",
                            "ready_count",
                            "warning_count",
                            "pending_count",
                            "error_count",
                            "lag_seconds",
                            "expected_revision",
                            "observed_revision",
                            "expected_digest",
                            "observed_digest",
                            "stage_digest",
                            "observed_at",
                        )
                    },
                },
            )
        for row in evidence_rows:
            connection.execute(
                text(
                    "INSERT INTO tenant_knowledge_serving_evidence_links (id,tenant_id,profile_id,snapshot_id,stage_fact_id,evidence_kind,resource_id,resource_revision,resource_digest,route_code,safe_label,evidence_digest,created_at) VALUES (:id,'tenant-a','profile-a','snapshot-a',:stage_fact_id,:evidence_kind,:resource_id,:resource_revision,:resource_digest,:route_code,:safe_label,:evidence_digest,:created_at)"
                ),
                row,
            )
        for row in event_rows:
            connection.execute(
                text(
                    "INSERT INTO tenant_knowledge_serving_events (id,tenant_id,profile_id,snapshot_id,stream_key,sequence,event_type,previous_event_digest,event_digest,actor_id,request_id,safe_snapshot_json,occurred_at) VALUES (:id,'tenant-a','profile-a','snapshot-a',:stream_key,:sequence,:event_type,:previous_event_digest,:event_digest,'owner-a',:request_id,:safe_snapshot_json,:occurred_at)"
                ),
                {
                    **row,
                    "safe_snapshot_json": json.dumps(
                        row["safe_snapshot_json"], ensure_ascii=False, separators=(",", ":")
                    ),
                },
            )
        connection.execute(
            text(
                "UPDATE tenant_knowledge_serving_profiles SET status='active',active_profile_key='dataset-a',current_policy_revision_id='policy-a',current_snapshot_id='snapshot-a' WHERE id='profile-a'"
            )
        )


def _replace_event_stream(engine, *, stream_key: str | None) -> None:
    from core.enterprise_knowledge_serving import canonical_knowledge_serving_event

    with engine.connect() as connection:
        connection.exec_driver_sql("DROP TRIGGER trg_tenant_knowledge_serving_events_no_delete")
        connection.execute(text("DELETE FROM tenant_knowledge_serving_events"))
        if stream_key is not None:
            previous = None
            for sequence, event_type in enumerate(
                ("profile_created", "policy_revision_created", "snapshot_recorded"), 1
            ):
                event = canonical_knowledge_serving_event(
                    {
                        "id": f"event-rogue-{sequence}",
                        "tenant_id": "tenant-a",
                        "profile_id": "profile-a",
                        "snapshot_id": "snapshot-a",
                        "stream_key": stream_key,
                        "sequence": sequence,
                        "event_type": event_type,
                        "previous_event_digest": previous,
                        "actor_id": "owner-a",
                        "request_id": f"request-rogue-{sequence}",
                        "safe_snapshot_json": {"kind": event_type},
                        "occurred_at": TIME,
                    }
                )
                connection.execute(
                    text(
                        "INSERT INTO tenant_knowledge_serving_events "
                        "(id,tenant_id,profile_id,snapshot_id,stream_key,sequence,event_type," 
                        "previous_event_digest,event_digest,actor_id,request_id,safe_snapshot_json,occurred_at) "
                        "VALUES (:id,:tenant_id,:profile_id,:snapshot_id,:stream_key,:sequence,:event_type," 
                        ":previous_event_digest,:event_digest,:actor_id,:request_id,:safe_snapshot_json,:occurred_at)"
                    ),
                    {
                        **event,
                        "safe_snapshot_json": json.dumps(
                            event["safe_snapshot_json"], ensure_ascii=False, separators=(",", ":")
                        ),
                    },
                )
                previous = event["event_digest"]
        connection.commit()


def _insert_event_stream(engine, *, stream_key: str) -> None:
    from core.enterprise_knowledge_serving import canonical_knowledge_serving_event

    event = canonical_knowledge_serving_event(
        {
            "id": "event-rogue-1",
            "tenant_id": "tenant-a",
            "profile_id": "profile-a",
            "snapshot_id": "snapshot-a",
            "stream_key": stream_key,
            "sequence": 1,
            "event_type": "profile_created",
            "previous_event_digest": None,
            "actor_id": "owner-a",
            "request_id": "request-rogue-1",
            "safe_snapshot_json": {"kind": "profile_created"},
            "occurred_at": TIME,
        }
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenant_knowledge_serving_events "
                "(id,tenant_id,profile_id,snapshot_id,stream_key,sequence,event_type," 
                "previous_event_digest,event_digest,actor_id,request_id,safe_snapshot_json,occurred_at) "
                "VALUES (:id,:tenant_id,:profile_id,:snapshot_id,:stream_key,:sequence,:event_type," 
                ":previous_event_digest,:event_digest,:actor_id,:request_id,:safe_snapshot_json,:occurred_at)"
            ),
            {
                **event,
                "safe_snapshot_json": json.dumps(
                    event["safe_snapshot_json"], ensure_ascii=False, separators=(",", ":")
                ),
            },
        )


def _seed_external_serving_resource(connection, evidence_kind: str) -> None:
    connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    if evidence_kind == "release":
        connection.execute(
            text(
                "INSERT INTO dataset_release_manifests "
                "(id,tenant_id,dataset_id,release_number,profile_revision,ownership_revision," 
                "workspace_id,workspace_revision,mutation_generation,serving_generation,schema_version," 
                "policy_digest,manifest_digest,readiness_digest,readiness_state,entry_count,blocker_count," 
                "readiness_blockers_json,created_at,created_by,reason,request_id) VALUES "
                "('release-other','tenant-a','dataset-other',1,1,1,'workspace-a',1,0,0,1," 
                ":digest,:digest,:digest,'ready',0,0,'[]',CURRENT_TIMESTAMP,'owner-a','test','request-release')"
            ),
            {"digest": "b" * 64},
        )
        return
    if evidence_kind == "certification":
        connection.execute(
            text(
                "INSERT INTO dataset_release_quality_certifications "
                "(id,tenant_id,dataset_id,release_id,baseline_id,policy_id,policy_revision," 
                "release_manifest_digest,release_mutation_generation,release_serving_generation,status," 
                "experiment_count,completed_experiment_count,degraded_experiment_count,query_count," 
                "judged_result_count,total_result_count,judgment_count,judgment_coverage_bps," 
                "multi_judged_results,unanimous_results,conflicting_results,exact_agreement_bps," 
                "mean_score_milli,failed_rule_count,policy_snapshot_json,summary_json,evidence_digest," 
                "certification_digest,valid_until,created_at,created_by,reason,request_id) VALUES "
                "('cert-other','tenant-a','dataset-other','release-other','baseline-other','policy-other',1," 
                ":digest,0,0,'passed',1,1,0,1,1,1,1,10000,0,0,0,10000,3000,0,'{}','{}'," 
                ":digest,:digest,'2099-01-01T00:00:00.000000','2026-08-30T12:00:00.000000'," 
                "'owner-a','test','request-cert')"
            ),
            {"digest": "c" * 64},
        )
        return
    raise AssertionError(evidence_kind)


def test_stage26_manifest_and_capability_are_exposed() -> None:
    assert manifest.ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REVISION == REVISION
    assert manifest.ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REQUIRED_TABLES == TABLES
    assert TABLES <= manifest.HEAD_CATALOG_TABLES
    for table in TABLES:
        assert table in manifest.ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REQUIRED_COLUMNS
        assert table in manifest.ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REQUIRED_NOT_NULL
        assert table in manifest.ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REQUIRED_UNIQUES
        assert table in manifest.ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REQUIRED_FOREIGN_KEYS
        assert table in manifest.ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REQUIRED_CHECK_FRAGMENTS
        assert table in manifest.ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REQUIRED_INDEXES


def test_stage26_empty_capability_is_ready_and_stage25_remains_ready(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "stage26-empty.db")
    try:
        # 本用例断言的是"在 head 上"的 current 语义，所以升到 head 而不是停在 0036。
        # 用 manifest.upgrade_catalog 而不是再写一个字面量：R2 的冻结 head 漂移教训。
        # 共享 seed 夹具给时代库补过 0038 的列；这个库接下来还要升到 head，
        # 而 0038 的 add_column 没有存在性守卫 -> 先还原成真正的时代形态再迁移。
        from tests.head_catalog import drop_era_columns

        drop_era_columns(engine)
        manifest.upgrade_catalog(str(engine.url))
        assert manifest.inspect_enterprise_knowledge_serving_reliability_capability(engine) == (
            "ready",
            (),
        )
        assert manifest.inspect_enterprise_automation_workflows_capability(engine) == ("ready", ())
        state = manifest.inspect_catalog_schema(engine)
        assert state.revision == manifest.HEAD_REVISION and state.status == "current"
    finally:
        engine.dispose()


def test_stage26_capability_accepts_canonical_five_stage_graph(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "stage26-valid.db")
    try:
        _seed_valid_graph(engine)
        assert manifest.inspect_enterprise_knowledge_serving_reliability_capability(engine) == (
            "ready",
            (),
        )
    finally:
        engine.dispose()


@pytest.mark.parametrize("event_setup", ("missing", "noncanonical", "additional"))
def test_stage26_readiness_requires_one_canonical_profile_event_stream(
    event_setup: str,
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path, f"stage26-event-stream-{event_setup}.db")
    try:
        _seed_valid_graph(engine)
        if event_setup == "missing":
            _replace_event_stream(engine, stream_key=None)
        elif event_setup == "noncanonical":
            _replace_event_stream(engine, stream_key="rogue-stream")
        else:
            _insert_event_stream(engine, stream_key="rogue-stream")

        state, issues = manifest.inspect_enterprise_knowledge_serving_reliability_capability(
            engine
        )
        assert state == "unavailable"
        assert any("canonical Knowledge Serving event stream" in issue for issue in issues), issues
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "evidence_kind,route_code,resource_id",
    (
        ("release", "knowledge_base_releases", "release-other"),
        ("certification", "release_quality", "cert-other"),
    ),
)
def test_stage26_readiness_rejects_evidence_resource_from_another_dataset(
    evidence_kind: str,
    route_code: str,
    resource_id: str,
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path, f"stage26-evidence-cross-dataset-{evidence_kind}.db")
    try:
        with engine.begin() as connection:
            _seed_external_serving_resource(connection, evidence_kind)
        _seed_valid_graph(
            engine,
            evidence_overrides={"source": (evidence_kind, resource_id, route_code)},
        )

        state, issues = manifest.inspect_enterprise_knowledge_serving_reliability_capability(
            engine
        )
        assert state == "unavailable"
        assert any(
            f"evidence {evidence_kind} resource dataset ownership mismatch" in issue
            for issue in issues
        ), issues
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "evidence_kind,route_code,resource_id",
    (
        ("release", "knowledge_base_releases", "release-missing"),
        ("certification", "release_quality", "cert-missing"),
    ),
)
def test_stage26_readiness_rejects_missing_evidence_resource(
    evidence_kind: str,
    route_code: str,
    resource_id: str,
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path, f"stage26-evidence-missing-{evidence_kind}.db")
    try:
        _seed_valid_graph(
            engine,
            evidence_overrides={"source": (evidence_kind, resource_id, route_code)},
        )

        state, issues = manifest.inspect_enterprise_knowledge_serving_reliability_capability(
            engine
        )
        assert state == "unavailable"
        assert any(
            f"orphan Knowledge Serving evidence {evidence_kind} resource" in issue
            for issue in issues
        ), issues
    finally:
        engine.dispose()


def test_stage26_readiness_rejects_digest_count_and_event_chain_drift(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "stage26-drift.db")
    try:
        _seed_valid_graph(engine)
        with engine.begin() as connection:
            connection.execute(
                text("DROP TRIGGER trg_tenant_knowledge_serving_stage_facts_no_update")
            )
            connection.execute(
                text(
                    "UPDATE tenant_knowledge_serving_stage_facts SET stage_digest=:digest WHERE id='stage-source'"
                ),
                {"digest": "f" * 64},
            )
            connection.execute(
                text("DROP TRIGGER trg_tenant_knowledge_serving_events_validate_insert")
            )
            connection.execute(text("DROP TRIGGER trg_tenant_knowledge_serving_events_no_update"))
            connection.execute(
                text(
                    "UPDATE tenant_knowledge_serving_events SET previous_event_digest=:previous WHERE id='event-3'"
                ),
                {"previous": "e" * 64},
            )
        state, issues = manifest.inspect_enterprise_knowledge_serving_reliability_capability(engine)
        assert state == "unavailable"
        assert any("digest" in issue.lower() for issue in issues)
        assert any("chain" in issue.lower() or "predecessor" in issue.lower() for issue in issues)
    finally:
        engine.dispose()


def test_stage26_unknown_dialect_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from core import catalog_schema as api

    class UnknownDialect:
        name = "oracle"

    class UnknownBind:
        dialect = UnknownDialect()

    state, issues = api.inspect_enterprise_knowledge_serving_reliability_capability(UnknownBind())
    assert state == "unavailable"
    assert any("dialect" in issue.lower() for issue in issues)


def test_stage26_readiness_passes_canonicalized_facts_and_evidence_to_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path, "stage26-snapshot-evidence-contract.db")
    try:
        _seed_valid_graph(engine)
        import core.enterprise_knowledge_serving as serving

        original = serving.canonical_knowledge_serving_snapshot
        calls: list[tuple[object, object]] = []

        def spy(value, *, stage_facts, evidence_links):
            calls.append((stage_facts, evidence_links))
            return original(
                value, stage_facts=stage_facts, evidence_links=evidence_links
            )

        monkeypatch.setattr(serving, "canonical_knowledge_serving_snapshot", spy)
        state, issues = manifest.inspect_enterprise_knowledge_serving_reliability_capability(
            engine
        )
        assert state == "ready", issues
        assert calls
        assert all(isinstance(facts, list) and len(facts) == 5 for facts, _ in calls)
        assert all(isinstance(evidence, list) for _, evidence in calls)
        assert all(evidence is not None for _, evidence in calls)
    finally:
        engine.dispose()


def test_stage26_readiness_allows_empty_evidence_list_but_never_none(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path, "stage26-empty-evidence-contract.db")
    try:
        _seed_valid_graph(engine, include_evidence=False)
        import core.enterprise_knowledge_serving as serving

        original = serving.canonical_knowledge_serving_snapshot
        calls: list[tuple[object, object]] = []

        def spy(value, *, stage_facts, evidence_links):
            calls.append((stage_facts, evidence_links))
            return original(
                value, stage_facts=stage_facts, evidence_links=evidence_links
            )

        monkeypatch.setattr(serving, "canonical_knowledge_serving_snapshot", spy)
        state, issues = manifest.inspect_enterprise_knowledge_serving_reliability_capability(
            engine
        )
        assert state == "ready", issues
        assert calls
        assert any(evidence == [] for _, evidence in calls)
        assert all(evidence is not None for _, evidence in calls)
    finally:
        engine.dispose()


def test_stage26_readiness_fails_closed_without_five_stage_facts_and_does_not_pass_none(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path, "stage26-incomplete-facts-contract.db")
    try:
        _seed_valid_graph(engine)
        with engine.begin() as connection:
            connection.execute(
                text("DROP TRIGGER trg_tenant_knowledge_serving_evidence_links_no_delete")
            )
            connection.execute(text("DROP TRIGGER trg_tenant_knowledge_serving_stage_facts_no_delete"))
            connection.execute(
                text(
                    "DELETE FROM tenant_knowledge_serving_evidence_links "
                    "WHERE stage_fact_id='stage-serve'"
                )
            )
            connection.execute(
                text("DELETE FROM tenant_knowledge_serving_stage_facts WHERE id='stage-serve'")
            )

        import core.enterprise_knowledge_serving as serving

        original = serving.canonical_knowledge_serving_snapshot
        calls: list[tuple[object, object]] = []

        def spy(value, *, stage_facts=None, evidence_links=None):
            calls.append((stage_facts, evidence_links))
            return original(
                value, stage_facts=stage_facts, evidence_links=evidence_links
            )

        monkeypatch.setattr(serving, "canonical_knowledge_serving_snapshot", spy)
        state, issues = manifest.inspect_enterprise_knowledge_serving_reliability_capability(
            engine
        )
        assert state == "unavailable"
        assert any("exactly five stage facts" in issue for issue in issues)
        assert all(facts is not None for facts, _ in calls)
    finally:
        engine.dispose()


def test_stage26_required_fragments_include_counts_and_exact_kind_route_contract() -> None:
    stage_counts = manifest.ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REQUIRED_CHECK_FRAGMENTS[
        "tenant_knowledge_serving_stage_facts"
    ]["ck_tenant_knowledge_serving_stage_facts_counts"]
    assert {"ready_count", "warning_count", "pending_count"} <= set(stage_counts)

    kind_route = manifest.ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REQUIRED_CHECK_FRAGMENTS[
        "tenant_knowledge_serving_evidence_links"
    ]["ck_tenant_knowledge_serving_evidence_links_kind_route"]
    for evidence_kind, route_code in (
        ("source", "knowledge_sources"),
        ("source_sync_run", "knowledge_sources"),
        ("document", "knowledge_documents"),
        ("ingest_attempt", "knowledge_documents"),
        ("chunk_head", "knowledge_documents"),
        ("index_operation", "knowledge_indexing"),
        ("release", "knowledge_base_releases"),
        ("certification", "release_quality"),
        ("task", "enterprise_tasks"),
    ):
        assert (
            f"(evidence_kind='{evidence_kind}' and route_code='{route_code}')" in kind_route
        )


def test_stage26_readiness_rejects_snapshot_resources_owned_by_another_dataset(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path, "stage26-resource-ownership.db")
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.execute(
                text(
                    "INSERT INTO dataset_release_manifests "
                    "(id,tenant_id,dataset_id,release_number,profile_revision,ownership_revision," 
                    "workspace_id,workspace_revision,mutation_generation,serving_generation,schema_version," 
                    "policy_digest,manifest_digest,readiness_digest,readiness_state,entry_count,blocker_count," 
                    "readiness_blockers_json,created_at,created_by,reason,request_id) VALUES "
                    "('release-other','tenant-a','dataset-other',1,1,1,'workspace-a',1,0,0,1," 
                    ":digest,:digest,:digest,'ready',0,0,'[]',CURRENT_TIMESTAMP,'owner-a','test','request-release')"
                ),
                {"digest": "b" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO dataset_release_quality_certifications "
                    "(id,tenant_id,dataset_id,release_id,baseline_id,policy_id,policy_revision," 
                    "release_manifest_digest,release_mutation_generation,release_serving_generation,status," 
                    "experiment_count,completed_experiment_count,degraded_experiment_count,query_count," 
                    "judged_result_count,total_result_count,judgment_count,judgment_coverage_bps," 
                    "multi_judged_results,unanimous_results,conflicting_results,exact_agreement_bps," 
                    "mean_score_milli,failed_rule_count,policy_snapshot_json,summary_json,evidence_digest," 
                    "certification_digest,valid_until,created_at,created_by,reason,request_id) VALUES "
                    "('cert-other','tenant-a','dataset-other','release-other','baseline-other','policy-other',1," 
                    ":digest,0,0,'passed',1,1,0,1,1,1,1,10000,0,0,0,10000,3000,0,'{}','{}'," 
                    ":digest,:digest,'2099-01-01T00:00:00.000000','2026-08-30T12:00:00.000000'," 
                    "'owner-a','test','request-cert')"
                ),
                {"digest": "c" * 64},
            )
            connection.commit()

        _seed_valid_graph(
            engine,
            current_release_id="release-other",
            current_certification_id="cert-other",
        )
        state, issues = manifest.inspect_enterprise_knowledge_serving_reliability_capability(
            engine
        )
        assert state == "unavailable"
        assert any("snapshot release dataset ownership mismatch" in issue for issue in issues), issues
        assert any("snapshot certification dataset ownership mismatch" in issue for issue in issues), issues
    finally:
        engine.dispose()



@pytest.mark.parametrize(
    "scenario",
    (
        "wrong_trigger_table",
        "wrong_trigger_schema",
        "same_name_wrong_function_oid",
        "immutable_body_without_raise",
        "wrong_event_table",
        "wrong_event_function",
    ),
)
def test_postgresql_guard_rejects_trigger_and_function_false_positives(scenario: str) -> None:
    class Result:
        def __init__(self, rows):
            self.rows = rows

        def all(self):
            return list(self.rows)

    class FakePostgresConnection:
        class Dialect:
            name = "postgresql"

        dialect = Dialect()

        def __init__(self, scenario: str):
            self.scenario = scenario

        def _detailed_rows(self):
            rows = []
            immutable_body = (
                "CREATE FUNCTION public.rag4c_knowledge_serving_immutable() "
                "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
                "RAISE EXCEPTION 'knowledge serving immutable authority'; END $$"
            )
            event_body = (
                "CREATE FUNCTION public.rag4c_knowledge_serving_event_validate() "
                "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.sequence=1 THEN "
                "IF NEW.event_type<>'profile_created' OR NEW.previous_event_digest IS NOT NULL "
                "THEN RAISE EXCEPTION 'invalid'; END IF; ELSE IF NEW.previous_event_digest IS NULL "
                "OR NOT EXISTS (SELECT 1 FROM tenant_knowledge_serving_events e WHERE "
                "e.tenant_id=NEW.tenant_id AND e.profile_id=NEW.profile_id AND "
                "e.stream_key=NEW.stream_key AND e.sequence=NEW.sequence-1 AND "
                "e.event_digest=NEW.previous_event_digest) THEN RAISE EXCEPTION 'invalid'; "
                "END IF; END IF; RETURN NEW; END $$"
            )
            immutable_oid = 101
            event_oid = 202
            for table in manifest._KNOWLEDGE_SERVING_IMMUTABLE_TABLES:
                for operation in ("update", "delete"):
                    target_table = table
                    table_schema = "public"
                    function_oid = immutable_oid
                    function_schema = "public"
                    function_name = "rag4c_knowledge_serving_immutable"
                    function_definition = immutable_body
                    if self.scenario == "wrong_trigger_table" and table.endswith("snapshots"):
                        target_table = "tenants"
                    if self.scenario == "wrong_trigger_schema" and table.endswith("snapshots"):
                        table_schema = "untrusted"
                    if (
                        self.scenario == "same_name_wrong_function_oid"
                        and table.endswith("snapshots")
                    ):
                        function_oid = 999
                        function_definition = (
                            "CREATE FUNCTION public.rag4c_knowledge_serving_immutable() "
                            "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$"
                        )
                    if (
                        self.scenario == "immutable_body_without_raise"
                        and table.endswith("snapshots")
                    ):
                        function_definition = (
                            "CREATE FUNCTION public.rag4c_knowledge_serving_immutable() "
                            "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$"
                        )
                    trigger_definition = (
                        f"CREATE TRIGGER trg_{table}_no_{operation} BEFORE {operation.upper()} "
                        f"ON {target_table} FOR EACH ROW EXECUTE FUNCTION {function_name}()"
                    )
                    rows.append(
                        (
                            f"trg_{table}_no_{operation}",
                            table_schema,
                            target_table,
                            function_oid,
                            function_schema,
                            function_name,
                            trigger_definition,
                            function_definition,
                        )
                    )
            event_table = manifest.EVENT_TABLE
            event_function_name = "rag4c_knowledge_serving_event_validate"
            if self.scenario == "wrong_event_table":
                event_table = "tenants"
            if self.scenario == "wrong_event_function":
                event_function_name = "rag4c_knowledge_serving_immutable"
            rows.append(
                (
                    manifest.EVENT_INSERT_TRIGGER,
                    "public",
                    event_table,
                    event_oid,
                    "public",
                    event_function_name,
                    f"CREATE TRIGGER {manifest.EVENT_INSERT_TRIGGER} BEFORE INSERT ON "
                    f"{event_table} FOR EACH ROW EXECUTE FUNCTION {event_function_name}()",
                    event_body,
                )
            )
            return rows

        def execute(self, statement, params=None):
            sql = str(statement)
            if "tgfoid" in sql or "p.oid=t.tgfoid" in sql:
                return Result(self._detailed_rows())
            if "pg_get_triggerdef" in sql:
                rows = []
                for table in manifest._KNOWLEDGE_SERVING_IMMUTABLE_TABLES:
                    for operation in ("update", "delete"):
                        rows.append(
                            (
                                f"trg_{table}_no_{operation}",
                                f"CREATE TRIGGER trg_{table}_no_{operation} BEFORE {operation.upper()} ON {table} FOR EACH ROW EXECUTE FUNCTION rag4c_knowledge_serving_immutable()",
                            )
                        )
                rows.append(
                    (
                        "trg_tenant_knowledge_serving_events_validate_insert",
                        "CREATE TRIGGER trg_tenant_knowledge_serving_events_validate_insert BEFORE INSERT ON tenant_knowledge_serving_events FOR EACH ROW EXECUTE FUNCTION rag4c_knowledge_serving_event_validate()",
                    )
                )
                return Result(rows)
            if "pg_get_functiondef" in sql:
                return Result(
                    [
                        (
                            "rag4c_knowledge_serving_immutable",
                            "CREATE FUNCTION public.rag4c_knowledge_serving_immutable() "
                            "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'ok'; END $$",
                        ),
                        (
                            "rag4c_knowledge_serving_event_validate",
                            "CREATE FUNCTION public.rag4c_knowledge_serving_event_validate() "
                            "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.sequence=1 THEN "
                            "IF NEW.event_type<>'profile_created' OR NEW.previous_event_digest IS NOT NULL "
                            "THEN RAISE EXCEPTION 'invalid'; END IF; ELSE IF NEW.previous_event_digest IS NULL "
                            "OR NOT EXISTS (SELECT 1 FROM tenant_knowledge_serving_events e WHERE "
                            "e.tenant_id=NEW.tenant_id AND e.profile_id=NEW.profile_id AND "
                            "e.stream_key=NEW.stream_key AND e.sequence=NEW.sequence-1 AND "
                            "e.event_digest=NEW.previous_event_digest) THEN RAISE EXCEPTION 'invalid'; "
                            "END IF; END IF; RETURN NEW; END $$",
                        ),
                    ]
                )
            raise AssertionError(sql)

        def scalar(self, statement, params=None):
            assert "current_schema" in str(statement)
            return "public"

    issues = manifest._knowledge_serving_guard_issues(FakePostgresConnection(scenario))
    assert issues
    assert any(
        marker in " ".join(issues).lower()
        for marker in ("invalid", "target", "schema", "function", "linkage")
    ), issues
