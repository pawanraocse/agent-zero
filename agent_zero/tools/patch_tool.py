from dataclasses import dataclass, field
from pathlib import Path
import re

from agent_zero.tools.file_tools import IGNORED_DIRS, IGNORED_FILES, TEXT_EXTENSIONS


HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


class PatchApplyError(RuntimeError):
    """Raised when a unified diff cannot be safely applied."""


@dataclass(frozen=True)
class PatchResult:
    changed_files: list[str]


@dataclass(frozen=True)
class PatchLine:
    kind: str
    text: str


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[PatchLine] = field(default_factory=list)


@dataclass
class FilePatch:
    path: str
    hunks: list[Hunk] = field(default_factory=list)
    is_new_file: bool = False


def apply_unified_diff(root: Path, diff_text: str) -> PatchResult:
    """Apply a small unified diff to text files under root."""
    file_patches = _parse_unified_diff(diff_text)
    if not file_patches:
        raise PatchApplyError("No file patches found.")

    changed_files = []
    for file_patch in file_patches:
        target_path = _resolve_patch_path(root, file_patch.path)
        _validate_patch_target(root, target_path)
        _apply_file_patch(target_path, file_patch)
        changed_files.append(file_patch.path)

    return PatchResult(changed_files=changed_files)


def _parse_unified_diff(diff_text: str) -> list[FilePatch]:
    file_patches: list[FilePatch] = []
    current_file: FilePatch | None = None
    current_hunk: Hunk | None = None
    pending_new_file = False

    for raw_line in diff_text.splitlines(keepends=True):
        line = raw_line.rstrip("\n")

        if line.startswith("new file mode "):
            pending_new_file = True
            continue

        if line.startswith("--- "):
            continue

        if line.startswith("+++ "):
            raw_path = line[4:].strip()
            if raw_path == "/dev/null":
                raise PatchApplyError("Deleting files is not supported yet.")
            current_file = FilePatch(
                path=_normalize_diff_path(raw_path),
                is_new_file=pending_new_file,
            )
            file_patches.append(current_file)
            current_hunk = None
            pending_new_file = False
            continue

        hunk_match = HUNK_HEADER_RE.match(line)
        if hunk_match:
            if current_file is None:
                raise PatchApplyError("Found hunk before file header.")
            current_hunk = Hunk(
                old_start=int(hunk_match.group("old_start")),
                old_count=int(hunk_match.group("old_count") or "1"),
                new_start=int(hunk_match.group("new_start")),
                new_count=int(hunk_match.group("new_count") or "1"),
            )
            current_file.hunks.append(current_hunk)
            continue

        if line.startswith("\\ No newline at end of file"):
            continue

        if raw_line[:1] in {" ", "+", "-"}:
            if current_hunk is None:
                continue
            current_hunk.lines.append(PatchLine(raw_line[0], raw_line[1:]))

    return file_patches


def _normalize_diff_path(raw_path: str) -> str:
    path = raw_path.split("\t", 1)[0].split(" ", 1)[0]
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


def _resolve_patch_path(root: Path, relative_path: str) -> Path:
    root = root.resolve()
    path = (root / relative_path).resolve()
    if root != path and root not in path.parents:
        raise PatchApplyError(f"Patch path escapes repository: {relative_path}")
    return path


def _validate_patch_target(root: Path, path: Path) -> None:
    relative_parts = path.relative_to(root.resolve()).parts
    if any(part in IGNORED_DIRS for part in relative_parts[:-1]):
        raise PatchApplyError(f"Refusing to patch ignored path: {path}")
    if path.name in IGNORED_FILES:
        raise PatchApplyError(f"Refusing to patch ignored path: {path}")
    if path.suffix.lower() not in TEXT_EXTENSIONS:
        raise PatchApplyError(f"Refusing to patch non-text path: {path}")


def _apply_file_patch(path: Path, file_patch: FilePatch) -> None:
    if file_patch.is_new_file:
        original_lines: list[str] = []
    else:
        if not path.exists():
            raise PatchApplyError(f"Cannot patch missing file: {file_patch.path}")
        original_lines = path.read_text(encoding="utf-8", errors="replace").splitlines(
            keepends=True
        )

    patched_lines = _apply_hunks(original_lines, file_patch)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(patched_lines), encoding="utf-8")


def _apply_hunks(original_lines: list[str], file_patch: FilePatch) -> list[str]:
    patched_lines = []
    original_index = 0

    for hunk in file_patch.hunks:
        hunk_start_index = max(hunk.old_start - 1, 0)
        if hunk_start_index < original_index:
            raise PatchApplyError(f"Overlapping hunks for {file_patch.path}")

        patched_lines.extend(original_lines[original_index:hunk_start_index])
        original_index = hunk_start_index

        for patch_line in hunk.lines:
            if patch_line.kind == " ":
                _assert_original_line(
                    original_lines,
                    original_index,
                    patch_line.text,
                    file_patch.path,
                )
                patched_lines.append(original_lines[original_index])
                original_index += 1
            elif patch_line.kind == "-":
                _assert_original_line(
                    original_lines,
                    original_index,
                    patch_line.text,
                    file_patch.path,
                )
                original_index += 1
            elif patch_line.kind == "+":
                patched_lines.append(patch_line.text)
            else:
                raise PatchApplyError(f"Unsupported patch line: {patch_line.kind}")

    patched_lines.extend(original_lines[original_index:])
    return patched_lines


def _assert_original_line(
    original_lines: list[str],
    index: int,
    expected: str,
    relative_path: str,
) -> None:
    if index >= len(original_lines):
        raise PatchApplyError(f"Patch hunk extends past end of file: {relative_path}")
    if original_lines[index] != expected:
        raise PatchApplyError(
            f"Patch context mismatch in {relative_path}: expected "
            f"{expected!r}, found {original_lines[index]!r}"
        )
