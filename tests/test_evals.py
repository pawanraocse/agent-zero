import json

import pytest

from agent_zero.evals import EvalSpecError, load_eval_spec, write_eval_result


def test_load_eval_spec_from_json_file(tmp_path):
    spec_path = tmp_path / "ask-project.json"
    spec_path.write_text(
        json.dumps(
            {
                "name": "ask-project",
                "mode": "ask",
                "task": "What does this project do?",
            }
        ),
        encoding="utf-8",
    )

    spec = load_eval_spec(spec_path)

    assert spec.name == "ask-project"
    assert spec.mode == "ask"
    assert spec.task == "What does this project do?"
    assert spec.validation_command is None


def test_load_eval_spec_rejects_invalid_mode(tmp_path):
    spec_path = tmp_path / "bad.json"
    spec_path.write_text(
        json.dumps(
            {
                "name": "bad",
                "mode": "edit",
                "task": "Change something",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(EvalSpecError, match="mode must be one of"):
        load_eval_spec(spec_path)


def test_write_eval_result_creates_timestamped_json_file(tmp_path):
    result = {
        "name": "Readme Note",
        "success": True,
        "status": "ask_completed",
    }

    path = write_eval_result(result, tmp_path)

    assert path.parent == tmp_path
    assert path.name.endswith("-readme-note.json")
    assert json.loads(path.read_text(encoding="utf-8")) == result
