import ast
from dataclasses import dataclass
from pathlib import Path
import re


HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


@dataclass(frozen=True)
class FileDiffSummary:
    path: str
    additions: int
    deletions: int
    symbols: tuple[str, ...] = ()


def summarize_unified_diff(
    diff_text: str,
    root: Path | None = None,
) -> list[FileDiffSummary]:
    summaries: list[FileDiffSummary] = []
    current_path: str | None = None
    additions = 0
    deletions = 0
    changed_lines: set[int] = set()
    old_line: int | None = None

    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            if current_path is not None:
                summaries.append(
                    _build_file_summary(
                        current_path,
                        additions,
                        deletions,
                        changed_lines,
                        root,
                    )
                )
            current_path = _normalize_diff_path(line[4:].strip())
            additions = 0
            deletions = 0
            changed_lines = set()
            old_line = None
            continue

        if current_path is None:
            continue

        hunk_match = HUNK_HEADER_RE.match(line)
        if hunk_match:
            old_line = int(hunk_match.group("old_start"))
            continue

        if line.startswith("+") and not line.startswith("+++"):
            additions += 1
            if old_line is not None:
                changed_lines.add(max(old_line, 1))
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
            if old_line is not None:
                changed_lines.add(old_line)
                old_line += 1
        elif line.startswith(" ") and old_line is not None:
            old_line += 1

    if current_path is not None:
        summaries.append(
            _build_file_summary(
                current_path,
                additions,
                deletions,
                changed_lines,
                root,
            )
        )

    return summaries


def format_diff_summary(summaries: list[FileDiffSummary]) -> str:
    if not summaries:
        return "(no file changes found)"

    return "\n".join(_format_file_summary(summary) for summary in summaries)


def diff_summary_has_changes(summaries: list[FileDiffSummary]) -> bool:
    return any(summary.additions > 0 or summary.deletions > 0 for summary in summaries)


def diff_summary_to_dicts(
    summaries: list[FileDiffSummary],
) -> list[dict[str, object]]:
    result = []
    for summary in summaries:
        item = {
            "path": summary.path,
            "additions": summary.additions,
            "deletions": summary.deletions,
        }
        if summary.symbols:
            item["symbols"] = list(summary.symbols)
        result.append(item)
    return result


def _build_file_summary(
    path: str,
    additions: int,
    deletions: int,
    changed_lines: set[int],
    root: Path | None,
) -> FileDiffSummary:
    return FileDiffSummary(
        path=path,
        additions=additions,
        deletions=deletions,
        symbols=_changed_python_symbols(path, changed_lines, root),
    )


def _format_file_summary(summary: FileDiffSummary) -> str:
    line = f"- {summary.path}: +{summary.additions} -{summary.deletions}"
    if summary.symbols:
        line += f" ({', '.join(summary.symbols)})"
    return line


def _changed_python_symbols(
    path: str,
    changed_lines: set[int],
    root: Path | None,
) -> tuple[str, ...]:
    if root is None or not path.endswith(".py") or path == "/dev/null":
        return ()

    file_path = root / path
    if not file_path.exists():
        return ()

    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return ()

    spans = _python_symbol_spans(tree)
    symbols = []
    for line in sorted(changed_lines):
        symbol = _symbol_for_line(line, spans)
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return tuple(symbols)


def _python_symbol_spans(tree: ast.AST) -> list[tuple[int, int, str]]:
    spans = []

    def visit(node: ast.AST, parents: tuple[str, ...]) -> None:
        name = _symbol_node_name(node)
        next_parents = parents
        if name is not None:
            symbol_name = ".".join([*parents, name])
            end_line = getattr(node, "end_lineno", getattr(node, "lineno", 0))
            spans.append((node.lineno, end_line, symbol_name))
            next_parents = (*parents, name)

        for child in ast.iter_child_nodes(node):
            visit(child, next_parents)

    visit(tree, ())
    return sorted(spans, key=lambda span: (span[1] - span[0], -span[0]))


def _symbol_node_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.ClassDef):
        return node.name
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return node.name
    return None


def _symbol_for_line(line: int, spans: list[tuple[int, int, str]]) -> str | None:
    for start, end, name in spans:
        if start <= line <= end:
            return name
    for start, end, name in spans:
        if start <= line - 1 <= end:
            return name
    return None


def _normalize_diff_path(raw_path: str) -> str:
    path = raw_path.split("\t", 1)[0].split(" ", 1)[0]
    if path == "/dev/null":
        return path
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path
