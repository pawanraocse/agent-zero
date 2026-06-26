import json

import pytest

from agent_zero.evals import (
    EvalSpecError,
    load_eval_spec,
    load_eval_suite,
    write_eval_result,
)


def test_load_eval_spec_from_json_file(tmp_path):
    spec_path = tmp_path / "ask-project.json"
    spec_path.write_text(
        json.dumps(
            {
                "name": "ask-project",
                "mode": "ask",
                "task": "What does this project do?",
                "expected_terms": ["Agent Zero"],
                "forbidden_terms": ["production assistant"],
            }
        ),
        encoding="utf-8",
    )

    spec = load_eval_spec(spec_path)

    assert spec.name == "ask-project"
    assert spec.mode == "ask"
    assert spec.task == "What does this project do?"
    assert spec.validation_command is None
    assert spec.expected_terms == ["Agent Zero"]
    assert spec.forbidden_terms == ["production assistant"]


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


def test_load_eval_spec_rejects_invalid_expected_terms(tmp_path):
    spec_path = tmp_path / "bad-terms.json"
    spec_path.write_text(
        json.dumps(
            {
                "name": "bad-terms",
                "mode": "ask",
                "task": "Explain Bedrock gateway",
                "expected_terms": ["Bedrock", 42],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(EvalSpecError, match="expected_terms"):
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


def test_load_eval_suite_accepts_file_and_inline_specs(tmp_path):
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
    suite_path = tmp_path / "core.json"
    suite_path.write_text(
        json.dumps(
            {
                "name": "core",
                "evals": [
                    "ask-project.json",
                    {
                        "name": "bedrock",
                        "mode": "ask",
                        "task": "Explain Bedrock gateway",
                        "expected_terms": ["BedrockGatewayClient"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    suite = load_eval_suite(suite_path)

    assert suite.name == "core"
    assert [spec.name for spec in suite.evals] == ["ask-project", "bedrock"]
    assert suite.evals[1].expected_terms == ["BedrockGatewayClient"]


def test_load_eval_suite_rejects_empty_evals(tmp_path):
    suite_path = tmp_path / "bad-suite.json"
    suite_path.write_text(
        json.dumps({"name": "bad", "evals": []}),
        encoding="utf-8",
    )

    with pytest.raises(EvalSpecError, match="non-empty list field: evals"):
        load_eval_suite(suite_path)
