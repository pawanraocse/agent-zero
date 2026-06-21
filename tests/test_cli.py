from typer.testing import CliRunner

from agent_zero import cli
from agent_zero.cli import app
from agent_zero.model_client import ModelClientError, ModelResponse


class FakeModelClient:
    def __init__(self, response: ModelResponse):
        self.response = response
        self.calls = []

    def complete(self, system_prompt: str, user_prompt: str) -> ModelResponse:
        self.calls.append((system_prompt, user_prompt))
        return self.response


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
    assert "Tokens: input=30, output=20, total=50" in result.output
    assert target.read_text(encoding="utf-8") == "new\n"
    system_prompt, user_prompt = fake_client.calls[0]
    assert system_prompt == cli.CODE_SYSTEM_PROMPT
    assert "Change request:\nChange old to new" in user_prompt


def test_code_command_reports_missing_diff(tmp_path, monkeypatch):
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

    assert result.exit_code == 1
    assert "Could not find a patch" in result.output
    assert "No changes needed." in result.output


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
