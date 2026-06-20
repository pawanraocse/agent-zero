from agent_zero.context import ContextIntent, build_repository_context


def test_build_repository_context_includes_default_files(tmp_path):
    (tmp_path / "README.md").write_text("# Agent Zero\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=value\n", encoding="utf-8")

    context = build_repository_context(tmp_path, "What does this project do?")
    prompt = context.to_prompt()

    assert context.files == ["README.md", "pyproject.toml"]
    assert context.intent == ContextIntent.OVERVIEW
    assert [snippet.path for snippet in context.snippets] == [
        "README.md",
        "pyproject.toml",
    ]
    assert ".env" not in prompt
    assert "Selected file contents:" in prompt


def test_build_repository_context_prefers_config_files_for_bedrock_questions(tmp_path):
    package = tmp_path / "agent_zero"
    package.mkdir()
    (package / "config.py").write_text("BEDROCK_URL = 'x'\n", encoding="utf-8")
    (package / "model_client.py").write_text(
        "class BedrockGatewayClient: ...\n", encoding="utf-8"
    )
    (tmp_path / ".env.example").write_text(
        "AGENT_ZERO_PROVIDER=bedrock\n", encoding="utf-8"
    )
    (tmp_path / "README.md").write_text("# Docs\n", encoding="utf-8")

    context = build_repository_context(tmp_path, "How does Bedrock config work?")

    assert context.intent == ContextIntent.CONFIG
    assert [snippet.path for snippet in context.snippets] == [
        "agent_zero/config.py",
        "agent_zero/model_client.py",
        ".env.example",
        "README.md",
    ]


def test_build_repository_context_prefers_tests_for_test_questions(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_model_client.py").write_text(
        "def test_model_client(): pass\n", encoding="utf-8"
    )
    package = tmp_path / "agent_zero"
    package.mkdir()
    (package / "model_client.py").write_text(
        "class ModelClient: ...\n", encoding="utf-8"
    )
    (tmp_path / "README.md").write_text("# Docs\n", encoding="utf-8")

    context = build_repository_context(tmp_path, "What tests cover model clients?")

    assert context.intent == ContextIntent.TESTS
    assert [snippet.path for snippet in context.snippets] == [
        "tests/test_model_client.py",
        "agent_zero/model_client.py",
    ]
