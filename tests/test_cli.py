import json

import pytest
from typer.testing import CliRunner

from agent_zero import cli
from agent_zero.cli import app
from agent_zero.memory import (
    append_memory_record,
    load_memory,
    load_memory_items,
    write_memory_candidate,
)
from agent_zero.tools.command_tool import CommandResult
from agent_zero.model_client import ModelClientError, ModelResponse


class FakeModelClient:
    def __init__(self, response: ModelResponse):
        self.response = response
        self.calls = []

    def complete(self, system_prompt: str, user_prompt: str) -> ModelResponse:
        self.calls.append((system_prompt, user_prompt))
        return self.response


class SequenceModelClient:
    def __init__(self, responses: list[ModelResponse]):
        self.responses = responses
        self.calls = []

    def complete(self, system_prompt: str, user_prompt: str) -> ModelResponse:
        self.calls.append((system_prompt, user_prompt))
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def disable_memory_writes(monkeypatch):
    monkeypatch.setenv("AGENT_ZERO_DISABLE_MEMORY", "1")


def test_ask_command_calls_model_and_prints_response(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
            ]
        ),
        encoding="utf-8",
    )

    fake_client = FakeModelClient(
        ModelResponse(
            content="Agent Zero is a learning project.",
            input_tokens=10,
            output_tokens=8,
            total_tokens=18,
        )
    )
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["ask", "What does this project do?", "--env-file", str(env_file)],
    )

    assert result.exit_code == 0
    assert "Agent Zero is a learning project." in result.output
    assert "Tokens: input=10, output=8, total=18" in result.output
    assert len(fake_client.calls) == 1
    system_prompt, user_prompt = fake_client.calls[0]
    assert system_prompt == cli.ASK_SYSTEM_PROMPT
    assert "Use the relevance guide to explain why files matter." in system_prompt
    assert "Distinguish included file contents" in system_prompt
    assert "User question:\nWhat does this project do?" in user_prompt
    assert "Repository context:" in user_prompt
    assert "Evidence boundary:" in user_prompt
    assert "Relevance guide:" in user_prompt
    assert "README.md" in user_prompt


def test_classify_command_prints_human_readable_result():
    runner = CliRunner()
    result = runner.invoke(app, ["classify", "Explain Bedrock gateway"])

    assert result.exit_code == 0
    assert "Action type: read" in result.output
    assert "Recommended mode: ask" in result.output
    assert "Subcategory: explain_code" in result.output
    assert "Write intent: none" in result.output
    assert "Specificity: high" in result.output
    assert "Requires clarification: False" in result.output
    assert "Confidence: high" in result.output


def test_classify_command_prints_missing_information_for_vague_code_request():
    runner = CliRunner()
    result = runner.invoke(app, ["classify", "Add a short README note"])

    assert result.exit_code == 0
    assert "Action type: write" in result.output
    assert "Recommended mode: code" in result.output
    assert "Subcategory: documentation_edit" in result.output
    assert "Requires clarification: True" in result.output
    assert "Missing information:" in result.output
    assert "- exact documentation text or topic" in result.output


def test_classify_command_prints_json():
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["classify", "Plan architecture for hybrid memory", "--json"],
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["action_type"] == "plan"
    assert data["recommended_mode"] == "plan"
    assert data["subcategory"] == "architecture_plan"
    assert data["write_intent"] == "none"
    assert data["requires_clarification"] is False


def test_ask_command_show_context_prints_selection_reasons(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# Agent Zero\n", encoding="utf-8")
    fake_client = FakeModelClient(ModelResponse(content="ok"))
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "ask",
            "What does this project do?",
            "--show-context",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    assert "Context selection:" in result.output
    assert "Query terms: this, project" in result.output
    assert "Repo index: not found" in result.output
    assert "Learning memory: not found" in result.output
    assert "SQLite memory: not found" in result.output
    assert "- README.md: overview prior" in result.output
    assert "ok" in result.output


def test_ask_command_prints_trace_json(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
                "AGENT_ZERO_INPUT_COST_PER_1M_TOKENS=1.0",
                "AGENT_ZERO_OUTPUT_COST_PER_1M_TOKENS=2.0",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# Agent Zero\n", encoding="utf-8")
    fake_client = FakeModelClient(
        ModelResponse(
            content="ok",
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
        )
    )
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "ask",
            "What does this project do?",
            "--trace-json",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    assert "Trace JSON:" in result.output
    trace = json.loads(result.output.split("Trace JSON:\n", maxsplit=1)[1])
    assert trace["mode"] == "ask"
    assert trace["task"] == "What does this project do?"
    assert trace["status"] == "ask_completed"
    assert trace["success"] is True
    assert trace["provider"] == "openai"
    assert trace["model"] == "test-model"
    assert trace["context"]["selected_files"] == ["README.md"]
    assert trace["context"]["context_budget_tokens"] == 8000
    assert trace["model_calls"][0]["purpose"] == "initial"
    assert trace["model_calls"][0]["usage"]["input_tokens"] == 10
    assert trace["model_calls"][0]["usage"]["estimated_cost"] == "$0.000020"
    assert trace["changed_files"] == []
    assert trace["validation"] is None
    assert [call["name"] for call in trace["tool_calls"]] == [
        "load_config",
        "build_repository_context",
        "model.complete",
        "record_memory",
    ]
    assert trace["tool_calls"][2]["status"] == "success"
    assert all(isinstance(call["duration_ms"], float) for call in trace["tool_calls"])


def test_ask_command_accepts_context_budget(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("a" * 120, encoding="utf-8")
    fake_client = FakeModelClient(ModelResponse(content="ok"))
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "ask",
            "What does this project do?",
            "--context-budget",
            "10",
            "--show-context",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    assert "Context budget: 10 tokens" in result.output
    assert "Selected content: ~10 tokens" in result.output
    assert "Included content files: README.md" in result.output
    assert "### README.md (truncated)" in fake_client.calls[0][1]


def test_ask_command_trace_prints_agent_timeline(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# Agent Zero\n", encoding="utf-8")
    fake_client = FakeModelClient(ModelResponse(content="ok"))
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "ask",
            "What does this project do?",
            "--trace",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    assert "Agent trace:" in result.output
    assert "1. Loaded config: provider=openai, model=test-model" in result.output
    assert "2. Listed repository files: 1" in result.output
    assert "3. Searched repository text:" in result.output
    assert "4. Loaded repo index: not found" in result.output
    assert "5. Loaded learning memory: not found" in result.output
    assert "6. Loaded SQLite memory: not found" in result.output
    assert "7. Selected files: README.md" in result.output
    assert "8. Included content files: README.md" in result.output
    assert "9. Applied context budget:" in result.output
    assert "11. Focused excerpts: (none)" in result.output
    assert "13. Prepared ask prompt and called model" in result.output
    assert "Agent trace debug:" not in result.output
    assert "ok" in result.output


def test_ask_command_trace_level_debug_prints_detailed_trace(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# Agent Zero\n", encoding="utf-8")
    fake_client = FakeModelClient(ModelResponse(content="ok"))
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "ask",
            "What does this project do?",
            "--trace-level",
            "debug",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    assert "Agent trace:" in result.output
    assert "Agent trace debug:" in result.output
    assert "- Selected file reasons:" in result.output
    assert "  - README.md:" in result.output
    assert "- Included content sizes:" in result.output
    assert "ok" in result.output


def test_ask_command_rejects_invalid_trace_level(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "ask",
            "What does this project do?",
            "--trace-level",
            "verbose",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 2
    assert "Invalid trace level. Use one of: none, basic, debug." in result.output


def test_ask_command_prints_estimated_cost_when_prices_are_configured(
    tmp_path, monkeypatch
):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
                "AGENT_ZERO_INPUT_COST_PER_1M_TOKENS=1.0",
                "AGENT_ZERO_OUTPUT_COST_PER_1M_TOKENS=2.0",
            ]
        ),
        encoding="utf-8",
    )

    fake_client = FakeModelClient(
        ModelResponse(
            content="Agent Zero is a learning project.",
            input_tokens=1000,
            output_tokens=2000,
            total_tokens=3000,
        )
    )
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["ask", "What does this project do?", "--env-file", str(env_file)],
    )

    assert result.exit_code == 0
    assert "Tokens: input=1000, output=2000, total=3000" in result.output
    assert "Estimated cost: $0.005000" in result.output


def test_ask_command_prints_partial_token_usage(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
            ]
        ),
        encoding="utf-8",
    )

    fake_client = FakeModelClient(
        ModelResponse(
            content="ok",
            input_tokens=1000,
            output_tokens=2000,
        )
    )
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["ask", "What does this project do?", "--env-file", str(env_file)],
    )

    assert result.exit_code == 0
    assert "Tokens: input=1000, output=2000, total=3000" in result.output


