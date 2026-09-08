from __future__ import annotations

import pytest

from server.app import _format_env_value, _resolve_eval_output
from server.config_writer import update_dotenv_text


def test_dotenv_update_preserves_unrelated_content() -> None:
    source = "# operator note\nRAG4C_A=old\n\nexport RAG4C_B = keep\n"

    updated = update_dotenv_text(source, {"RAG4C_A": "new", "RAG4C_C": "3"})

    assert updated == (
        "# operator note\nRAG4C_A=new\n\nexport RAG4C_B = keep\n\nRAG4C_C=3\n"
    )


def test_dotenv_value_rejects_line_injection() -> None:
    with pytest.raises(ValueError, match="换行"):
        _format_env_value("safe\nRAG4C_INJECTED=1")


def test_dotenv_value_quotes_spaces_and_expansion() -> None:
    assert _format_env_value("model name $HOME") == '"model name \\$HOME"'


@pytest.mark.parametrize("raw", ["../outside.json", "results.json", "eval/report.txt"])
def test_eval_output_rejects_unsafe_paths(raw: str) -> None:
    with pytest.raises(ValueError):
        _resolve_eval_output(raw)


def test_eval_output_accepts_project_eval_json() -> None:
    resolved = _resolve_eval_output("eval/reports/latest.json")

    assert resolved.name == "latest.json"
    assert "eval" in resolved.parts
    assert resolved.is_absolute()
