from dataclasses import asdict, dataclass
import re


@dataclass(frozen=True)
class TaskClassification:
    action_type: str
    recommended_mode: str
    subcategory: str
    write_intent: str
    specificity: str
    requires_clarification: bool
    missing_information: list[str]
    confidence: str
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


_WORD_PATTERN = re.compile(r"[a-zA-Z0-9_.-]+")

_ASK_TERMS = {
    "what",
    "why",
    "how",
    "explain",
    "describe",
    "show",
    "list",
    "compare",
    "review",
    "summarize",
    "status",
    "meaning",
    "understand",
}

_PLAN_TERMS = {
    "plan",
    "design",
    "architecture",
    "roadmap",
    "approach",
    "strategy",
    "steps",
    "todo",
    "testing",
    "test",
    "migration",
    "risks",
}

_CODE_TERMS = {
    "add",
    "update",
    "change",
    "fix",
    "implement",
    "create",
    "write",
    "delete",
    "remove",
    "refactor",
    "rename",
    "replace",
    "append",
    "edit",
    "proceed",
}

_POSSIBLE_WRITE_TERMS = {
    "next",
    "proceed",
    "continue",
    "go",
    "do",
    "ok",
    "yes",
}

_FEEDBACK_WORKED_TERMS = {
    "worked",
    "fixed",
    "correct",
    "good",
    "great",
    "done",
}

_FEEDBACK_FAILED_TERMS = {
    "wrong",
    "failed",
    "broken",
    "issue",
    "not",
    "still",
}

_DOC_TARGET_TERMS = {"readme", "docs", "documentation", "hld", "todo"}
_TEST_TERMS = {"test", "tests", "pytest", "eval", "validation"}
_CONFIG_TERMS = {"env", ".env", "config", "configuration", "settings"}
_BUG_TERMS = {"fix", "bug", "error", "failed", "failure", "broken", "issue"}
_REFACTOR_TERMS = {"refactor", "rename", "extract", "move"}
_ARCH_TERMS = {"architecture", "design", "hld", "system", "flow"}
_MIGRATION_TERMS = {"migrate", "migration", "move"}
_COMPARE_TERMS = {"compare", "vs", "versus", "difference", "better"}
_STATUS_TERMS = {"status", "pending", "left", "done", "completed", "next"}
_FILLER_TERMS = {
    "a",
    "an",
    "the",
    "short",
    "small",
    "simple",
    "quick",
    "harmless",
    "one",
    "sentence",
}


def classify_task(task: str) -> TaskClassification:
    text = task.strip()
    lowered = text.lower()
    terms = _terms(lowered)

    if not terms:
        return TaskClassification(
            action_type="read",
            recommended_mode="ask",
            subcategory="unclear",
            write_intent="none",
            specificity="low",
            requires_clarification=True,
            missing_information=["task details"],
            confidence="low",
            reason="The request is empty or does not contain enough information.",
        )

    if _looks_like_feedback(lowered, terms):
        return TaskClassification(
            action_type="write",
            recommended_mode="memory",
            subcategory="memory_feedback",
            write_intent="explicit",
            specificity="medium",
            requires_clarification=False,
            missing_information=[],
            confidence="medium",
            reason="The request looks like feedback that may update memory.",
        )

    if _has_any(terms, _CODE_TERMS):
        return _classify_code(text, terms)

    if _has_any(terms, _PLAN_TERMS):
        return _classify_plan(terms)

    if _has_any(terms, _ASK_TERMS) or "?" in text:
        return _classify_ask(terms)

    if _has_any(terms, _POSSIBLE_WRITE_TERMS):
        return TaskClassification(
            action_type="write",
            recommended_mode="code",
            subcategory="possible_write",
            write_intent="possible",
            specificity="low",
            requires_clarification=True,
            missing_information=[
                "the action to perform",
                "the target or expected outcome",
            ],
            confidence="low",
            reason="The request implies continuation but does not describe the action.",
        )

    return TaskClassification(
        action_type="read",
        recommended_mode="ask",
        subcategory="unclear",
        write_intent="none",
        specificity="low",
        requires_clarification=True,
        missing_information=["whether this is a question, plan, or change request"],
        confidence="low",
        reason="The request does not contain enough routing signal.",
    )