def test_ask_command_estimates_usage_when_provider_usage_is_missing(
    tmp_path, monkeypatch
):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
                "AGENT_ZERO_INPUT_COST_PER_1M_TOKENS=1.0",
                "AGENT_ZERO_OUTPUT_COST_PER_1M_TOKENS=2.0",
            ]
        ),
        encoding="utf-8",
    )

    fake_client = FakeModelClient(ModelResponse(content="ok"))
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["ask", "What does this project do?", "--env-file", str(env_file)],
    )

    assert result.exit_code == 0
    assert "Estimated tokens: input=" in result.output
    assert "Estimated cost: $" in result.output


def test_ask_command_records_learning_memory(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_ZERO_DISABLE_MEMORY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# Agent Zero\n", encoding="utf-8")

    fake_client = FakeModelClient(
        ModelResponse(
            content="Agent Zero is a learning project.",
            input_tokens=10,
            output_tokens=8,
            total_tokens=18,
        )
    )
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["ask", "What does this project do?", "--env-file", str(env_file)],
    )

    assert result.exit_code == 0
    memory_file = tmp_path / ".agent-zero" / "memory.jsonl"
    records = [
        json.loads(line)
        for line in memory_file.read_text(encoding="utf-8").splitlines()
    ]
    assert records[-1]["mode"] == "ask"
    assert records[-1]["task_terms"] == ["project"]
    assert records[-1]["selected_files"] == ["README.md"]
    assert records[-1]["useful_files"] == []
    assert records[-1]["success"] is True
    assert records[-1]["usage"]["total_tokens"] == 18
    assert records[-1]["reflection"] == {
        "lesson": "ask run succeeded, but no reusable file signal was recorded.",
        "task_terms": ["project"],
        "useful_files": [],
        "confidence": "low",
    }
    memory_items = load_memory_items(tmp_path)
    assert len(memory_items) == 1
    assert memory_items[0]["type"] == "interaction_observation"
    assert memory_items[0]["status"] == "candidate"
    assert memory_items[0]["confidence"] == "low"


def test_ask_command_reports_model_errors(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
            ]
        ),
        encoding="utf-8",
    )

    class FailingModelClient:
        def complete(self, system_prompt: str, user_prompt: str) -> ModelResponse:
            raise ModelClientError("boom")

    monkeypatch.setattr(cli, "create_model_client", lambda config: FailingModelClient())

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["ask", "What does this project do?", "--env-file", str(env_file)],
    )

    assert result.exit_code == 1
    assert "Model call failed: boom" in result.output


def test_plan_command_calls_model_and_prints_structured_plan(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
            ]
        ),
        encoding="utf-8",
    )

    fake_client = FakeModelClient(
        ModelResponse(
            content="1. Summary\nPlan config loading.",
            input_tokens=20,
            output_tokens=12,
            total_tokens=32,
        )
    )
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["plan", "Add config loading", "--env-file", str(env_file)],
    )

    assert result.exit_code == 0
    assert "1. Summary" in result.output
    assert "Tokens: input=20, output=12, total=32" in result.output
    assert len(fake_client.calls) == 1
    system_prompt, user_prompt = fake_client.calls[0]
    assert system_prompt == cli.PLAN_SYSTEM_PROMPT
    assert "Use the relevance guide to explain why files matter." in system_prompt
    assert "Change request:\nAdd config loading" in user_prompt
    assert "Repository context:" in user_prompt
    assert "Relevance guide:" in user_prompt


def test_plan_command_show_context_prints_selection_reasons(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# Agent Zero\n", encoding="utf-8")
    fake_client = FakeModelClient(ModelResponse(content="1. Summary"))
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "plan",
            "Add README details",
            "--show-context",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    assert "Context selection:" in result.output
    assert "Query terms: add, readme, details" in result.output
    assert "- README.md:" in result.output
    assert "1. Summary" in result.output


def test_plan_command_prints_trace_json(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# Agent Zero\n", encoding="utf-8")
    fake_client = FakeModelClient(ModelResponse(content="1. Summary"))
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "plan",
            "Plan README update",
            "--trace-json",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    trace = json.loads(result.output.split("Trace JSON:\n", maxsplit=1)[1])
    assert trace["mode"] == "plan"
    assert trace["task"] == "Plan README update"
    assert trace["status"] == "plan_completed"
    assert trace["success"] is True
    assert trace["context"]["selected_files"] == ["README.md"]
    assert trace["model_calls"][0]["purpose"] == "initial"
    assert [call["name"] for call in trace["tool_calls"]] == [
        "load_config",
        "build_repository_context",
        "model.complete",
        "record_memory",
    ]
    assert all(isinstance(call["duration_ms"], float) for call in trace["tool_calls"])


def test_plan_command_trace_prints_agent_timeline(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# Agent Zero\n", encoding="utf-8")
    fake_client = FakeModelClient(ModelResponse(content="1. Summary"))
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "plan",
            "Add README details",
            "--trace",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    assert "Agent trace:" in result.output
    assert "1. Loaded config: provider=openai, model=test-model" in result.output
    assert "13. Prepared plan prompt and called model" in result.output
    assert "1. Summary" in result.output


def test_plan_command_reports_model_errors(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
            ]
        ),
        encoding="utf-8",
    )

    class FailingModelClient:
        def complete(self, system_prompt: str, user_prompt: str) -> ModelResponse:
            raise ModelClientError("plan failed")

    monkeypatch.setattr(cli, "create_model_client", lambda config: FailingModelClient())

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["plan", "Add config loading", "--env-file", str(env_file)],
    )

    assert result.exit_code == 1
    assert "Model call failed: plan failed" in result.output


