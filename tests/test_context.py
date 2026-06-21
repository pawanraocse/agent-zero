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


def test_decide_context_files_uses_repo_index_concepts():
    files = [
        "agent_zero/model_client.py",
        "agent_zero/config.py",
    ]
    repo_index = {
        "files": [
            {
                "path": "agent_zero/model_client.py",
                "summary": "Handles hosted model providers.",
                "concepts": ["bedrock", "gateway", "model"],
                "symbols": ["BedrockGatewayClient"],
            }
        ],
        "relationships": [
            {
                "from": "agent_zero/config.py",
                "to": "agent_zero/model_client.py",
                "type": "imports",
            }
        ],
    }

    decision = decide_context_files(
        files=files,
        task="How does bedrock work?",
        search_results=[],
        max_snippets=2,
        repo_index=repo_index,
    )

    assert decision.index_used is True
    assert decision.selected_files == [
        "agent_zero/model_client.py",
        "agent_zero/config.py",
    ]
    assert (
        "index concept matches: bedrock"
        in decision.reasons["agent_zero/model_client.py"]
    )
    assert (
        "index related via imports: agent_zero/model_client.py"
        in decision.reasons["agent_zero/config.py"]
    )


def test_decide_context_files_uses_learning_memory():
    files = [
        "README.md",
        "agent_zero/model_client.py",
    ]
    memory_records = [
        {
            "task_terms": ["bedrock", "gateway"],
            "useful_files": ["agent_zero/model_client.py"],
            "success": True,
        }
    ]

    decision = decide_context_files(
        files=files,
        task="Explain bedrock gateway",
        search_results=[],
        max_snippets=2,
        memory_records=memory_records,
    )

    assert decision.memory_used is True
    assert decision.selected_files == ["agent_zero/model_client.py"]
    assert (
        "memory boost from similar successful task +4"
        in decision.reasons["agent_zero/model_client.py"]
    )


def test_decide_context_files_penalizes_tests_for_non_test_tasks():
    files = [
        "agent_zero/model_client.py",
        "tests/test_model_client.py",
    ]
    repo_index = {
        "files": [
            {
                "path": "agent_zero/model_client.py",
                "summary": "Handles bedrock gateway calls.",
                "concepts": ["bedrock", "gateway"],
                "symbols": ["BedrockGatewayClient"],
            },
            {
                "path": "tests/test_model_client.py",
                "summary": "Tests bedrock gateway calls.",
                "concepts": ["bedrock", "gateway"],
                "symbols": ["test_bedrock_gateway"],
            },
        ],
        "relationships": [],
    }

    decision = decide_context_files(
        files=files,
        task="Explain bedrock gateway",
        search_results=[
            "agent_zero/model_client.py:1: BedrockGatewayClient",
            "tests/test_model_client.py:1: BedrockGatewayClient",
        ],
        max_snippets=2,
        repo_index=repo_index,
    )

    assert decision.selected_files[0] == "agent_zero/model_client.py"
    assert "implementation file boost" in decision.reasons["agent_zero/model_client.py"]
    assert (
        "test file penalty for non-test task"
        in decision.reasons["tests/test_model_client.py"]
    )


def test_decide_context_files_selects_non_tests_before_tests_for_explanations():
    files = [
        ".env.example",
        "README.md",
        "agent_zero/model_client.py",
        "tests/test_config.py",
        "tests/test_context.py",
        "tests/test_model_client.py",
    ]
    repo_index = {
        "files": [
            {
                "path": "tests/test_model_client.py",
                "summary": "Tests bedrock gateway behavior.",
                "concepts": ["bedrock", "gateway"],
                "symbols": ["test_bedrock_gateway"],
            },
            {
                "path": "agent_zero/model_client.py",
                "summary": "Implements bedrock gateway behavior.",
                "concepts": ["bedrock", "gateway"],
                "symbols": ["BedrockGatewayClient"],
            },
        ],
        "relationships": [],
    }
    search_results = [
        "tests/test_model_client.py:1: bedrock gateway",
        "tests/test_context.py:1: bedrock",
        "tests/test_config.py:1: bedrock",
        "agent_zero/model_client.py:1: bedrock gateway",
        "README.md:1: bedrock gateway",
        ".env.example:1: AGENT_ZERO_PROVIDER=bedrock",
    ]

    decision = decide_context_files(
        files=files,
        task="Explain bedrock gateway",
        search_results=search_results,
        max_snippets=4,
        repo_index=repo_index,
    )

    assert decision.selected_files == [
        "agent_zero/model_client.py",
        "README.md",
        ".env.example",
        "tests/test_model_client.py",
    ]
