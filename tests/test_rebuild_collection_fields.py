"""重建脚本的字段漂移与恢复语义守卫 —— master-plan F2.1 踩坑修复。

F2.1 要「在评测集合上重建 BM25」，而这条路径此前根本走不通。四个缺陷依次
挡路，每一个都是「配了不生效」或「静默失效」型——这类故障不会让流程停下，
只会让读数悄悄失去意义，所以值得用测试钉住。

守卫五条：
1. 导出字段**从 schema 动态取**，不写死：写死的清单会随 schema 加字段而漏，
   而回填时漏一个必填字段就整批被拒（那时集合已经被 drop 了）；
2. sparse_vector 仍然排除：它是 BM25 Function 的输出，应用层写不进去；
3. 回填按目标 schema **补齐**缺失字段（标量补空串、整型补 0），而不是
   只做白名单过滤；
4. 恢复的语义是「重建」：集合已存在时先 drop，否则 analyzer 不变（schema
   属性改不了）且旧行还在，再 insert 就是 2N 行；
5. `--collection` 必须真的改到 settings 上：只改局部变量的话，
   ensure_collection() 仍然去动**生产集合**，评测集合却被 drop 掉了。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "rebuild_collection.py"
_spec = importlib.util.spec_from_file_location("rebuild_collection", _SCRIPT)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_export_fields = _mod._export_fields
_normalise = _mod._normalise


class _FakeClient:
    """最小 Milvus 客户端桩：只实现 describe_collection。"""

    def __init__(self, fields: list[dict[str, Any]] | None = None, fail: bool = False) -> None:
        self._fields = fields if fields is not None else [
            {"name": "chunk_id"}, {"name": "text"}, {"name": "dense_vector"},
            {"name": "sparse_vector"}, {"name": "document_revision"},
            {"name": "content_revision"}, {"name": "metadata"},
        ]
        self._fail = fail

    def describe_collection(self, name: str) -> dict[str, Any]:
        if self._fail:
            raise RuntimeError("no permission")
        return {"fields": self._fields}


# ---------------------------------------------------------------------------
# 1 / 2：导出字段
# ---------------------------------------------------------------------------


def test_export_fields_come_from_schema_not_a_hardcoded_list() -> None:
    """后加的 document_revision / content_revision 必须自动被带上。

    写死清单时这两个字段被漏掉，回填阶段整批被拒——而集合那时已经被 drop。
    """
    fields = _export_fields(_FakeClient(), "c")
    assert "document_revision" in fields
    assert "content_revision" in fields


def test_sparse_vector_is_excluded() -> None:
    """它是 BM25 Function 的输出，应用层写不进去，也正是要重算的那一份。"""
    assert "sparse_vector" not in _export_fields(_FakeClient(), "c")


def test_export_fields_falls_back_when_schema_unavailable() -> None:
    """拿不到 schema 时退回历史清单，而不是把流程掐断。"""
    fields = _export_fields(_FakeClient(fail=True), "c")
    assert "chunk_id" in fields and "dense_vector" in fields
    # 兜底清单里也不该有 sparse_vector
    assert "sparse_vector" not in fields


# ---------------------------------------------------------------------------
# 3：回填补齐
# ---------------------------------------------------------------------------


def test_normalise_fills_missing_fields_from_target_schema() -> None:
    """旧备份缺后加字段时按类型补默认值，而不是让整批被拒。"""
    row = {"chunk_id": "c1", "text": "t", "dense_vector": [0.1]}
    fields = ["chunk_id", "text", "dense_vector", "document_revision", "content_revision"]
    out = _normalise(row, fields)
    assert out["document_revision"] == 0
    assert out["content_revision"] == 0


def test_normalise_keeps_existing_values() -> None:
    row = {"chunk_id": "c1", "text": "t", "document_revision": 7}
    out = _normalise(row, ["chunk_id", "text", "document_revision"])
    assert out["document_revision"] == 7


def test_normalise_parses_json_metadata_string() -> None:
    """Milvus 查询可能把 JSON 字段以字符串返回，insert 不接受字符串。"""
    out = _normalise({"chunk_id": "c", "text": "t", "metadata": '{"a": 1}'}, ["chunk_id", "text", "metadata"])
    assert out["metadata"] == {"a": 1}


def test_normalise_handles_broken_metadata_string() -> None:
    out = _normalise({"chunk_id": "c", "text": "t", "metadata": "not json"}, ["chunk_id", "text", "metadata"])
    assert out["metadata"] == {}


def test_normalise_fills_null_scalars_with_empty_string() -> None:
    out = _normalise(
        {"chunk_id": "c", "text": "t", "tenant_id": None, "acl": None},
        ["chunk_id", "text", "tenant_id", "acl"],
    )
    assert out["tenant_id"] == ""
    assert out["acl"] == ""


def test_normalise_without_fields_uses_legacy_whitelist() -> None:
    """不传 fields 时退回历史清单：回填旧备份时那批行本来就没有新字段。"""
    out = _normalise({"chunk_id": "c", "text": "t"}, None)
    assert set(out) >= {"chunk_id", "text"}
    assert "sparse_vector" not in out


# ---------------------------------------------------------------------------
# 4 / 5：CLI 语义（读源码守边界）
# ---------------------------------------------------------------------------


def _main_source() -> str:
    """只取 main() 的函数体。

    直接 split 整个文件会先命中模块顶部的用法说明（那里也提到
    ``--restore-from``），断言就会在"文档写对了、代码没改"的情况下变绿。
    """
    source = _SCRIPT.read_text(encoding="utf-8")
    return source.split("def main()", 1)[1].split('if __name__ == "__main__"', 1)[0]


def test_restore_branch_drops_existing_collection_first() -> None:
    """恢复 = 重建，不是追加。

    只 ensure_collection() 的话，集合已存在时幂等跳过 → analyzer 不变
    （schema 属性改不了）→ 旧行还在 → 再 insert 就是 2N 行。实测出现过 454 行。
    """
    main_src = _main_source()
    restore_at = main_src.index("if args.restore_from:")
    # 窗口取到下一个顶层注释为止，避免跨进后面的分支
    end = main_src.find("# ---------- 1. 导出", restore_at)
    block = main_src[restore_at : end if end > 0 else restore_at + 900]
    assert "client.drop_collection(collection)" in block, "恢复分支必须先 drop 再建"
    # drop 必须在 ensure_collection 之前。**只认代码行**——上面那段解释性
    # 注释里也出现了 ensure_collection()，按子串找会误判成"注释在前"。
    code_lines = [ln.strip() for ln in block.splitlines() if not ln.strip().startswith("#")]
    drop_line = next(i for i, ln in enumerate(code_lines) if "drop_collection" in ln)
    create_line = next(i for i, ln in enumerate(code_lines) if "mc.ensure_collection()" in ln)
    assert drop_line < create_line, f"drop（第 {drop_line} 行）必须先于建集合（第 {create_line} 行）"
    # 且要核对行数，不一致必须告警
    assert "get_collection_stats" in block
    assert "警告" in block


def test_collection_flag_must_write_into_settings() -> None:
    """--collection 必须改 settings.milvus.collection_name 本身。

    只改局部变量的话，ensure_collection() 与回填仍然去动 settings 里的
    **生产集合**，而评测集合被 drop 掉了——这是实测踩过的坑。
    """
    source = _SCRIPT.read_text(encoding="utf-8")
    assert "settings.milvus.collection_name = args.collection" in source, (
        "--collection 必须写回 settings，否则 MilvusClient 仍指向生产集合"
    )


def test_analyzer_flag_is_process_local_only() -> None:
    """实验参数只改进程内副本，不污染配置文件。"""
    source = _SCRIPT.read_text(encoding="utf-8")
    assert "settings.milvus.bm25_analyzer = args.analyzer" in source
    # 不得出现写回 .env 的行为
    assert ".env" not in source.split("def main", 1)[1].split("if __name__", 1)[0]


def test_backup_roundtrip_is_json_serialisable() -> None:
    """备份是普通 JSON，中途失败才能靠 --restore-from 单独救回来。"""
    row = {"chunk_id": "c1", "text": "中文", "metadata": {"k": [1, 2]}}
    normalised = _normalise(row, ["chunk_id", "text", "metadata"])
    assert json.loads(json.dumps(normalised, ensure_ascii=False)) == normalised


@pytest.mark.parametrize("missing", ["document_revision", "content_revision", "tenant_id"])
def test_every_backfillable_field_survives_a_legacy_backup(missing: str) -> None:
    """旧备份缺任一后加字段都不该让回填失败。"""
    row = {"chunk_id": "c", "text": "t"}
    fields = ["chunk_id", "text", missing]
    out = _normalise(row, fields)
    assert missing in out and out[missing] is not None
