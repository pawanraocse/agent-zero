import pytest

from agent_zero.tools.file_tools import (
    list_files,
    read_focused_text_file,
    read_text_file,
    search_text,
)


def test_list_files_excludes_secrets_and_caches(tmp_path):
    (tmp_path / "README.md").write_text("# Test\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=value\n", encoding="utf-8")
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "module.pyc").write_bytes(b"compiled")
    memory = tmp_path / ".agent-zero"
    memory.mkdir()
    (memory / "index.json").write_text("{}", encoding="utf-8")

    assert list_files(tmp_path) == ["README.md"]


def test_read_text_file_refuses_path_escape(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("nope", encoding="utf-8")

    with pytest.raises(ValueError, match="escapes repository"):
        read_text_file(tmp_path, "../outside.txt")


def test_read_text_file_accepts_unresolved_root(tmp_path):
    (tmp_path / "README.md").write_text("# Test\n", encoding="utf-8")

    snippet = read_text_file(tmp_path / ".", "README.md")

    assert snippet.content == "# Test\n"


def test_search_text_returns_matching_file_lines(tmp_path):
    (tmp_path / "README.md").write_text(
        "Agent Zero is a learning project.\n",
        encoding="utf-8",
    )

    assert search_text(tmp_path, "learning") == [
        "README.md:1: Agent Zero is a learning project."
    ]


def test_read_focused_text_file_keeps_query_matches_when_truncated(tmp_path):
    lines = [
        "intro line that should be omitted",
        "setup line that should be omitted",
        "class BedrockGatewayClient:",
        "    def submit(self):",
        "        return request_id",
        "tail line that should be omitted",
    ]
    (tmp_path / "model_client.py").write_text("\n".join(lines), encoding="utf-8")

    snippet = read_focused_text_file(
        tmp_path,
        "model_client.py",
        query_terms=["bedrock", "gateway"],
        max_chars=90,
        context_lines=1,
    )

    assert snippet.truncated is True
    assert snippet.focused is True
    assert "class BedrockGatewayClient:" in snippet.content
    assert "intro line that should be omitted" not in snippet.content
    assert snippet.content.startswith("... lines ")


def test_read_focused_text_file_prefers_python_symbol_blocks(tmp_path):
    content = "\n".join(
        [
            "def unrelated():",
            "    return 'skip me'",
            "",
            "class BedrockGatewayClient:",
            "    def __init__(self):",
            "        self.request_id = None",
            "",
            "    def complete(self):",
            "        return self.request_id",
            "",
            "def also_unrelated():",
            "    return 'skip me too'",
        ]
    )
    (tmp_path / "model_client.py").write_text(content, encoding="utf-8")

    snippet = read_focused_text_file(
        tmp_path,
        "model_client.py",
        query_terms=["bedrock", "gateway"],
        max_chars=210,
        context_lines=1,
    )

    assert snippet.truncated is True
    assert snippet.focused is True
    assert snippet.content.startswith("... symbol class BedrockGatewayClient")
    assert "class BedrockGatewayClient:" in snippet.content
    assert "def __init__(self):" in snippet.content
    assert "def complete(self):" in snippet.content
    assert "def unrelated():" not in snippet.content
    assert "def also_unrelated():" not in snippet.content


def test_read_focused_text_file_slices_oversized_python_classes(tmp_path):
    content = "\n".join(
        [
            "class BedrockGatewayClient:",
            '    """Client for an internal Bedrock HTTP gateway."""',
            "",
            "    def __init__(self):",
            "        self.url = 'gateway'",
            "        self.tenant_id = 'tenant'",
            "",
            "    def complete(self):",
            "        payload = {'model': 'bedrock'}",
            "        return self._poll_until_complete(payload)",
            "",
            "    def _poll_until_complete(self, payload):",
            "        status = 'pending'",
            "        return status",
            "",
            "    def _unrelated_helper(self):",
            "        return 'skip me'",
            "",
            "def other_symbol():",
            "    return 'skip me too'",
        ]
    )
    (tmp_path / "model_client.py").write_text(content, encoding="utf-8")

    snippet = read_focused_text_file(
        tmp_path,
        "model_client.py",
        query_terms=["bedrock", "gateway"],
        max_chars=460,
        context_lines=1,
    )

    assert snippet.truncated is True
    assert snippet.focused is True
    assert snippet.content.startswith("... symbol class BedrockGatewayClient")
    assert "sliced" in snippet.content
    assert "def complete(self):" in snippet.content
    assert "def _poll_until_complete(self, payload):" in snippet.content
    assert "def _unrelated_helper(self):" not in snippet.content
    assert "def other_symbol():" not in snippet.content


def test_read_focused_text_file_slices_oversized_method_bodies(tmp_path):
    filler_lines = [f"        unused_{index} = {index}" for index in range(20)]
    content = "\n".join(
        [
            "class BedrockGatewayClient:",
            '    """Client for an internal Bedrock HTTP gateway."""',
            "",
            "    def complete(self):",
            *filler_lines,
            "        response = self._client.post(self._url, json=payload)",
            "        data = response.json()",
            "        request_id = _extract_request_id(data)",
            "        return self._poll_until_complete(request_id, headers)",
            "",
            "def other_symbol():",
            "    return 'skip me'",
        ]
    )
    (tmp_path / "model_client.py").write_text(content, encoding="utf-8")

    snippet = read_focused_text_file(
        tmp_path,
        "model_client.py",
        query_terms=["bedrock", "gateway"],
        max_chars=520,
        context_lines=1,
    )

    assert snippet.truncated is True
    assert snippet.focused is True
    assert "... method def complete" in snippet.content
    assert "sliced" in snippet.content
    assert "response = self._client.post" in snippet.content
    assert "request_id = _extract_request_id(data)" in snippet.content
    assert "return self._poll_until_complete(request_id, headers)" in snippet.content
    assert "unused_0 = 0" not in snippet.content
    assert "def other_symbol():" not in snippet.content
