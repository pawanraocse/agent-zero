import pytest

from agent_zero.tools.patch_tool import PatchApplyError, apply_unified_diff


def test_apply_unified_diff_updates_existing_file(tmp_path):
    target = tmp_path / "hello.txt"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")

    result = apply_unified_diff(
        tmp_path,
        """diff --git a/hello.txt b/hello.txt
--- a/hello.txt
+++ b/hello.txt
@@ -1,3 +1,3 @@
 one
-two
+TWO
 three
""",
    )

    assert result.changed_files == ["hello.txt"]
    assert target.read_text(encoding="utf-8") == "one\nTWO\nthree\n"


def test_apply_unified_diff_relocates_hunk_when_line_number_is_stale(tmp_path):
    target = tmp_path / "hello.txt"
    target.write_text("intro\none\ntwo\nthree\n", encoding="utf-8")

    result = apply_unified_diff(
        tmp_path,
        """diff --git a/hello.txt b/hello.txt
--- a/hello.txt
+++ b/hello.txt
@@ -1,3 +1,3 @@
 one
-two
+TWO
 three
""",
    )

    assert result.changed_files == ["hello.txt"]
    assert target.read_text(encoding="utf-8") == "intro\none\nTWO\nthree\n"


def test_apply_unified_diff_creates_new_text_file(tmp_path):
    result = apply_unified_diff(
        tmp_path,
        """diff --git a/new.txt b/new.txt
new file mode 100644
--- /dev/null
+++ b/new.txt
@@ -0,0 +1,2 @@
+hello
+world
""",
    )

    assert result.changed_files == ["new.txt"]
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "hello\nworld\n"


def test_apply_unified_diff_refuses_ignored_files(tmp_path):
    (tmp_path / ".env").write_text("SECRET=old\n", encoding="utf-8")

    with pytest.raises(PatchApplyError, match="ignored path"):
        apply_unified_diff(
            tmp_path,
            """diff --git a/.env b/.env
--- a/.env
+++ b/.env
@@ -1 +1 @@
-SECRET=old
+SECRET=new
""",
        )


def test_apply_unified_diff_refuses_path_escape(tmp_path):
    with pytest.raises(PatchApplyError, match="escapes repository"):
        apply_unified_diff(
            tmp_path,
            """diff --git a/../outside.txt b/../outside.txt
--- a/../outside.txt
+++ b/../outside.txt
@@ -1 +1 @@
-old
+new
""",
        )


def test_apply_unified_diff_reports_context_mismatch(tmp_path):
    target = tmp_path / "hello.txt"
    target.write_text("one\ntwo\n", encoding="utf-8")

    with pytest.raises(PatchApplyError, match="context mismatch"):
        apply_unified_diff(
            tmp_path,
            """diff --git a/hello.txt b/hello.txt
--- a/hello.txt
+++ b/hello.txt
@@ -1,2 +1,2 @@
 one
-missing
+changed
""",
        )
