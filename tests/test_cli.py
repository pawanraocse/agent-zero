import json

from typer.testing import CliRunner

from agent_zero import cli
from agent_zero.cli import app
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
    assert "User question:\nWhat does this project do?" in user_prompt
    assert "Repository context:" in user_prompt
    assert "README.md" in user_prompt


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
    assert "Change request:\nAdd config loading" in user_prompt
    assert "Repository context:" in user_prompt


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
                "task": "Change old to new",
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
        ["code", "Change old to new", "--env-file", str(env_file)],
    )

    assert result.exit_code == 0
    assert "Applied patch." in result.output
    assert "- hello.txt" in result.output
    assert "Validation skipped" in result.output
    assert "Tokens: input=30, output=20, total=50" in result.output
    assert target.read_text(encoding="utf-8") == "new\n"
    system_prompt, user_prompt = fake_client.calls[0]
    assert system_prompt == cli.CODE_SYSTEM_PROMPT
    assert "Change request:\nChange old to new" in user_prompt


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
        ["code", "Change old to new", "--dry-run", "--env-file", str(env_file)],
    )

    assert result.exit_code == 0
    assert "Dry run: no files changed." in result.output
    assert "Proposed patch:" in result.output
    assert "diff --git a/hello.txt b/hello.txt" in result.output
    assert "Applied patch." not in result.output
    assert "Validation command:" not in result.output
    assert "Tokens: input=30, output=20, total=50" in result.output
    assert target.read_text(encoding="utf-8") == "old\n"
    assert validation_calls == []


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
        ["code", "Change something", "--env-file", str(env_file)],
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
        ["code", "Change something", "--env-file", str(env_file)],
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
        ["code", "Change expected to new", "--env-file", str(env_file)],
    )

    assert result.exit_code == 1
    assert "Patch failed:" in result.output
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
        ["code", "Change old to new", "--env-file", str(env_file)],
    )

    assert result.exit_code == 0
    assert "Validation command: pytest" in result.output
    assert "Validation passed." in result.output


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
        ["code", "Change old to new", "--env-file", str(env_file)],
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
        ["code", "Change old to good", "--env-file", str(env_file)],
    )

    assert result.exit_code == 0
    assert "Attempting one validation-fix retry." in result.output
    assert "Applied retry patch." in result.output
    assert "Validation passed." in result.output
    assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "good\n"
    assert fake_client.calls[0][0] == cli.CODE_SYSTEM_PROMPT
    assert fake_client.calls[1][0] == cli.FIX_VALIDATION_SYSTEM_PROMPT
    assert "Stdout:\nfailed" in fake_client.calls[1][1]
