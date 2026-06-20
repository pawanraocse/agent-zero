from agent_zero.context import build_repository_context, decide_context_files


def test_build_repository_context_includes_overview_files(tmp_path):
    (tmp_path / "README.md").write_text("# Agent Zero\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=value\n", encoding="utf-8")

    context = build_repository_context(tmp_path, "What does this project do?")
    prompt = context.to_prompt()

    assert context.files == ["README.md", "pyproject.toml"]
    assert context.decision.selected_files == ["README.md", "pyproject.toml"]
    assert [snippet.path for snippet in context.snippets] == [
        "README.md",
        "pyproject.toml",
    ]
    assert ".env" not in prompt
    assert "Context selection:" in prompt
    assert "overview prior" in prompt


def test_decide_context_files_ranks_config_files_for_bedrock_questions():
    files = [
        "README.md",
        ".env.example",
        "agent_zero/config.py",
        "agent_zero/model_client.py",
        "tests/test_model_client.py",
    ]
    search_results = [
        "agent_zero/model_client.py:70: class BedrockGatewayClient:",
        ".env.example:7: AGENT_ZERO_PROVIDER=bedrock",
    ]

    decision = decide_context_files(
        files=files,
        task="How does Bedrock config work?",
        search_results=search_results,
        max_snippets=4,
    )

    assert decision.selected_files[:3] == [
        "agent_zero/model_client.py",
        "agent_zero/config.py",
        ".env.example",
    ]
    assert "content search hit" in decision.reasons["agent_zero/model_client.py"]


def test_decide_context_files_can_select_unclassified_domain_files():
    files = [
        "README.md",
        "agent_zero/payment_gateway.py",
        "tests/test_payment_gateway.py",
        "agent_zero/model_client.py",
    ]
    search_results = [
        "agent_zero/payment_gateway.py:12: def calculate_refund():",
        "tests/test_payment_gateway.py:5: def test_calculate_refund():",
    ]

    decision = decide_context_files(
        files=files,
        task="How is refund calculation implemented?",
        search_results=search_results,
        max_snippets=3,
    )

    assert decision.selected_files == [
        "agent_zero/payment_gateway.py",
        "tests/test_payment_gateway.py",
    ]
