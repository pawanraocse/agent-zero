class DiffExtractionError(RuntimeError):
    """Raised when a model response does not contain a unified diff."""


NO_CHANGE_PHRASES = (
    "already contains",
    "already exists",
    "already present",
    "already satisfied",
    "no change needed",
    "no changes needed",
    "no modification needed",
    "nothing to change",
    "nothing to do",
)


def extract_unified_diff(text: str) -> str:
    """Extract unified diff text from a model response."""
    fenced = _extract_fenced_diff(text)
    if fenced:
        return fenced

    diff_start = text.find("diff --git ")
    if diff_start != -1:
        return text[diff_start:].strip() + "\n"

    file_header_start = text.find("--- ")
    if file_header_start != -1 and "+++ " in text[file_header_start:]:
        return text[file_header_start:].strip() + "\n"

    raise DiffExtractionError("Model response did not contain a unified diff.")


def is_no_change_response(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in NO_CHANGE_PHRASES)


def _extract_fenced_diff(text: str) -> str | None:
    lines = text.splitlines()
    in_fence = False
    captured: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not in_fence and stripped in {"```diff", "```patch", "```"}:
            in_fence = True
            continue
        if in_fence and stripped == "```":
            candidate = "\n".join(captured).strip()
            if _looks_like_diff(candidate):
                return candidate + "\n"
            captured = []
            in_fence = False
            continue
        if in_fence:
            captured.append(line)

    return None


def _looks_like_diff(text: str) -> bool:
    return "diff --git " in text or ("--- " in text and "+++ " in text)
