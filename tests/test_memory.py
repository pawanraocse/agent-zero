import json

from agent_zero.memory import append_memory_record, load_memory, memory_file_scores


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
