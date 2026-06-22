from agent_zero.diff_summary import (
    FileDiffSummary,
    diff_summary_has_changes,
    diff_summary_to_dicts,
    format_diff_summary,
    summarize_unified_diff,
)


def test_summarize_unified_diff_counts_changes_per_file():
    diff_text = """diff --git a/one.txt b/one.txt
--- a/one.txt
+++ b/one.txt
@@ -1,2 +1,3 @@
 old
-remove me
+add me
+add another
diff --git a/two.txt b/two.txt
--- a/two.txt
+++ b/two.txt
@@ -1 +1 @@
-before
+after
"""

    summaries = summarize_unified_diff(diff_text)

    assert summaries == [
        FileDiffSummary(path="one.txt", additions=2, deletions=1),
        FileDiffSummary(path="two.txt", additions=1, deletions=1),
    ]


def test_format_diff_summary():
    summaries = [FileDiffSummary(path="README.md", additions=3, deletions=1)]

    assert format_diff_summary(summaries) == "- README.md: +3 -1"
    assert format_diff_summary([]) == "(no file changes found)"


def test_diff_summary_to_dicts():
    summaries = [FileDiffSummary(path="README.md", additions=3, deletions=1)]

    assert diff_summary_to_dicts(summaries) == [
        {"path": "README.md", "additions": 3, "deletions": 1}
    ]


def test_diff_summary_has_changes_rejects_empty_file_diffs():
    assert diff_summary_has_changes(
        [FileDiffSummary(path="README.md", additions=1, deletions=0)]
    )
    assert not diff_summary_has_changes(
        [FileDiffSummary(path="README.md", additions=0, deletions=0)]
    )
    assert not diff_summary_has_changes([])
