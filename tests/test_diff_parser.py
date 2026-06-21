import pytest

from agent_zero.diff_parser import (
    DiffExtractionError,
    extract_unified_diff,
    is_no_change_response,
)


def test_extract_unified_diff_from_fenced_block():
    text = """Here is the patch:

```diff
diff --git a/a.txt b/a.txt
--- a/a.txt
+++ b/a.txt
@@ -1 +1 @@
-old
+new
```
"""

    assert extract_unified_diff(text).startswith("diff --git a/a.txt b/a.txt")


def test_extract_unified_diff_from_plain_response():
    text = """Summary first.

diff --git a/a.txt b/a.txt
--- a/a.txt
+++ b/a.txt
@@ -1 +1 @@
-old
+new
"""

    assert extract_unified_diff(text).startswith("diff --git a/a.txt b/a.txt")


def test_extract_unified_diff_reports_missing_diff():
    with pytest.raises(DiffExtractionError, match="did not contain"):
        extract_unified_diff("No changes needed.")


def test_is_no_change_response_detects_satisfied_request():
    assert is_no_change_response("The README already contains this note.")
    assert is_no_change_response("No changes needed.")
    assert not is_no_change_response("I cannot make this change safely.")
