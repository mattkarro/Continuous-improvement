import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ci_loop.config import (  # noqa: E402
    DEFAULT_PROTECTED_PATHS,
    DEFAULT_SECRET_FILE_PATTERNS,
    Config,
    RepoConfig,
)


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    """A Config wired to temp directories — never touches real state."""
    return Config(
        mode="todo",
        repos=[RepoConfig(name="repo-a", github="owner/repo-a"),
               RepoConfig(name="repo-b", github="owner/repo-b")],
        grok_model="grok-4",
        grok_base_url="https://api.x.ai/v1",
        max_code_chars=100_000,
        max_log_chars=20_000,
        claude_model="claude-opus-4-8",
        claude_max_tokens=64_000,
        batches_per_day=4,
        slots_utc=["06:15", "11:45", "17:20", "22:50"],
        max_suggestions_per_batch=5,
        update_branch_prefix="ci/improvements",
        secret_file_patterns=list(DEFAULT_SECRET_FILE_PATTERNS),
        protected_paths=list(DEFAULT_PROTECTED_PATHS),
        verify_compile=True,
        verify_run_tests=False,
        todo_destination="repo",
        todo_repo_dir="todos",
        workdir=tmp_path / "workdir",
        artifacts_dir=tmp_path / "artifacts",
        state_dir=tmp_path / "state",
    )
