from agent_zero.task_classifier import classify_task


def test_classify_explanation_as_ask():
    result = classify_task("Explain Bedrock gateway polling")

    assert result.action_type == "read"
    assert result.recommended_mode == "ask"
    assert result.subcategory == "explain_code"
    assert result.write_intent == "none"
    assert result.specificity == "high"
    assert result.requires_clarification is False


def test_classify_architecture_request_as_plan():
    result = classify_task("Plan architecture for hybrid memory")

    assert result.action_type == "plan"
    assert result.recommended_mode == "plan"
    assert result.subcategory == "architecture_plan"
    assert result.write_intent == "none"
    assert result.requires_clarification is False


def test_classify_specific_documentation_edit_as_code():
    result = classify_task(
        "Add one sentence to README.md saying Agent Zero teaches agent internals"
    )

    assert result.action_type == "write"
    assert result.recommended_mode == "code"
    assert result.subcategory == "documentation_edit"
    assert result.write_intent == "explicit"
    assert result.specificity == "high"
    assert result.requires_clarification is False


def test_classify_vague_documentation_edit_needs_clarification():
    result = classify_task("Add a short README note")

    assert result.action_type == "write"
    assert result.recommended_mode == "code"
    assert result.subcategory == "documentation_edit"
    assert result.write_intent == "explicit"
    assert result.specificity == "low"
    assert result.requires_clarification is True
    assert "exact documentation text or topic" in result.missing_information


def test_classify_proceed_as_possible_write_needing_clarification():
    result = classify_task("proceed")

    assert result.action_type == "write"
    assert result.recommended_mode == "code"
    assert result.subcategory == "possible_write"
    assert result.write_intent == "possible"
    assert result.specificity == "low"
    assert result.requires_clarification is True
    assert "which task to proceed with" in result.missing_information


def test_classify_feedback_as_memory_feedback_recommends_memory_mode():
    result = classify_task("it worked")

    assert result.action_type == "write"
    assert result.recommended_mode == "memory"
    assert result.subcategory == "memory_feedback"
    assert result.write_intent == "explicit"
    assert result.requires_clarification is False
