import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent / "templates" / ".agents" / "skills" / "crash-to-mr"
sys.path.insert(0, str(SKILL_DIR))

import crash_agent  # noqa: E402


@pytest.fixture(autouse=True)
def repo_root(tmp_path, monkeypatch):
    monkeypatch.setattr(crash_agent, "REPO_ROOT", tmp_path)
    crash_agent.CHANGED_FILES.clear()
    yield tmp_path
    crash_agent.CHANGED_FILES.clear()


def test_safe_path_allows_paths_within_repo_root(repo_root):
    (repo_root / "src").mkdir()
    result = crash_agent.safe_path("src/App.java")
    assert result == (repo_root / "src" / "App.java").resolve()


def test_safe_path_rejects_relative_traversal(repo_root):
    with pytest.raises(ValueError):
        crash_agent.safe_path("../../etc/passwd")


def test_safe_path_rejects_absolute_escape(repo_root, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    with pytest.raises(ValueError):
        crash_agent.safe_path(str(outside / "secret.txt"))


def test_tool_list_files_ignores_git_dir_and_reports_relative_paths(repo_root):
    (repo_root / ".git").mkdir()
    (repo_root / ".git" / "config").write_text("junk")
    (repo_root / "src").mkdir()
    (repo_root / "src" / "App.java").write_text("class App {}")

    result = crash_agent.tool_list_files(".")

    assert "src/App.java" in result["entries"]
    assert not any(entry.startswith(".git") for entry in result["entries"])


def test_tool_list_files_reports_missing_path(repo_root):
    result = crash_agent.tool_list_files("does/not/exist")
    assert "error" in result


def test_tool_read_file_returns_content(repo_root):
    (repo_root / "App.java").write_text("class App {}")
    result = crash_agent.tool_read_file("App.java")
    assert result["content"] == "class App {}"


def test_tool_read_file_reports_missing_file(repo_root):
    result = crash_agent.tool_read_file("missing.java")
    assert "error" in result


def test_tool_write_file_marks_existed_true_for_pre_existing_file(repo_root):
    (repo_root / "App.java").write_text("class App {}")
    crash_agent.tool_write_file("App.java", "class App { fixed }")
    assert crash_agent.CHANGED_FILES["App.java"] == {
        "content": "class App { fixed }",
        "existed": True,
    }


def test_tool_write_file_marks_existed_false_for_new_file(repo_root):
    crash_agent.tool_write_file("NewHelper.java", "class NewHelper {}")
    assert crash_agent.CHANGED_FILES["NewHelper.java"]["existed"] is False


def test_dispatch_tool_routes_to_correct_handler(repo_root):
    (repo_root / "App.java").write_text("class App {}")
    result = crash_agent.dispatch_tool("read_file", {"path": "App.java"})
    assert result == {"content": "class App {}"}


def test_dispatch_tool_rejects_unknown_tool(repo_root):
    result = crash_agent.dispatch_tool("delete_repo", {})
    assert "error" in result


def test_build_commit_actions_uses_update_for_existing_and_create_for_new():
    changed = {
        "App.java": {"content": "fixed", "existed": True},
        "NewHelper.java": {"content": "new", "existed": False},
    }
    actions = crash_agent.build_commit_actions(changed)
    by_path = {action["file_path"]: action for action in actions}
    assert by_path["App.java"]["action"] == "update"
    assert by_path["NewHelper.java"]["action"] == "create"


def test_branch_name_for_uses_pipeline_id():
    assert crash_agent.branch_name_for("12345") == "ai-fix/crash-12345"


def test_open_merge_request_skips_when_no_changes(repo_root, capsys):
    crash_agent.open_merge_request({}, {}, {})
    assert "skipping merge request" in capsys.readouterr().out
