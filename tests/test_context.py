from agent_zero.context import build_repository_context, decide_context_files
from agent_zero.memory import write_memory_candidate


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
    assert "Relevance guide:" in prompt
    assert (
        "- README.md: primary evidence from included content; likely project overview file"
        in prompt
    )
    assert "overview prior" in prompt


def test_build_repository_context_applies_context_budget(tmp_path):
    (tmp_path / "README.md").write_text("a" * 120, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    context = build_repository_context(
        tmp_path,
        "What does this project do?",
        context_budget_tokens=10,
    )
    prompt = context.to_prompt()

    assert context.decision.context_budget_tokens == 10
    assert context.decision.context_content_tokens == 10
    assert context.decision.included_files == ["README.md"]
    assert context.decision.truncated_files == ["README.md"]
    assert context.decision.skipped_files == ["pyproject.toml"]
    assert context.snippets[0].content == "a" * 40
    assert "Context budget: 10 tokens" in prompt
    assert "Selected content: ~10 tokens" in prompt
    assert "Included content files: README.md" in prompt
    assert "Selected but content skipped: pyproject.toml" in prompt
    assert (
        "- pyproject.toml: relevance signal only because content was skipped;" in prompt
    )
    assert "likely project overview file" in prompt
    assert "Truncated files: README.md" in prompt
    assert "Skipped files: pyproject.toml" in prompt
    assert "Included selected file contents:" in prompt


def test_build_repository_context_allows_large_file_to_use_remaining_budget(tmp_path):
    content = "a" * 7000
    (tmp_path / "README.md").write_text(content, encoding="utf-8")

    context = build_repository_context(
        tmp_path,
        "Update README.md",
        context_budget_tokens=2000,
    )

    assert context.decision.included_files == ["README.md"]
    assert context.decision.context_content_tokens == 1750
    assert context.decision.truncated_files == []
    assert context.decision.focused_files == []
    assert context.snippets[0].content == content


def test_build_repository_context_uses_focused_excerpts(tmp_path):
    content = "\n".join(
        [
            "intro line that should be omitted",
            "more unrelated setup",
            "more unrelated setup",
            "more unrelated setup",
            "more unrelated setup",
            "more unrelated setup",
            "more unrelated setup",
            "class BedrockGatewayClient:",
            "    def poll(self):",
            "        return gateway_response",
            "tail line that should be omitted",
        ]
    )
    (tmp_path / "README.md").write_text(content, encoding="utf-8")

    context = build_repository_context(
        tmp_path,
        "Explain Bedrock gateway",
        context_budget_tokens=40,
    )
    prompt = context.to_prompt()

    assert context.decision.truncated_files == ["README.md"]
    assert context.decision.focused_files == ["README.md"]
    assert context.decision.included_files == ["README.md"]
    assert "class BedrockGatewayClient:" in context.snippets[0].content
    assert "intro line that should be omitted" not in context.snippets[0].content
    assert "### README.md (focused excerpt)" in prompt
    assert "Focused files: README.md" in prompt
    assert (
        "- README.md: primary evidence from focused included content; "
        "matched repository search results"
    ) in prompt


def test_build_repository_context_loads_confirmed_sqlite_memory(tmp_path):
    package = tmp_path / "agent_zero"
    package.mkdir()
    (package / "model_client.py").write_text(
        "class BedrockGatewayClient:\n    pass\n",
        encoding="utf-8",
    )
    write_memory_candidate(
        tmp_path,
        {
            "mode": "code",
            "task_terms": ["bedrock", "gateway"],
            "changed_files": ["agent_zero/model_client.py"],
            "status": "validation_passed",
            "success": True,
            "validation_passed": True,
        },
    )

    context = build_repository_context(tmp_path, "Explain bedrock gateway")

    assert context.decision.sqlite_memory_used is True
    assert context.decision.selected_files == ["agent_zero/model_client.py"]
    assert (
        "sqlite memory boost from confirmed lesson +12"
        in context.decision.reasons["agent_zero/model_client.py"]
    )


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


def test_decide_context_files_narrows_documentation_target_edits():
    files = [
        "README.md",
        "agent_zero/context.py",
        "agent_zero/tools/file_tools.py",
        "docs/high-level-design.md",
    ]
    search_results = [
        "README.md:1: Agent Zero",
        "agent_zero/context.py:10: README note",
        "agent_zero/tools/file_tools.py:20: note",
        "docs/high-level-design.md:30: README",
    ]

    decision = decide_context_files(
        files=files,
        task="Add a short README note",
        search_results=search_results,
        max_snippets=6,
    )

    assert decision.target_files == ["README.md"]
    assert decision.selected_files == ["README.md"]
    assert "explicit target file" in decision.reasons["README.md"]


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
        "index related via imports: agent_zero/model_client.py +2"
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


def test_decide_context_files_uses_confirmed_sqlite_memory():
    files = [
        "README.md",
        "agent_zero/model_client.py",
    ]
    memory_items = [
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
            "useful_files": ["README.md"],
        },
    ]

    decision = decide_context_files(
        files=files,
        task="Explain bedrock gateway",
        search_results=[],
        max_snippets=2,
        memory_items=memory_items,
    )

    assert decision.sqlite_memory_used is True
    assert decision.selected_files == ["agent_zero/model_client.py"]
    assert (
        "sqlite memory boost from confirmed lesson +12"
        in decision.reasons["agent_zero/model_client.py"]
    )
    assert "README.md" not in decision.reasons


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


