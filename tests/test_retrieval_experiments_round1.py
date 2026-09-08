from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.orm import Session

from core.retrieval_experiments import (
    RetrievalExperimentConflict,
    RetrievalExperimentDatasetInactive,
    RetrievalExperimentNotFound,
    RetrievalExperimentRepository,
)
from models.orm import Dataset, RetrievalExperiment, RetrievalJudgment
from tests.test_retrieval_experiments import _audit, _create, _repository, _snapshots


def _set_dataset_status(engine, status: str) -> None:
    with Session(engine) as session:
        session.execute(
            update(Dataset)
            .where(Dataset.id == "dataset-1", Dataset.tenant_id == "tenant-1")
            .values(status=status)
        )
        session.commit()


def test_all_retrieval_mutations_reject_inactive_dataset_in_repository(
    tmp_path: Path,
) -> None:
    engine, repository = _repository(tmp_path)
    strategy, results, lineage = _snapshots()
    _set_dataset_status(engine, "archived")

    with pytest.raises(RetrievalExperimentDatasetInactive):
        repository.create_experiment(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            query="inactive create",
            strategy_snapshot=strategy,
            result_snapshot=results,
            evidence_lineage=lineage,
            latency_ms=1,
            status="completed",
            audit=_audit("judge-a", "inactive-create"),
        )

    _set_dataset_status(engine, "active")
    experiment = _create(repository)
    _set_dataset_status(engine, "archived")
    with pytest.raises(RetrievalExperimentDatasetInactive):
        repository.add_judgment(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            experiment_id=experiment.id,
            result_rank=1,
            relevance_label="relevant",
            audit=_audit("judge-a", "inactive-add"),
        )

    _set_dataset_status(engine, "active")
    judgment = repository.add_judgment(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        experiment_id=experiment.id,
        result_rank=1,
        relevance_label="relevant",
        score=3,
        note="original",
        audit=_audit("judge-a", "active-add"),
    )
    _set_dataset_status(engine, "disabled")
    with pytest.raises(RetrievalExperimentDatasetInactive):
        repository.update_judgment_partial(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            experiment_id=experiment.id,
            judgment_id=judgment.id,
            expected_revision=1,
            note="blocked",
            audit=_audit("judge-a", "inactive-update"),
        )
    with Session(engine) as session:
        stored = session.get(RetrievalJudgment, judgment.id)
        assert stored is not None
        assert (stored.revision, stored.note) == (1, "original")
    engine.dispose()


def test_partial_judgment_cas_updates_only_supplied_fields(tmp_path: Path) -> None:
    engine, repository = _repository(tmp_path)
    experiment = _create(repository)
    other_experiment = _create(repository, query="other experiment")
    judgment = repository.add_judgment(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        experiment_id=experiment.id,
        result_rank=1,
        relevance_label="relevant",
        score=3,
        note="original",
        audit=_audit("judge-a", "partial-create"),
    )

    note_clear = repository.update_judgment_partial(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        experiment_id=experiment.id,
        judgment_id=judgment.id,
        expected_revision=1,
        note="",
        audit=_audit("judge-a", "clear-note"),
    )
    assert (
        note_clear.revision,
        note_clear.relevance_label,
        note_clear.score,
        note_clear.note,
    ) == (2, "relevant", 3, "")

    score_clear = repository.update_judgment_partial(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        experiment_id=experiment.id,
        judgment_id=judgment.id,
        expected_revision=2,
        score=None,
        audit=_audit("judge-a", "clear-score"),
    )
    assert (
        score_clear.revision,
        score_clear.relevance_label,
        score_clear.score,
        score_clear.note,
    ) == (3, "relevant", None, "")

    with pytest.raises(RetrievalExperimentNotFound):
        repository.update_judgment_partial(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            experiment_id=other_experiment.id,
            judgment_id=judgment.id,
            expected_revision=3,
            relevance_label="partial",
            audit=_audit("judge-a", "wrong-experiment"),
        )
    with pytest.raises(RetrievalExperimentConflict, match="revision"):
        repository.update_judgment_partial(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            experiment_id=experiment.id,
            judgment_id=judgment.id,
            expected_revision=2,
            relevance_label="partial",
            audit=_audit("judge-a", "stale-partial"),
        )
    with pytest.raises(ValueError, match="at least one"):
        repository.update_judgment_partial(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            experiment_id=experiment.id,
            judgment_id=judgment.id,
            expected_revision=3,
            audit=_audit("judge-a", "empty-partial"),
        )
    engine.dispose()


