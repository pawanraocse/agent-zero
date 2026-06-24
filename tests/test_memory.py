import json

from agent_zero.memory import (
    append_memory_record,
    apply_memory_feedback,
    build_reflection,
    classify_memory_candidate,
    delete_memory_items,
    detect_user_feedback,
    load_memory,
    load_memory_items,
    memory_file_scores,
    memory_item_scores,
    reset_memory,
    write_memory_candidate,
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


def test_memory_item_scores_only_boosts_confirmed_items():
    items = [
        {
            "status": "confirmed",
            "confidence": "high",
            "task_terms": ["bedrock", "gateway"],
            "useful_files": ["agent_zero/model_client.py"],
        },
        {
            "status": "candidate",
            "confidence": "medium",
            "task_terms": ["bedrock", "gateway"],
            "useful_files": ["agent_zero/config.py"],
        },
        {
            "status": "rejected",
            "confidence": "low",
            "task_terms": ["bedrock", "gateway"],
            "useful_files": ["agent_zero/old.py"],
        },
    ]

    scores = memory_item_scores(items, ["bedrock", "gateway"])

    assert scores == {"agent_zero/model_client.py": 12}


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


def test_classify_memory_candidate_confirms_validated_code_runs():
    candidate = classify_memory_candidate(
        {
            "mode": "code",
            "task_terms": ["bedrock", "gateway"],
            "changed_files": ["agent_zero/model_client.py"],
            "status": "validation_passed",
            "success": True,
            "validation_passed": True,
        }
    )

    assert candidate is not None
    assert candidate["type"] == "project_lesson"
    assert candidate["status"] == "confirmed"
    assert candidate["confidence"] == "high"
    assert candidate["useful_files"] == ["agent_zero/model_client.py"]
    assert "validation_passed" in candidate["evidence"]


def test_classify_memory_candidate_rejects_failed_runs():
    candidate = classify_memory_candidate(
        {
            "mode": "code",
            "task_terms": ["readme"],
            "status": "patch_failed",
            "success": False,
        }
    )

    assert candidate is not None
    assert candidate["type"] == "failure_lesson"
    assert candidate["status"] == "rejected"
    assert candidate["confidence"] == "low"


def test_write_memory_candidate_stores_sqlite_item(tmp_path):
    record = {
        "mode": "code",
        "task_terms": ["bedrock", "gateway"],
        "changed_files": ["agent_zero/model_client.py"],
        "status": "validation_passed",
        "success": True,
        "validation_passed": True,
        "reflection": {
            "lesson": "validated",
            "task_terms": ["bedrock", "gateway"],
            "useful_files": ["agent_zero/model_client.py"],
            "confidence": "high",
        },
    }

    db_path = write_memory_candidate(tmp_path, record)
    write_memory_candidate(tmp_path, record)
    items = load_memory_items(tmp_path)

    assert db_path == tmp_path / ".agent-zero" / "memory.db"
    assert len(items) == 1
    assert items[0]["type"] == "project_lesson"
    assert items[0]["status"] == "confirmed"
    assert items[0]["confidence"] == "high"
    assert items[0]["task_terms"] == ["bedrock", "gateway"]
    assert items[0]["useful_files"] == ["agent_zero/model_client.py"]
    assert items[0]["use_count"] == 2


def test_delete_memory_items_removes_rejected_but_keeps_confirmed(tmp_path):
    write_memory_candidate(
        tmp_path,
        {
            "mode": "code",
            "task_terms": ["bedrock"],
            "changed_files": ["agent_zero/model_client.py"],
            "status": "validation_passed",
            "success": True,
            "validation_passed": True,
        },
    )
    write_memory_candidate(
        tmp_path,
        {
            "mode": "code",
            "task_terms": ["readme"],
            "status": "patch_failed",
            "success": False,
        },
    )

    deleted_count = delete_memory_items(tmp_path, {"rejected"})
    items = load_memory_items(tmp_path)

    assert deleted_count == 1
    assert len(items) == 1
    assert items[0]["status"] == "confirmed"


def test_reset_memory_deletes_sqlite_and_can_keep_raw_log(tmp_path):
    append_memory_record(
        tmp_path,
        {
            "mode": "ask",
            "task_terms": ["project"],
            "selected_files": ["README.md"],
            "status": "ask_completed",
            "success": True,
        },
    )
    write_memory_candidate(
        tmp_path,
        {
            "mode": "code",
            "task_terms": ["bedrock"],
            "changed_files": ["agent_zero/model_client.py"],
            "status": "validation_passed",
            "success": True,
            "validation_passed": True,
        },
    )

    deleted = reset_memory(tmp_path)

    assert deleted == {"sqlite_items": 1, "raw_records": 0}
    assert load_memory_items(tmp_path) == []
    assert len(load_memory(tmp_path)) == 1


def test_reset_memory_can_delete_raw_log(tmp_path):
    append_memory_record(
        tmp_path,
        {
            "mode": "ask",
            "task_terms": ["project"],
            "selected_files": ["README.md"],
            "status": "ask_completed",
            "success": True,
        },
    )

    deleted = reset_memory(tmp_path, include_raw=True)

    assert deleted == {"sqlite_items": 0, "raw_records": 1}
    assert load_memory(tmp_path) == []


def test_apply_memory_feedback_promotes_latest_candidate(tmp_path):
    write_memory_candidate(
        tmp_path,
        {
            "mode": "ask",
            "task_terms": ["bedrock"],
            "status": "ask_completed",
            "success": True,
        },
    )

    item = apply_memory_feedback(tmp_path, "worked")

    assert item is not None
    assert item["status"] == "confirmed"
    assert item["confidence"] == "high"


def test_apply_memory_feedback_can_reject_confirmed_item(tmp_path):
    write_memory_candidate(
        tmp_path,
        {
            "mode": "code",
            "task_terms": ["bedrock"],
            "changed_files": ["agent_zero/model_client.py"],
            "status": "validation_passed",
            "success": True,
            "validation_passed": True,
        },
    )

    item = apply_memory_feedback(tmp_path, "failed", status="confirmed")

    assert item is not None
    assert item["status"] == "rejected"
    assert item["confidence"] == "low"


def test_detect_user_feedback_recognizes_clear_phrases():
    assert detect_user_feedback("it worked") == "worked"
    assert detect_user_feedback("That fixed it") == "worked"
    assert detect_user_feedback("did not work") == "failed"
    assert detect_user_feedback("issue still exists") == "failed"
    assert detect_user_feedback("let us move to the next task") is None
