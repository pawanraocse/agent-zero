import sys

import pytest

from agent_zero.tools.command_tool import CommandRunError, run_command


def test_run_command_captures_success(tmp_path):
    result = run_command(
        f"{sys.executable} -c \"print('ok')\"",
        cwd=tmp_path,
    )

    assert result.passed
    assert result.exit_code == 0
    assert result.stdout == "ok\n"
    assert result.stderr == ""


def test_run_command_captures_failure(tmp_path):
    result = run_command(
        f"{sys.executable} -c \"import sys; print('bad'); sys.exit(3)\"",
        cwd=tmp_path,
    )

    assert not result.passed
    assert result.exit_code == 3
    assert result.stdout == "bad\n"


def test_run_command_reports_timeout(tmp_path):
    result = run_command(
        f'{sys.executable} -c "import time; time.sleep(1)"',
        cwd=tmp_path,
        timeout_seconds=0.01,
    )

    assert not result.passed
    assert result.timed_out
    assert result.exit_code == 124


def test_run_command_rejects_empty_command(tmp_path):
    with pytest.raises(CommandRunError, match="empty"):
        run_command("", cwd=tmp_path)
