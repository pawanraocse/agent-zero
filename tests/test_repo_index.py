import json

from agent_zero.repo_index import (
    build_repo_index,
    index_file_count,
    index_relationship_count,
    load_repo_index,
    write_repo_index,
)


def test_build_repo_index_summarizes_files_and_relationships(tmp_path):
    package = tmp_path / "agent_zero"
    package.mkdir()
    tests = tmp_path / "tests"
    tests.mkdir()
    (package / "config.py").write_text(
        "class AgentConfig:\n    pass\n\ndef load_config():\n    return AgentConfig()\n",
        encoding="utf-8",
    )
    (tests / "test_config.py").write_text(
        "from agent_zero.config import load_config\n\n"
        "def test_load_config():\n    assert load_config()\n",
        encoding="utf-8",
    )

    index = build_repo_index(tmp_path)

    entries = {entry["path"]: entry for entry in index["files"]}
    assert entries["agent_zero/config.py"]["type"] == "python"
    assert "AgentConfig" in entries["agent_zero/config.py"]["symbols"]
    assert "AgentConfig" in entries["agent_zero/config.py"]["summary"]
    assert {
        "from": "tests/test_config.py",
        "to": "agent_zero/config.py",
        "type": "imports",
    } in index["relationships"]
    assert {
        "from": "tests/test_config.py",
        "to": "agent_zero/config.py",
        "type": "tests",
    } in index["relationships"]


def test_write_and_load_repo_index(tmp_path):
    (tmp_path / "README.md").write_text("# Agent Zero\n", encoding="utf-8")

    path = write_repo_index(tmp_path)
    loaded = load_repo_index(tmp_path)

    assert path == tmp_path / ".agent-zero" / "index.json"
    assert loaded is not None
    assert json.loads(path.read_text(encoding="utf-8")) == loaded
    assert index_file_count(loaded) == 1
    assert index_relationship_count(loaded) == 0
