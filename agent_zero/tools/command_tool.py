from dataclasses import dataclass
from pathlib import Path
import shlex
import subprocess


class CommandRunError(RuntimeError):
    """Raised when a validation command cannot be started."""


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def run_command(
    command: str,
    cwd: Path,
    timeout_seconds: float = 120.0,
) -> CommandResult:
    """Run a command safely without shell expansion."""
    argv = shlex.split(command)
    if not argv:
        raise CommandRunError("Validation command is empty.")

    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            command=argv,
            exit_code=124,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            timed_out=True,
        )
    except OSError as exc:
        raise CommandRunError(str(exc)) from exc

    return CommandResult(
        command=argv,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
