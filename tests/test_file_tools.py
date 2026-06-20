import pytest

from agent_zero.tools.file_tools import list_files, read_text_file, search_text


def test_list_files_excludes_secrets_and_caches(tmp_path):
    (tmp_path / "README.md").write_text("# Test\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=value\n", encoding="utf-8")
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "module.pyc").write_bytes(b"compiled")

    assert list_files(tmp_path) == ["README.md"]


def test_read_text_file_refuses_path_escape(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("nope", encoding="utf-8")

    with pytest.raises(ValueError, match="escapes repository"):
        read_text_file(tmp_path, "../outside.txt")


def test_search_text_returns_matching_file_lines(tmp_path):
    (tmp_path / "README.md").write_text(
        "Agent Zero is a learning project.\n",
        encoding="utf-8",
    )

    assert search_text(tmp_path, "learning") == [
        "README.md:1: Agent Zero is a learning project."
    ]