def test_plan_command_reports_missing_config(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["plan", "Add config loading", "--env-file", str(env_file)],
        env={
            "AGENT_ZERO_BASE_URL": "",
            "AGENT_ZERO_API_KEY": "",
            "AGENT_ZERO_MODEL": "",
        },
    )

    assert result.exit_code == 2
    assert "Missing required configuration" in result.output


def test_index_command_writes_repo_index(tmp_path, monkeypatch):
    (tmp_path / "README.md").write_text("# Agent Zero\n", encoding="utf-8")
    package = tmp_path / "agent_zero"
    package.mkdir()
    (package / "config.py").write_text(
        "def load_config():\n    return None\n",
        encoding="utf-8",
    )
    output = tmp_path / "custom-index.json"
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(app, ["index", "--output", str(output)])

    assert result.exit_code == 0
    assert "Index written:" in result.output
    assert "Files indexed: 2" in result.output
    data = json.loads(output.read_text(encoding="utf-8"))
    assert [entry["path"] for entry in data["files"]] == [
        "README.md",
        "agent_zero/config.py",
    ]


def test_memory_command_prints_empty_summary(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(app, ["memory"])

    assert result.exit_code == 0
    assert "Raw memory records: 0" in result.output
    assert "SQLite memory items: 0" in result.output
    assert "Confirmed:\n- (none)" in result.output
    assert "Candidates:\n- (none)" in result.output
    assert "Rejected:\n- (none)" in result.output


def test_memory_command_groups_raw_and_curated_memory(tmp_path, monkeypatch):
    append_memory_record(
        tmp_path,
        {
            "mode": "ask",
            "task_terms": ["project"],
            "selected_files": ["README.md"],
            "useful_files": [],
            "status": "ask_completed",
            "success": True,
        },
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
    write_memory_candidate(
        tmp_path,
        {
            "mode": "ask",
            "task_terms": ["project"],
            "status": "ask_completed",
            "success": True,
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
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(app, ["memory"])

    assert result.exit_code == 0
    assert "Raw memory records: 1" in result.output
    assert "SQLite memory items: 3" in result.output
    assert "Confirmed:" in result.output
    assert (
        "[high] code task with terms bedrock, gateway used agent_zero/model_client.py."
        in result.output
    )
    assert "files: agent_zero/model_client.py" in result.output
    assert "Candidates:" in result.output
    assert (
        "[low] ask task with terms project completed without reusable file evidence."
        in result.output
    )
    assert "Rejected:" in result.output
    assert "[low] code task with terms readme ended with patch_failed." in result.output


def test_memory_command_filters_by_status(tmp_path, monkeypatch):
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
            "mode": "ask",
            "task_terms": ["project"],
            "status": "ask_completed",
            "success": True,
        },
    )
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(app, ["memory", "--status", "confirmed"])

    assert result.exit_code == 0
    assert "SQLite memory items: 1" in result.output
    assert "Confirmed:" in result.output
    assert "Candidates:" not in result.output
    assert "Rejected:" not in result.output
    assert "agent_zero/model_client.py" in result.output
    assert "without reusable file evidence" not in result.output


def test_memory_command_prints_json(tmp_path, monkeypatch):
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
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(app, ["memory", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["raw_memory_records"] == 0
    assert data["sqlite_memory_items"] == 1
    assert data["items"][0]["status"] == "confirmed"


def test_memory_command_prune_rejected_dry_run_does_not_delete(tmp_path, monkeypatch):
    write_memory_candidate(
        tmp_path,
        {
            "mode": "code",
            "task_terms": ["readme"],
            "status": "patch_failed",
            "success": False,
        },
    )
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(app, ["memory", "--prune"])

    assert result.exit_code == 0
    assert "Dry run: no memory deleted." in result.output
    assert "Prunable rejected memory items: 1" in result.output
    assert "Re-run with --yes" in result.output
    assert load_memory_items(tmp_path)[0]["status"] == "rejected"


def test_memory_command_prune_rejected_with_yes_deletes_only_rejected(
    tmp_path, monkeypatch
):
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
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(app, ["memory", "--prune", "--yes"])

    assert result.exit_code == 0
    assert "Deleted rejected memory items: 1" in result.output
    assert "Confirmed memory kept." in result.output
    items = load_memory_items(tmp_path)
    assert len(items) == 1
    assert items[0]["status"] == "confirmed"


def test_memory_command_refuses_to_prune_confirmed(tmp_path, monkeypatch):
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
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(app, ["memory", "--prune", "--status", "confirmed", "--yes"])

    assert result.exit_code == 2
    assert "Refusing to prune confirmed memory" in result.output
    assert load_memory_items(tmp_path)[0]["status"] == "confirmed"


def test_memory_command_reset_dry_run_does_not_delete(tmp_path, monkeypatch):
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
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(app, ["memory", "--reset"])

    assert result.exit_code == 0
    assert "Dry run: no memory reset." in result.output
    assert "SQLite memory items that would be deleted: 1" in result.output
    assert "Raw JSONL audit log would be kept." in result.output
    assert len(load_memory_items(tmp_path)) == 1


def test_memory_command_reset_with_yes_deletes_sqlite_only(tmp_path, monkeypatch):
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
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(app, ["memory", "--reset", "--yes"])

    assert result.exit_code == 0
    assert "Deleted SQLite memory items: 1" in result.output
    assert "Raw JSONL audit log kept." in result.output
    assert load_memory_items(tmp_path) == []
    assert len(load_memory(tmp_path)) == 1


def test_memory_command_reset_include_raw_deletes_jsonl(tmp_path, monkeypatch):
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
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(app, ["memory", "--reset", "--include-raw", "--yes"])

    assert result.exit_code == 0
    assert "Deleted SQLite memory items: 0" in result.output
    assert "Deleted raw memory records: 1" in result.output
    assert load_memory(tmp_path) == []


def test_memory_command_reset_json_dry_run(tmp_path, monkeypatch):
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
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(app, ["memory", "--reset", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["action"] == "reset"
    assert data["dry_run"] is True
    assert data["sqlite_items"] == 1
    assert data["deleted_sqlite_items"] == 0


def test_memory_command_feedback_worked_promotes_latest_item(tmp_path, monkeypatch):
    write_memory_candidate(
        tmp_path,
        {
            "mode": "ask",
            "task_terms": ["bedrock"],
            "status": "ask_completed",
            "success": True,
        },
    )
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(app, ["memory", "--feedback", "worked"])

    assert result.exit_code == 0
    assert "Applied feedback: worked" in result.output
    assert "Updated memory status: confirmed" in result.output
    item = load_memory_items(tmp_path)[0]
    assert item["status"] == "confirmed"
    assert item["confidence"] == "high"


def test_memory_command_feedback_failed_with_status_filter(tmp_path, monkeypatch):
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
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["memory", "--status", "confirmed", "--feedback", "failed"],
    )

    assert result.exit_code == 0
    assert "Applied feedback: failed" in result.output
    assert "Updated memory status: rejected" in result.output
    item = load_memory_items(tmp_path)[0]
    assert item["status"] == "rejected"
    assert item["confidence"] == "low"


def test_memory_command_feedback_prints_json(tmp_path, monkeypatch):
    write_memory_candidate(
        tmp_path,
        {
            "mode": "ask",
            "task_terms": ["bedrock"],
            "status": "ask_completed",
            "success": True,
        },
    )
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(app, ["memory", "--feedback", "worked", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["action"] == "feedback"
    assert data["feedback"] == "worked"
    assert data["updated"] is True
    assert data["item"]["status"] == "confirmed"


def test_memory_command_rejects_feedback_with_prune(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(app, ["memory", "--feedback", "worked", "--prune"])

    assert result.exit_code == 2
    assert "--feedback cannot be combined" in result.output


def test_memory_command_detect_feedback_dry_run(tmp_path, monkeypatch):
    write_memory_candidate(
        tmp_path,
        {
            "mode": "ask",
            "task_terms": ["bedrock"],
            "status": "ask_completed",
            "success": True,
        },
    )
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(app, ["memory", "--detect-feedback", "it worked"])

    assert result.exit_code == 0
    assert "Detected feedback: worked" in result.output
    assert "Dry run: no memory updated." in result.output
    assert load_memory_items(tmp_path)[0]["status"] == "candidate"


def test_memory_command_detect_feedback_with_yes_applies_feedback(
    tmp_path, monkeypatch
):
    write_memory_candidate(
        tmp_path,
        {
            "mode": "ask",
            "task_terms": ["bedrock"],
            "status": "ask_completed",
            "success": True,
        },
    )
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["memory", "--detect-feedback", "it worked", "--yes"],
    )

    assert result.exit_code == 0
    assert "Detected feedback: worked" in result.output
    assert "Updated memory status: confirmed" in result.output
    assert load_memory_items(tmp_path)[0]["status"] == "confirmed"


def test_memory_command_detect_feedback_json_no_match(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["memory", "--detect-feedback", "move to next task", "--json"],
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["action"] == "detect_feedback"
    assert data["detected_feedback"] is None
    assert data["updated"] is False


def test_eval_command_runs_ask_eval_and_writes_result(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
                "AGENT_ZERO_INPUT_COST_PER_1M_TOKENS=1.0",
                "AGENT_ZERO_OUTPUT_COST_PER_1M_TOKENS=2.0",
            ]
        ),
        encoding="utf-8",
    )
    spec_file = tmp_path / "ask-project.json"
    spec_file.write_text(
        json.dumps(
            {
                "name": "ask-project",
                "mode": "ask",
                "task": "What does this project do?",
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "eval-results"
    fake_client = FakeModelClient(
        ModelResponse(
            content="Agent Zero is a learning project.",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
        )
    )
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            str(spec_file),
            "--output-dir",
            str(output_dir),
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    assert "Eval: ask-project" in result.output
    assert "Success: True" in result.output

    result_files = list(output_dir.glob("*-ask-project.json"))
    assert len(result_files) == 1
    data = json.loads(result_files[0].read_text(encoding="utf-8"))
    assert data["name"] == "ask-project"
    assert data["mode"] == "ask"
    assert data["success"] is True
    assert data["status"] == "ask_completed"
    assert data["model_calls"][0]["usage"]["input_tokens"] == 100
    assert data["model_calls"][0]["usage"]["estimated_cost"] == "$0.000200"


def test_eval_command_runs_ad_hoc_ask_eval_and_writes_result(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
                "AGENT_ZERO_INPUT_COST_PER_1M_TOKENS=1.0",
                "AGENT_ZERO_OUTPUT_COST_PER_1M_TOKENS=2.0",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# Agent Zero\n", encoding="utf-8")
    output_dir = tmp_path / "eval-results"
    fake_client = FakeModelClient(
        ModelResponse(
            content="Bedrock gateway uses polling with AWS SDK.",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
        )
    )
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--mode",
            "ask",
            "Explain Bedrock gateway",
            "--expect",
            "polling",
            "--expect",
            "Bedrock",
            "--forbid",
            "AWS SDK",
            "--show-context",
            "--context-budget",
            "10",
            "--output-dir",
            str(output_dir),
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    assert "Context selection:" in result.output
    assert "Context budget: 10 tokens" in result.output
    assert "Eval: ad-hoc-ask-explain-bedrock-gateway" in result.output
    assert "Success: True" in result.output
    assert "Score: 2/3 (passed=False)" in result.output

    result_files = list(output_dir.glob("*-ad-hoc-ask-explain-bedrock-gateway.json"))
    assert len(result_files) == 1
    data = json.loads(result_files[0].read_text(encoding="utf-8"))
    assert data["name"] == "ad-hoc-ask-explain-bedrock-gateway"
    assert data["mode"] == "ask"
    assert data["task"] == "Explain Bedrock gateway"
    assert data["success"] is True
    assert data["status"] == "ask_completed"
    assert data["score"] == {
        "expected_terms": ["polling", "Bedrock"],
        "forbidden_terms": ["AWS SDK"],
        "missing_expected_terms": [],
        "passed": False,
        "passed_checks": 2,
        "present_forbidden_terms": ["AWS SDK"],
        "total_checks": 3,
    }


def test_eval_command_rejects_invalid_ad_hoc_mode(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--mode",
            "bad",
            "Explain Bedrock gateway",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 2
    assert "Eval mode must be one of: ask, plan, code." in result.output


def test_eval_suite_command_runs_inline_and_file_specs(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
                "AGENT_ZERO_INPUT_COST_PER_1M_TOKENS=1.0",
                "AGENT_ZERO_OUTPUT_COST_PER_1M_TOKENS=2.0",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# Agent Zero\n", encoding="utf-8")
    spec_file = tmp_path / "ask-project.json"
    spec_file.write_text(
        json.dumps(
            {
                "name": "ask-project",
                "mode": "ask",
                "task": "What does this project do?",
                "expected_terms": ["Agent Zero"],
            }
        ),
        encoding="utf-8",
    )
    suite_file = tmp_path / "core.json"
    suite_file.write_text(
        json.dumps(
            {
                "name": "core",
                "evals": [
                    "ask-project.json",
                    {
                        "name": "bedrock",
                        "mode": "ask",
                        "task": "Explain Bedrock gateway",
                        "expected_terms": ["Agent Zero"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "eval-results"
    fake_client = FakeModelClient(
        ModelResponse(
            content="Agent Zero is a learning project.",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
        )
    )
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval-suite",
            str(suite_file),
            "--output-dir",
            str(output_dir),
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    assert "Eval suite: core" in result.output
    assert "Total: 2" in result.output
    assert "Passed: 2" in result.output
    assert "Failed: 0" in result.output
    assert "Total tokens: 300" in result.output
    suite_files = list((output_dir / "suites").glob("*-core.json"))
    assert len(suite_files) == 1
    suite_data = json.loads(suite_files[0].read_text(encoding="utf-8"))
    assert suite_data["success"] is True
    assert suite_data["total_tokens"] == 300
    assert suite_data["estimated_cost"] == "$0.000400"
    assert all(item["run_success"] is True for item in suite_data["evals"])
    assert all(item["score_passed"] is True for item in suite_data["evals"])
    assert [item["name"] for item in suite_data["evals"]] == [
        "ask-project",
        "bedrock",
    ]
    assert len(list(output_dir.glob("*.json"))) == 2


def test_eval_suite_command_treats_score_failure_as_failed_eval(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# Agent Zero\n", encoding="utf-8")
    suite_file = tmp_path / "core.json"
    suite_file.write_text(
        json.dumps(
            {
                "name": "core",
                "evals": [
                    {
                        "name": "bedrock",
                        "mode": "ask",
                        "task": "Explain Bedrock gateway",
                        "expected_terms": ["Bedrock"],
                        "forbidden_terms": ["AWS SDK"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "eval-results"
    fake_client = FakeModelClient(
        ModelResponse(
            content="Bedrock gateway does not use AWS SDK here.",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
        )
    )
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval-suite",
            str(suite_file),
            "--output-dir",
            str(output_dir),
            "--env-file",
            str(env_file),
        ],
    )
    allowed_result = runner.invoke(
        app,
        [
            "eval-suite",
            str(suite_file),
            "--output-dir",
            str(output_dir),
            "--env-file",
            str(env_file),
            "--allow-failures",
        ],
    )

    assert result.exit_code == 1
    assert "Passed: 0" in result.output
    assert "Failed: 1" in result.output
    assert "- bedrock (score_failed)" in result.output
    assert allowed_result.exit_code == 0
    suite_files = sorted((output_dir / "suites").glob("*-core.json"))
    suite_data = json.loads(suite_files[-1].read_text(encoding="utf-8"))
    assert suite_data["success"] is False
    assert suite_data["evals"][0]["success"] is False
    assert suite_data["evals"][0]["run_success"] is True
    assert suite_data["evals"][0]["score_passed"] is False


def test_eval_suite_command_exits_nonzero_on_failure_unless_allowed(
    tmp_path, monkeypatch
):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
            ]
        ),
        encoding="utf-8",
    )
    suite_file = tmp_path / "core.json"
    suite_file.write_text(
        json.dumps(
            {
                "name": "core",
                "evals": [
                    {
                        "name": "bad",
                        "mode": "ask",
                        "task": "Explain Bedrock gateway",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "eval-results"
    monkeypatch.setattr(
        cli,
        "_run_eval",
        lambda spec, config, context_budget_tokens, show_context: {
            "name": spec.name,
            "mode": spec.mode,
            "task": spec.task,
            "provider": config.provider,
            "model": config.model,
            "success": False,
            "status": "model_failed",
            "selected_files": [],
            "changed_files": [],
            "patch_summary": [],
            "validation": None,
            "model_calls": [],
            "response": "boom",
        },
    )
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval-suite",
            str(suite_file),
            "--output-dir",
            str(output_dir),
            "--env-file",
            str(env_file),
        ],
    )
    allowed_result = runner.invoke(
        app,
        [
            "eval-suite",
            str(suite_file),
            "--output-dir",
            str(output_dir),
            "--env-file",
            str(env_file),
            "--allow-failures",
        ],
    )

    assert result.exit_code == 1
    assert "Failed: 1" in result.output
    assert "Failed evals:" in result.output
    assert allowed_result.exit_code == 0


def test_eval_report_command_summarizes_saved_results(tmp_path):
    output_dir = tmp_path / "eval-results"
    output_dir.mkdir()
    result_file = output_dir / "20260623T170918Z-ad-hoc-ask-explain-bedrock.json"
    result_file.write_text(
        json.dumps(
            {
                "name": "ad-hoc-ask-explain-bedrock",
                "mode": "ask",
                "status": "ask_completed",
                "success": True,
                "selected_files": ["agent_zero/model_client.py", "README.md"],
                "changed_files": [],
                "score": {
                    "passed": True,
                    "passed_checks": 2,
                    "total_checks": 2,
                },
                "model_calls": [
                    {
                        "purpose": "initial",
                        "usage": {
                            "input_tokens": 100,
                            "output_tokens": 50,
                            "total_tokens": 150,
                            "estimated_cost": "$0.000200",
                        },
                    },
                    {
                        "purpose": "retry",
                        "usage": {
                            "input_tokens": 20,
                            "output_tokens": 10,
                            "total_tokens": 30,
                            "estimated_cost": "$0.000040",
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["eval-report", "--output-dir", str(output_dir)],
    )

    assert result.exit_code == 0
    assert "Eval results:" in result.output
    assert "20260623T170918Z-ad-hoc-ask-explain-bedrock.json" in result.output
    assert "name: ad-hoc-ask-explain-bedrock" in result.output
    assert "score: 2/2 passed=True" in result.output
    assert "tokens: input=120 output=60 total=180" in result.output
    assert "cost: $0.000240" in result.output
    assert "selected files: 2" in result.output
    assert "changed files: 0" in result.output


def test_eval_report_command_filters_by_name(tmp_path):
    output_dir = tmp_path / "eval-results"
    output_dir.mkdir()
    for filename, name in [
        ("20260623T170918Z-keep-this.json", "keep-this"),
        ("20260623T170917Z-skip-this.json", "skip-this"),
    ]:
        (output_dir / filename).write_text(
            json.dumps(
                {
                    "name": name,
                    "mode": "ask",
                    "status": "ask_completed",
                    "success": True,
                    "model_calls": [],
                }
            ),
            encoding="utf-8",
        )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["eval-report", "--output-dir", str(output_dir), "--name", "keep"],
    )

    assert result.exit_code == 0
    assert "name: keep-this" in result.output
    assert "skip-this" not in result.output


def test_eval_report_command_prints_json(tmp_path):
    output_dir = tmp_path / "eval-results"
    output_dir.mkdir()
    (output_dir / "20260623T170918Z-report-json.json").write_text(
        json.dumps(
            {
                "name": "report-json",
                "mode": "ask",
                "status": "ask_completed",
                "success": True,
                "selected_files": ["README.md"],
                "changed_files": [],
                "model_calls": [
                    {
                        "purpose": "initial",
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 5,
                            "total_tokens": 15,
                            "estimated_cost": "$0.000020",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["eval-report", "--output-dir", str(output_dir), "--json"],
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data == [
        {
            "changed_file_count": 0,
            "estimated_cost": "$0.000020",
            "file": "20260623T170918Z-report-json.json",
            "input_tokens": 10,
            "mode": "ask",
            "name": "report-json",
            "output_tokens": 5,
            "score": None,
            "selected_file_count": 1,
            "status": "ask_completed",
            "success": True,
            "total_tokens": 15,
        }
    ]


def test_eval_command_runs_code_eval_and_writes_validation_result(
    tmp_path, monkeypatch
):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
            ]
        ),
        encoding="utf-8",
    )
    spec_file = tmp_path / "change-text.json"
    spec_file.write_text(
        json.dumps(
            {
                "name": "change-text",
                "mode": "code",
                "task": "Change hello.txt old to new",
                "validation_command": "pytest",
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "eval-results"
    target = tmp_path / "hello.txt"
    target.write_text("old\n", encoding="utf-8")
    fake_client = FakeModelClient(
        ModelResponse(
            content="""diff --git a/hello.txt b/hello.txt
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-old
+new
""",
            input_tokens=10,
            output_tokens=10,
            total_tokens=20,
        )
    )
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.setattr(
        cli,
        "run_command",
        lambda command, cwd, timeout_seconds: CommandResult(
            command=["pytest"],
            exit_code=0,
            stdout="passed\n",
            stderr="",
        ),
    )
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            str(spec_file),
            "--output-dir",
            str(output_dir),
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    assert target.read_text(encoding="utf-8") == "new\n"

    result_files = list(output_dir.glob("*-change-text.json"))
    assert len(result_files) == 1
    data = json.loads(result_files[0].read_text(encoding="utf-8"))
    assert data["success"] is True
    assert data["status"] == "validation_passed"
    assert data["changed_files"] == ["hello.txt"]
    assert data["patch_summary"] == [
        {"path": "hello.txt", "additions": 1, "deletions": 1}
    ]
    assert data["validation"]["passed"] is True
    assert data["validation"]["command"] == ["pytest"]


def test_code_command_applies_model_diff(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
            ]
        ),
        encoding="utf-8",
    )
    target = tmp_path / "hello.txt"
    target.write_text("old\n", encoding="utf-8")

    fake_client = FakeModelClient(
        ModelResponse(
            content="""```diff
diff --git a/hello.txt b/hello.txt
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-old
+new
```""",
            input_tokens=30,
            output_tokens=20,
            total_tokens=50,
        )
    )
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["code", "Change hello.txt old to new", "--env-file", str(env_file)],
    )

    assert result.exit_code == 0
    assert "Applied patch." in result.output
    assert "- hello.txt" in result.output
    assert "Patch summary:" in result.output
    assert "- hello.txt: +1 -1" in result.output
    assert "Validation skipped" in result.output
    assert "Tokens: input=30, output=20, total=50" in result.output
    assert target.read_text(encoding="utf-8") == "new\n"
    system_prompt, user_prompt = fake_client.calls[0]
    assert system_prompt == cli.CODE_SYSTEM_PROMPT
    assert "Change request:\nChange hello.txt old to new" in user_prompt


def test_code_command_accepts_context_budget(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("a" * 120, encoding="utf-8")
    fake_client = FakeModelClient(
        ModelResponse(
            content="""diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-old
+new
"""
        )
    )
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "code",
            "Update README.md saying Agent Zero is a learning harness",
            "--context-budget",
            "10",
            "--dry-run",
            "--trace",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    assert "Applied context budget: 10 tokens" in result.output
    assert "### README.md (truncated)" in fake_client.calls[0][1]


def test_code_command_clarifies_vague_documentation_request_before_config(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["code", "Add a short README note", "--trace"])

    assert result.exit_code == 2
    assert "Code trace: Clarification needed before context selection." in result.output
    assert "Clarification needed:" in result.output
    assert "- exact documentation text or topic" in result.output
    assert "Recommended mode: code" in result.output
    assert "No model call made." in result.output
    assert "AGENT_ZERO_API_KEY" not in result.output


def test_code_command_clarifies_vague_generic_change_before_model(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["code", "Change old to new", "--trace-json"])

    assert result.exit_code == 2
    assert "Clarification needed:" in result.output
    assert "- target file or component" in result.output
    assert "No model call made." in result.output
    trace = json.loads(result.output.split("Trace JSON:\n", maxsplit=1)[1])
    assert trace["status"] == "clarification_needed"
    assert trace["classification"]["missing_information"] == [
        "target file or component"
    ]
    assert trace["model_calls"] == []
    assert [call["name"] for call in trace["tool_calls"]] == [
        "classify_task",
        "record_memory",
        "build_repository_context",
        "model.complete",
    ]
    assert trace["tool_calls"][2]["status"] == "skipped"
    assert trace["tool_calls"][3]["status"] == "skipped"
    assert all(isinstance(call["duration_ms"], float) for call in trace["tool_calls"])


def test_code_command_clarification_prints_trace_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["code", "Add a short README note", "--trace-json"],
    )

    assert result.exit_code == 2
    trace = json.loads(result.output.split("Trace JSON:\n", maxsplit=1)[1])
    assert trace["mode"] == "code"
    assert trace["status"] == "clarification_needed"
    assert trace["success"] is False
    assert trace["provider"] is None
    assert trace["context"] is None
    assert trace["classification"]["subcategory"] == "documentation_edit"
    assert trace["model_calls"] == []
    assert trace["patch_summary"] == []
    assert trace["tool_calls"][0]["name"] == "classify_task"


def test_code_command_records_clarification_needed_as_rejected_memory(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("AGENT_ZERO_DISABLE_MEMORY", raising=False)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["code", "Add a short README note"])

    assert result.exit_code == 2
    records = load_memory(tmp_path)
    assert records[-1]["status"] == "clarification_needed"
    assert records[-1]["success"] is False
    assert records[-1]["selected_files"] == []
    items = load_memory_items(tmp_path)
    assert items[-1]["status"] == "rejected"
    assert "clarification_needed" in items[-1]["claim"]


def test_code_command_dry_run_prints_patch_without_changing_files(
    tmp_path, monkeypatch
):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
                "AGENT_ZERO_VALIDATION_COMMAND=pytest",
            ]
        ),
        encoding="utf-8",
    )
    target = tmp_path / "hello.txt"
    target.write_text("old\n", encoding="utf-8")

    fake_client = FakeModelClient(
        ModelResponse(
            content="""diff --git a/hello.txt b/hello.txt
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-old
+new
""",
            input_tokens=30,
            output_tokens=20,
            total_tokens=50,
        )
    )
    validation_calls = []
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.setattr(
        cli,
        "run_command",
        lambda command, cwd, timeout_seconds: validation_calls.append(command),
    )
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "code",
            "Change hello.txt old to new",
            "--dry-run",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    assert "Dry run: no files changed." in result.output
    assert "Patch summary:" in result.output
    assert "- hello.txt: +1 -1" in result.output
    assert "Proposed patch:" in result.output
    assert "diff --git a/hello.txt b/hello.txt" in result.output
    assert "Applied patch." not in result.output
    assert "Validation command:" not in result.output
    assert "Tokens: input=30, output=20, total=50" in result.output
    assert target.read_text(encoding="utf-8") == "old\n"
    assert validation_calls == []


def test_code_command_dry_run_prints_trace_json(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
            ]
        ),
        encoding="utf-8",
    )
    target = tmp_path / "hello.txt"
    target.write_text("old\n", encoding="utf-8")
    fake_client = FakeModelClient(
        ModelResponse(
            content="""diff --git a/hello.txt b/hello.txt
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-old
+new
""",
            input_tokens=30,
            output_tokens=20,
            total_tokens=50,
        )
    )
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "code",
            "Change hello.txt old to new",
            "--dry-run",
            "--trace-json",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    trace = json.loads(result.output.split("Trace JSON:\n", maxsplit=1)[1])
    assert trace["mode"] == "code"
    assert trace["status"] == "dry_run"
    assert trace["success"] is True
    assert trace["dry_run"] is True
    assert trace["changed_files"] == []
    assert trace["patch_summary"] == [
        {"path": "hello.txt", "additions": 1, "deletions": 1}
    ]
    assert trace["validation"] is None
    assert trace["model_calls"][0]["usage"]["total_tokens"] == 50
    assert [call["name"] for call in trace["tool_calls"]] == [
        "classify_task",
        "load_config",
        "build_repository_context",
        "model.complete",
        "extract_unified_diff",
        "summarize_unified_diff",
        "apply_unified_diff",
        "run_validation",
        "record_memory",
    ]
    assert trace["tool_calls"][6]["status"] == "skipped"
    assert trace["tool_calls"][7]["status"] == "skipped"
    assert all(isinstance(call["duration_ms"], float) for call in trace["tool_calls"])
    assert target.read_text(encoding="utf-8") == "old\n"


def test_code_command_dry_run_prints_changed_python_symbols(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
            ]
        ),
        encoding="utf-8",
    )
    target = tmp_path / "app.py"
    target.write_text(
        "\n".join(
            [
                "def greet():",
                '    return "old"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    fake_client = FakeModelClient(
        ModelResponse(
            content="""diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -2 +2 @@
-    return "old"
+    return "new"
"""
        )
    )
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "code",
            "Change app.py greeting return old to new",
            "--dry-run",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    assert "- app.py: +1 -1 (greet)" in result.output
    assert target.read_text(encoding="utf-8") == 'def greet():\n    return "old"\n'


def test_code_command_dry_run_trace_prints_code_steps(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
                "AGENT_ZERO_VALIDATION_COMMAND=pytest",
            ]
        ),
        encoding="utf-8",
    )
    target = tmp_path / "hello.txt"
    target.write_text("old\n", encoding="utf-8")
    fake_client = FakeModelClient(
        ModelResponse(
            content="""diff --git a/hello.txt b/hello.txt
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-old
+new
"""
        )
    )
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "code",
            "Change hello.txt old to new",
            "--dry-run",
            "--trace",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    assert "Agent trace:" in result.output
    assert "13. Prepared code prompt and called model" in result.output
    assert "Code trace: Model response received." in result.output
    assert "Code trace: Extracted unified diff." in result.output
    assert "Code trace: Patch summary prepared for 1 file(s)." in result.output
    assert (
        "Code trace: Dry run selected; patch application and validation skipped."
        in result.output
    )
    assert target.read_text(encoding="utf-8") == "old\n"


def test_code_command_rejects_empty_patch_in_dry_run(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
                "AGENT_ZERO_VALIDATION_COMMAND=pytest",
            ]
        ),
        encoding="utf-8",
    )
    target = tmp_path / "hello.txt"
    target.write_text("same\n", encoding="utf-8")
    fake_client = FakeModelClient(
        ModelResponse(
            content="""diff --git a/hello.txt b/hello.txt
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
 same
"""
        )
    )
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "code",
            "Update hello.txt saying same",
            "--dry-run",
            "--trace",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 1
    assert "Code trace: Empty patch rejected." in result.output
    assert "Attempting one empty-patch retry." in result.output
    assert "Code trace: Empty-patch retry returned empty patch." in result.output
    assert "Empty patch: Retry also returned a diff with no file content changes." in (
        result.output
    )
    assert "Dry run: no files changed." not in result.output
    assert "Proposed patch:" not in result.output
    assert target.read_text(encoding="utf-8") == "same\n"
    assert len(fake_client.calls) == 2


def test_code_command_retries_empty_patch_in_dry_run(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
                "AGENT_ZERO_VALIDATION_COMMAND=pytest",
            ]
        ),
        encoding="utf-8",
    )
    target = tmp_path / "hello.txt"
    target.write_text("old\n", encoding="utf-8")
    fake_client = SequenceModelClient(
        [
            ModelResponse(
                content="""diff --git a/hello.txt b/hello.txt
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
 old
"""
            ),
            ModelResponse(
                content="""diff --git a/hello.txt b/hello.txt
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-old
+new
"""
            ),
        ]
    )
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "code",
            "Change hello.txt old to new",
            "--dry-run",
            "--trace",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    assert "Code trace: Empty patch rejected." in result.output
    assert "Attempting one empty-patch retry." in result.output
    assert "Code trace: Empty-patch retry model response received." in result.output
    assert "Code trace: Empty-patch retry produced a non-empty patch." in result.output
    assert "Dry run: no files changed." in result.output
    assert "Patch summary:" in result.output
    assert "- hello.txt: +1 -1" in result.output
    assert "Proposed patch:" in result.output
    assert "+new" in result.output
    assert target.read_text(encoding="utf-8") == "old\n"
    assert len(fake_client.calls) == 2
    assert "Rejected empty diff:" in fake_client.calls[1][1]


def test_code_command_retries_patch_application_failure(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
            ]
        ),
        encoding="utf-8",
    )
    target = tmp_path / "README.md"
    target.write_text(
        "\n".join(
            [
                "# Demo",
                "",
                "Use `index` to build a local narrative map.",
                "",
                "This makes the context and cost tradeoff visible.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    fake_client = SequenceModelClient(
        [
            ModelResponse(
                content="""diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -3,2 +3,3 @@
 This makes the context
+Validation supports tests, lint, and format checks.
"""
            ),
            ModelResponse(
                content="""diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -5,2 +5,3 @@
 This makes the context and cost tradeoff visible.
+Validation supports tests, lint, and format checks.
 
"""
            ),
        ]
    )
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "code",
            "Append one sentence to the end of README.md",
            "--trace",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    assert "Code trace: Patch application failed." in result.output
    assert "Attempting one patch-application retry." in result.output
    assert "Code trace: Patch-application retry model response received." in (
        result.output
    )
    assert "Code trace: Patch-application retry applied patch to README.md." in (
        result.output
    )
    assert "Applied patch." in result.output
    assert "Validation supports tests, lint, and format checks." in target.read_text(
        encoding="utf-8"
    )
    assert len(fake_client.calls) == 2
    assert "Patch failure:" in fake_client.calls[1][1]
    assert "Current file excerpts:" in fake_client.calls[1][1]
    assert (
        "This makes the context and cost tradeoff visible." in fake_client.calls[1][1]
    )


def test_code_command_treats_no_change_response_as_success(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
            ]
        ),
        encoding="utf-8",
    )

    fake_client = FakeModelClient(ModelResponse(content="No changes needed."))
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "code",
            "Change hello.txt something to another thing",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    assert "No changes applied." in result.output
    assert "No changes needed." in result.output


def test_code_command_reports_missing_diff_when_not_noop(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
            ]
        ),
        encoding="utf-8",
    )

    fake_client = FakeModelClient(ModelResponse(content="I cannot do that safely."))
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "code",
            "Change hello.txt something to another thing",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 1
    assert "Could not find a patch" in result.output
    assert "I cannot do that safely." in result.output


def test_code_command_reports_patch_failure(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "hello.txt").write_text("actual\n", encoding="utf-8")

    fake_client = FakeModelClient(
        ModelResponse(
            content="""diff --git a/hello.txt b/hello.txt
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-expected
+new
"""
        )
    )
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["code", "Change hello.txt expected to new", "--env-file", str(env_file)],
    )

    assert result.exit_code == 1
    assert "Attempting one patch-application retry." in result.output
    assert "Patch failed after retry:" in result.output
    assert len(fake_client.calls) == 2
    assert "context mismatch" in result.output


def test_code_command_runs_validation_success(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
                "AGENT_ZERO_VALIDATION_COMMAND=pytest",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "hello.txt").write_text("old\n", encoding="utf-8")
    fake_client = FakeModelClient(
        ModelResponse(
            content="""diff --git a/hello.txt b/hello.txt
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-old
+new
"""
        )
    )
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.setattr(
        cli,
        "run_command",
        lambda command, cwd, timeout_seconds: CommandResult(
            command=["pytest"],
            exit_code=0,
            stdout="passed\n",
            stderr="",
        ),
    )
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["code", "Change hello.txt old to new", "--env-file", str(env_file)],
    )

    assert result.exit_code == 0
    assert "Validation command: pytest" in result.output
    assert "Validation passed." in result.output


def test_code_command_validation_success_prints_trace_json(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
                "AGENT_ZERO_VALIDATION_COMMAND=pytest",
            ]
        ),
        encoding="utf-8",
    )
    target = tmp_path / "hello.txt"
    target.write_text("old\n", encoding="utf-8")
    fake_client = FakeModelClient(
        ModelResponse(
            content="""diff --git a/hello.txt b/hello.txt
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-old
+new
""",
            input_tokens=30,
            output_tokens=20,
            total_tokens=50,
        )
    )
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.setattr(
        cli,
        "run_command",
        lambda command, cwd, timeout_seconds: CommandResult(
            command=["pytest"],
            exit_code=0,
            stdout="passed\n",
            stderr="",
        ),
    )
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "code",
            "Change hello.txt old to new",
            "--trace-json",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    trace = json.loads(result.output.split("Trace JSON:\n", maxsplit=1)[1])
    assert trace["mode"] == "code"
    assert trace["status"] == "validation_passed"
    assert trace["success"] is True
    assert trace["dry_run"] is False
    assert trace["changed_files"] == ["hello.txt"]
    assert trace["patch_summary"] == [
        {"path": "hello.txt", "additions": 1, "deletions": 1}
    ]
    assert trace["validation"]["passed"] is True
    assert trace["validation"]["command"] == ["pytest"]
    assert trace["model_calls"][0]["purpose"] == "initial"
    assert trace["model_calls"][0]["usage"]["total_tokens"] == 50
    assert [call["name"] for call in trace["tool_calls"]] == [
        "classify_task",
        "load_config",
        "build_repository_context",
        "model.complete",
        "extract_unified_diff",
        "summarize_unified_diff",
        "apply_unified_diff",
        "run_validation",
        "record_memory",
    ]
    assert trace["tool_calls"][6]["status"] == "success"
    assert trace["tool_calls"][7]["output_summary"] == "passed=True"
    assert all(isinstance(call["duration_ms"], float) for call in trace["tool_calls"])
    assert target.read_text(encoding="utf-8") == "new\n"


def test_code_command_runs_layered_validation_commands(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
                "AGENT_ZERO_TEST_COMMAND=pytest tests",
                "AGENT_ZERO_LINT_COMMAND=ruff check .",
                "AGENT_ZERO_FORMAT_COMMAND=ruff format --check .",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "hello.txt").write_text("old\n", encoding="utf-8")
    fake_client = FakeModelClient(
        ModelResponse(
            content="""diff --git a/hello.txt b/hello.txt
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-old
+new
"""
        )
    )
    commands = []

    def fake_run_command(command, cwd, timeout_seconds):
        commands.append(command)
        return CommandResult(
            command=command.split(),
            exit_code=0,
            stdout="passed\n",
            stderr="",
        )

    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.setattr(cli, "run_command", fake_run_command)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["code", "Change hello.txt old to new", "--env-file", str(env_file)],
    )

    assert result.exit_code == 0
    assert commands == [
        "pytest tests",
        "ruff check .",
        "ruff format --check .",
    ]
    assert "Validation step: tests" in result.output
    assert "Validation step: lint" in result.output
    assert "Validation step: format" in result.output


def test_code_command_trace_prints_patch_and_validation_steps(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
                "AGENT_ZERO_VALIDATION_COMMAND=pytest",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "hello.txt").write_text("old\n", encoding="utf-8")
    fake_client = FakeModelClient(
        ModelResponse(
            content="""diff --git a/hello.txt b/hello.txt
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-old
+new
"""
        )
    )
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.setattr(
        cli,
        "run_command",
        lambda command, cwd, timeout_seconds: CommandResult(
            command=["pytest"],
            exit_code=0,
            stdout="passed\n",
            stderr="",
        ),
    )
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["code", "Change hello.txt old to new", "--trace", "--env-file", str(env_file)],
    )

    assert result.exit_code == 0
    assert "Code trace: Model response received." in result.output
    assert "Code trace: Extracted unified diff." in result.output
    assert "Code trace: Applied patch to hello.txt." in result.output
    assert "Code trace: Validation passed." in result.output
    assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "new\n"


def test_code_command_reports_validation_failure(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
                "AGENT_ZERO_VALIDATION_COMMAND=pytest",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "hello.txt").write_text("old\n", encoding="utf-8")
    fake_client = SequenceModelClient(
        [
            ModelResponse(
                content="""diff --git a/hello.txt b/hello.txt
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-old
+new
"""
            ),
            ModelResponse(content="No safe correction."),
        ]
    )
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.setattr(
        cli,
        "run_command",
        lambda command, cwd, timeout_seconds: CommandResult(
            command=["pytest"],
            exit_code=1,
            stdout="failed\n",
            stderr="trace\n",
        ),
    )
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["code", "Change hello.txt old to new", "--env-file", str(env_file)],
    )

    assert result.exit_code == 1
    assert "Validation failed with exit code 1." in result.output
    assert "Attempting one validation-fix retry." in result.output
    assert "Could not find a retry patch" in result.output
    assert "failed" in result.output
    assert "trace" in result.output


def test_code_command_retries_after_validation_failure(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=http://localhost:1234/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
                "AGENT_ZERO_VALIDATION_COMMAND=pytest",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "hello.txt").write_text("old\n", encoding="utf-8")
    fake_client = SequenceModelClient(
        [
            ModelResponse(
                content="""diff --git a/hello.txt b/hello.txt
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-old
+bad
"""
            ),
            ModelResponse(
                content="""diff --git a/hello.txt b/hello.txt
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-bad
+good
"""
            ),
        ]
    )
    validation_results = [
        CommandResult(
            command=["pytest"],
            exit_code=1,
            stdout="failed\n",
            stderr="trace\n",
        ),
        CommandResult(
            command=["pytest"],
            exit_code=0,
            stdout="passed\n",
            stderr="",
        ),
    ]
    monkeypatch.setattr(cli, "create_model_client", lambda config: fake_client)
    monkeypatch.setattr(
        cli,
        "run_command",
        lambda command, cwd, timeout_seconds: validation_results.pop(0),
    )
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["code", "Change old to good", "--trace", "--env-file", str(env_file)],
    )

    assert result.exit_code == 0
    assert "Attempting one validation-fix retry." in result.output
    assert "Code trace: Starting validation-fix retry." in result.output
    assert "Code trace: Retry model response received." in result.output
    assert "Code trace: Applied retry patch to hello.txt." in result.output
    assert "Applied retry patch." in result.output
    assert "Validation passed." in result.output
    assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "good\n"
    assert fake_client.calls[0][0] == cli.CODE_SYSTEM_PROMPT
    assert fake_client.calls[1][0] == cli.FIX_VALIDATION_SYSTEM_PROMPT
    assert "Stdout:\nfailed" in fake_client.calls[1][1]
