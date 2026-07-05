import pytest

from ci_loop.config import load_config


def _write_config(tmp_path, body: str):
    path = tmp_path / "config.yaml"
    path.write_text(body)
    return path


MINIMAL = """\
repos:
  - name: r1
    github: owner/r1
"""


class TestLoadConfig:
    def test_defaults(self, tmp_path):
        cfg = load_config(_write_config(tmp_path, MINIMAL))
        assert cfg.mode == "todo"
        assert cfg.todo_destination == "repo"
        assert cfg.todo_repo_dir == "todos"
        assert cfg.verify_compile is True
        assert cfg.verify_run_tests is False
        assert cfg.repos[0].branch is None
        assert cfg.repos[0].exclude_globs == []
        assert ".env" in cfg.secret_file_patterns
        assert ".github/" in cfg.protected_paths

    def test_repo_branch_and_excludes(self, tmp_path):
        cfg = load_config(_write_config(tmp_path, """\
repos:
  - name: r1
    github: owner/r1
    branch: feature/x
    exclude_globs: ["state/**"]
"""))
        assert cfg.repos[0].branch == "feature/x"
        assert cfg.repos[0].exclude_globs == ["state/**"]

    def test_invalid_mode_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="mode"):
            load_config(_write_config(tmp_path, MINIMAL + "mode: hybrid\n"))

    def test_invalid_todo_destination_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="destination"):
            load_config(_write_config(
                tmp_path, MINIMAL + "todos:\n  destination: nowhere\n"))

    def test_security_overrides(self, tmp_path):
        cfg = load_config(_write_config(tmp_path, MINIMAL + """\
security:
  secret_file_patterns: ["*.custom"]
  protected_paths: ["deploy/"]
"""))
        assert cfg.secret_file_patterns == ["*.custom"]
        assert cfg.protected_paths == ["deploy/"]