def _classify_ask(terms: set[str]) -> TaskClassification:
    if _has_any(terms, _COMPARE_TERMS):
        subcategory = "compare_options"
        reason = "The request asks to compare options."
    elif _has_any(terms, _STATUS_TERMS):
        subcategory = "status"
        reason = "The request asks for project or task status."
    elif "how" in terms:
        subcategory = "how_to"
        reason = "The request asks how something works or how to do something."
    elif _has_any(terms, _BUG_TERMS):
        subcategory = "debug_info"
        reason = "The request asks about an error or failure."
    else:
        subcategory = "explain_code"
        reason = "The request asks for explanation without edit intent."

    return TaskClassification(
        action_type="read",
        recommended_mode="ask",
        subcategory=subcategory,
        write_intent="none",
        specificity="high",
        requires_clarification=False,
        missing_information=[],
        confidence="high",
        reason=reason,
    )


def _classify_plan(terms: set[str]) -> TaskClassification:
    if _has_any(terms, _ARCH_TERMS):
        subcategory = "architecture_plan"
    elif _has_any(terms, _TEST_TERMS):
        subcategory = "testing_plan"
    elif _has_any(terms, _MIGRATION_TERMS):
        subcategory = "migration_plan"
    elif "risk" in terms or "risks" in terms:
        subcategory = "risk_review"
    else:
        subcategory = "implementation_plan"

    return TaskClassification(
        action_type="plan",
        recommended_mode="plan",
        subcategory=subcategory,
        write_intent="none",
        specificity="high",
        requires_clarification=False,
        missing_information=[],
        confidence="high",
        reason="The request asks for planning or design before action.",
    )


def _classify_code(text: str, terms: set[str]) -> TaskClassification:
    subcategory = _code_subcategory(terms)
    missing_information = _code_missing_information(text, terms, subcategory)
    requires_clarification = bool(missing_information)
    specificity = "low" if requires_clarification else "high"
    confidence = "medium" if requires_clarification else "high"

    if "proceed" in terms and len(terms) <= 2:
        return TaskClassification(
            action_type="write",
            recommended_mode="code",
            subcategory="possible_write",
            write_intent="possible",
            specificity="low",
            requires_clarification=True,
            missing_information=["which task to proceed with"],
            confidence="low",
            reason="The request asks to proceed but does not include the task.",
        )

    return TaskClassification(
        action_type="write",
        recommended_mode="code",
        subcategory=subcategory,
        write_intent="explicit",
        specificity=specificity,
        requires_clarification=requires_clarification,
        missing_information=missing_information,
        confidence=confidence,
        reason=(
            "The request asks for a repository change."
            if not requires_clarification
            else "The request has edit intent but is missing details needed to act safely."
        ),
    )


def _code_subcategory(terms: set[str]) -> str:
    if _has_any(terms, _DOC_TARGET_TERMS):
        return "documentation_edit"
    if _has_any(terms, _TEST_TERMS):
        return "test_addition"
    if _has_any(terms, _CONFIG_TERMS):
        return "config_change"
    if _has_any(terms, _REFACTOR_TERMS):
        return "refactor"
    if _has_any(terms, _BUG_TERMS):
        return "bug_fix"
    return "feature_change"


def _code_missing_information(
    text: str,
    terms: set[str],
    subcategory: str,
) -> list[str]:
    missing = []
    has_target = _has_target(text, terms)
    has_change_detail = _has_change_detail(text, terms)

    if not has_target:
        missing.append("target file or component")
    if subcategory == "documentation_edit" and not has_change_detail:
        missing.append("exact documentation text or topic")
    elif not has_change_detail:
        missing.append("specific behavior to change")

    return missing


def _has_target(text: str, terms: set[str]) -> bool:
    if _has_any(terms, _DOC_TARGET_TERMS | _CONFIG_TERMS | _TEST_TERMS):
        return True
    if "/" in text or "." in text:
        return True
    return any(
        term.endswith((".py", ".md", ".txt", ".json", ".toml")) for term in terms
    )


def _has_change_detail(text: str, terms: set[str]) -> bool:
    if ":" in text:
        return True
    if "saying" in terms or "that" in terms:
        return True
    detail_terms = terms - _CODE_TERMS - _DOC_TARGET_TERMS - _FILLER_TERMS
    return len(detail_terms) >= 3


def _looks_like_feedback(text: str, terms: set[str]) -> bool:
    if "it worked" in text or "this worked" in text or "did not work" in text:
        return True
    if _has_any(terms, _FEEDBACK_WORKED_TERMS) and len(terms) <= 4:
        return True
    return _has_any(terms, _FEEDBACK_FAILED_TERMS) and len(terms) <= 5


def _terms(text: str) -> set[str]:
    terms = {match.group(0).lower() for match in _WORD_PATTERN.finditer(text)}
    for term in list(terms):
        if "." in term:
            terms.add(term.split(".", maxsplit=1)[0])
    return terms


def _has_any(terms: set[str], candidates: set[str]) -> bool:
    return bool(terms & candidates)