def _enable_sqlite_races(engine) -> None:
    with engine.connect() as connection:
        mode = connection.exec_driver_sql("PRAGMA journal_mode=WAL").scalar_one()
        assert str(mode).casefold() == "wal"
    engine.dispose()

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()


def _archive_after_initial_read(
    engine,
    repository: RetrievalExperimentRepository,
    mutation,
):
    initial_read = Event()
    archive_committed = Event()

    def pause_after_read(*_args, **_kwargs) -> None:
        initial_read.set()
        assert archive_committed.wait(timeout=10)

    repository._before_dataset_active_fence = pause_after_read

    def run_mutation():
        try:
            return ("ok", mutation())
        except RetrievalExperimentDatasetInactive as exc:
            return ("inactive", exc)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_mutation)
        try:
            assert initial_read.wait(timeout=3), "mutation never exposed the post-read fence hook"
            with Session(engine) as session:
                changed = session.execute(
                    update(Dataset)
                    .where(
                        Dataset.id == "dataset-1",
                        Dataset.tenant_id == "tenant-1",
                        Dataset.status == "active",
                    )
                    .values(status="archived")
                )
                assert changed.rowcount == 1
                session.commit()
        finally:
            archive_committed.set()
        outcome = future.result(timeout=10)

    with Session(engine) as session:
        assert (
            session.scalar(
                select(Dataset.status).where(
                    Dataset.id == "dataset-1", Dataset.tenant_id == "tenant-1"
                )
            )
            == "archived"
        )
    return outcome


def test_sqlite_archive_after_initial_read_blocks_experiment_create(tmp_path: Path) -> None:
    engine, repository = _repository(tmp_path)
    _enable_sqlite_races(engine)
    strategy, results, lineage = _snapshots()

    outcome = _archive_after_initial_read(
        engine,
        repository,
        lambda: repository.create_experiment(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            query="raced create",
            strategy_snapshot=strategy,
            result_snapshot=results,
            evidence_lineage=lineage,
            latency_ms=1,
            status="completed",
            audit=_audit("judge-a", "raced-create"),
        ),
    )
    assert outcome[0] == "inactive"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(RetrievalExperiment)) == 0
    engine.dispose()


def test_sqlite_archive_after_initial_read_blocks_judgment_add(tmp_path: Path) -> None:
    engine, repository = _repository(tmp_path)
    experiment = _create(repository)
    _enable_sqlite_races(engine)

    outcome = _archive_after_initial_read(
        engine,
        repository,
        lambda: repository.add_judgment(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            experiment_id=experiment.id,
            result_rank=1,
            relevance_label="relevant",
            audit=_audit("judge-a", "raced-add"),
        ),
    )
    assert outcome[0] == "inactive"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(RetrievalJudgment)) == 0
    engine.dispose()


def test_sqlite_archive_after_initial_read_blocks_judgment_update(tmp_path: Path) -> None:
    engine, repository = _repository(tmp_path)
    experiment = _create(repository)
    judgment = repository.add_judgment(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        experiment_id=experiment.id,
        result_rank=1,
        relevance_label="relevant",
        note="original",
        audit=_audit("judge-a", "before-raced-update"),
    )
    _enable_sqlite_races(engine)

    outcome = _archive_after_initial_read(
        engine,
        repository,
        lambda: repository.update_judgment_partial(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            experiment_id=experiment.id,
            judgment_id=judgment.id,
            expected_revision=1,
            note="raced",
            audit=_audit("judge-a", "raced-update"),
        ),
    )
    assert outcome[0] == "inactive"
    with Session(engine) as session:
        stored = session.get(RetrievalJudgment, judgment.id)
        assert stored is not None
        assert (stored.revision, stored.note) == (1, "original")
    engine.dispose()
