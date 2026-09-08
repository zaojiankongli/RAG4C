# KnowledgeOps disposable MySQL release drill evidence

**Date:** August 24, 2026
**Status:** Passed
**Evidence source:** Operator-confirmed execution of `scripts/verify_knowledgeops_mysql.py`

## Safety boundary

- The drill used a uniquely named disposable MySQL database.
- The configured business database `rag4c` was not used as the drill database.
- The disposable database was dropped after verification.
- Task 3 fix round 3 did not rerun the MySQL drill or connect to the external server.

## Observed migration sequence

```text
create unique temporary database
→ upgrade 0001_base through 0008_governance
→ downgrade to 0006_source_id
→ verify chunk-authority tables are absent
→ re-upgrade to 0008_governance
→ verify current schema and chunk-authority tables
→ drop temporary database
```

## Release-gate conclusion

The isolated MySQL upgrade/downgrade/re-upgrade path completed successfully and cleanup completed. Future release runs remain mandatory through:

```powershell
$env:TEST_MYSQL_URL = "<isolated-server-credentials>"
uv run python scripts/verify_knowledgeops_mysql.py
```

The drill script always derives a new `rag4c_knowledgeops_drill_<random>` database name and connects through MySQL's administrative database rather than opening the database named in `TEST_MYSQL_URL`.