def test_relationship_only_files_do_not_crowd_out_direct_matches():
    files = [
        ".env.example",
        "README.md",
        "agent_zero/cli.py",
        "agent_zero/config.py",
        "agent_zero/model_client.py",
        "agent_zero/usage.py",
        "tests/test_model_client.py",
    ]
    repo_index = {
        "files": [
            {
                "path": "agent_zero/model_client.py",
                "summary": "Implements asynchronous Bedrock gateway calls.",
                "concepts": ["bedrock", "gateway"],
                "symbols": ["BedrockGatewayClient"],
            },
            {
                "path": "README.md",
                "summary": "Documents Bedrock gateway setup.",
                "concepts": ["bedrock", "gateway"],
                "symbols": ["AGENT_ZERO_PROVIDER"],
            },
            {
                "path": ".env.example",
                "summary": "Shows Bedrock gateway environment variables.",
                "concepts": ["bedrock", "gateway"],
                "symbols": ["AGENT_ZERO_BEDROCK_URL"],
            },
            {
                "path": "agent_zero/config.py",
                "summary": "Loads provider configuration.",
                "concepts": ["bedrock"],
                "symbols": ["AgentConfig"],
            },
            {
                "path": "agent_zero/cli.py",
                "summary": "Wires CLI commands to model clients.",
                "concepts": ["bedrock"],
                "symbols": ["ask"],
            },
        ],
        "relationships": [
            {
                "from": "agent_zero/usage.py",
                "to": "agent_zero/config.py",
                "type": "imports",
            },
            {
                "from": "agent_zero/usage.py",
                "to": "agent_zero/model_client.py",
                "type": "mentions",
            },
        ],
    }
    search_results = [
        "agent_zero/model_client.py:70: class BedrockGatewayClient:",
        "README.md:120: Bedrock gateway",
        ".env.example:7: AGENT_ZERO_PROVIDER=bedrock",
        "agent_zero/config.py:20: bedrock_url",
        "agent_zero/cli.py:30: provider bedrock",
        "tests/test_model_client.py:80: bedrock gateway",
    ]

    decision = decide_context_files(
        files=files,
        task="Explain Bedrock gateway",
        search_results=search_results,
        max_snippets=6,
        repo_index=repo_index,
    )

    assert decision.selected_files[0] == "agent_zero/model_client.py"
    assert set(decision.selected_files[:-1]) == {
        ".env.example",
        "README.md",
        "agent_zero/cli.py",
        "agent_zero/config.py",
        "agent_zero/model_client.py",
    }
    assert decision.selected_files[-1] == "tests/test_model_client.py"
    assert "agent_zero/usage.py" not in decision.selected_files
