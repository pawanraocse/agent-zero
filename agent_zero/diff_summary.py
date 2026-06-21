from dataclasses import dataclass


@dataclass(frozen=True)
class FileDiffSummary:
    path: str
    additions: int
    deletions: int


def summarize_unified_diff(diff_text: str) -> list[FileDiffSummary]:
    summaries: list[FileDiffSummary] = []
    current_path: str | None = None
    additions = 0
    deletions = 0

    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            if current_path is not None:
                summaries.append(FileDiffSummary(current_path, additions, deletions))
            current_path = _normalize_diff_path(line[4:].strip())
            additions = 0
            deletions = 0
            continue

        if current_path is None:
            continue

        if line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1

    if current_path is not None:
        summaries.append(FileDiffSummary(current_path, additions, deletions))

    return summaries


def format_diff_summary(summaries: list[FileDiffSummary]) -> str:
    if not summaries:
        return "(no file changes found)"

    return "\n".join(
        f"- {summary.path}: +{summary.additions} -{summary.deletions}"
        for summary in summaries
    )


def diff_summary_to_dicts(
    summaries: list[FileDiffSummary],
) -> list[dict[str, int | str]]:
    return [
        {
            "path": summary.path,
            "additions": summary.additions,
            "deletions": summary.deletions,
        }
        for summary in summaries
    ]


def _normalize_diff_path(raw_path: str) -> str:
    path = raw_path.split("\t", 1)[0].split(" ", 1)[0]
    if path == "/dev/null":
        return path
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path
