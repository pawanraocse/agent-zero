import json

from agent_zero.memory import (
    append_memory_record,
    build_reflection,
    load_memory,
    memory_file_scores,
)


def test_append_memory_record_keeps_recent_records(tmp_path):
    for index in range(3):
        append_memory_record(
            tmp_path,
            {
                "mode": "ask",
                "task_terms": [f"term{index}"],
                "selected_files": [f"file{index}.py"],
                "status": "ask_completed",
                "success": True,
            },
            max_records=2,
        )

    records = load_memory(tmp_path)

    assert [record["task_terms"] for record in records] == [["term1"], ["term2"]]
    memory_file = tmp_path / ".agent-zero" / "memory.jsonl"
    assert len(memory_file.read_text(encoding="utf-8").splitlines()) == 2


def test_append_memory_record_compacts_duplicate_lessons(tmp_path):
    record = {
        "mode": "code",
        "task_terms": ["bedrock", "gateway"],
        "selected_files": ["agent_zero/model_client.py"],
        "useful_files": ["agent_zero/model_client.py"],
        "changed_files": ["agent_zero/model_client.py"],
        "status": "validation_passed",
        "success": True,
        "reflection": {
            "lesson": "reusable",
            "task_terms": ["bedrock", "gateway"],
            "useful_files": ["agent_zero/model_client.py"],
            "confidence": "high",
        },
    }

    append_memory_record(tmp_path, record)
    append_memory_record(tmp_path, record)

    records = load_memory(tmp_path)

    assert len(records) == 1
    assert records[0]["occurrences"] == 2
    assert "created_at" in records[0]
    assert "last_seen_at" in records[0]
    assert records[0]["reflection"]["confidence"] == "high"


def test_append_memory_record_keeps_distinct_useful_files(tmp_path):
    base_record = {
        "mode": "code",
        "task_terms": ["bedrock"],
        "selected_files": ["agent_zero/model_client.py"],
        "status": "validation_passed",
        "success": True,
    }

    append_memory_record(
        tmp_path,
        {
            **base_record,
            "useful_files": ["agent_zero/model_client.py"],
            "changed_files": ["agent_zero/model_client.py"],
        },
    )
    append_memory_record(
        tmp_path,
        {
            **base_record,
            "useful_files": ["agent_zero/config.py"],
            "changed_files": ["agent_zero/config.py"],
        },
    )

    records = load_memory(tmp_path)

    assert len(records) == 2
    assert [record["occurrences"] for record in records] == [1, 1]


def test_memory_file_scores_boosts_successful_similar_tasks():
    records = [
        {
            "task_terms": ["bedrock", "gateway"],
            "useful_files": ["agent_zero/model_client.py"],
            "success": True,
        },
        {
            "task_terms": ["bedrock"],
            "selected_files": ["agent_zero/old.py"],
            "success": False,
        },
    ]

    scores = memory_file_scores(records, ["bedrock", "gateway"])

    assert scores == {"agent_zero/model_client.py": 4}


def test_memory_file_scores_does_not_treat_selected_files_as_useful():
    records = [
        {
            "task_terms": ["bedrock", "gateway"],
            "selected_files": ["tests/test_model_client.py"],
            "useful_files": [],
            "success": True,
        }
    ]

    assert memory_file_scores(records, ["bedrock", "gateway"]) == {}


def test_load_memory_ignores_invalid_json_lines(tmp_path):
    memory_dir = tmp_path / ".agent-zero"
    memory_dir.mkdir()
    (memory_dir / "memory.jsonl").write_text(
        "\n".join(
            [
                "{not-json}",
                json.dumps({"success": True, "selected_files": ["README.md"]}),
            ]
        ),
        encoding="utf-8",
    )

    assert load_memory(tmp_path) == [{"success": True, "selected_files": ["README.md"]}]


def test_build_reflection_for_successful_code_run():
    reflection = build_reflection(
        {
            "mode": "code",
            "task_terms": ["bedrock", "gateway"],
            "changed_files": ["agent_zero/model_client.py"],
            "status": "validation_passed",
            "success": True,
            "validation_passed": True,
        }
    )

    assert reflection == {
        "lesson": (
            "code run succeeded with validation passing; future similar tasks may "
            "reuse changed files: agent_zero/model_client.py."
        ),
        "task_terms": ["bedrock", "gateway"],
        "useful_files": ["agent_zero/model_client.py"],
        "confidence": "high",
    }


def test_build_reflection_for_failed_run_is_low_confidence():
    reflection = build_reflection(
        {
            "mode": "code",
            "task_terms": ["readme"],
            "selected_files": ["README.md"],
            "status": "patch_failed",
            "success": False,
        }
    )

    assert reflection["lesson"] == (
        "code run ended with patch_failed; do not boost files from this run."
    )
    assert reflection["confidence"] == "low"
    assert reflection["useful_files"] == []
