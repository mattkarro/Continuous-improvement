import pytest

from ci_loop.claude_updater import apply_changes, is_protected_path, verify_changes

PROTECTED = [".github/", ".git/"]


class TestProtectedPaths:
    def test_protected(self):
        for p in [".github/workflows/x.yml", ".github", ".git/config"]:
            assert is_protected_path(p, PROTECTED), p

    def test_not_protected(self):
        for p in ["src/github_client.py", "docs/.github.md", "main.py"]:
            assert not is_protected_path(p, PROTECTED), p


class TestApplyChanges:
    def test_applies_and_skips_protected(self, tmp_path):
        applied, skipped = apply_changes(tmp_path, [
            {"path": "src/new.py", "action": "create", "content": "x = 1\n"},
            {"path": ".github/workflows/evil.yml", "action": "create", "content": "on: push"},
        ], PROTECTED)
        assert applied == ["create src/new.py"]
        assert skipped == ["create .github/workflows/evil.yml (protected path)"]
        assert (tmp_path / "src" / "new.py").exists()
        assert not (tmp_path / ".github").exists()

    def test_delete_and_modify(self, tmp_path):
        (tmp_path / "old.py").write_text("x")
        applied, _ = apply_changes(tmp_path, [
            {"path": "old.py", "action": "delete", "content": ""},
        ], PROTECTED)
        assert applied == ["delete old.py"]
        assert not (tmp_path / "old.py").exists()

    @pytest.mark.parametrize("bad", [
        "../outside.py",
        "/etc/passwd",
        ".git/hooks/pre-commit",
        "a/../../outside.py",
    ])
    def test_unsafe_paths_rejected(self, tmp_path, bad):
        with pytest.raises(ValueError):
            apply_changes(tmp_path, [
                {"path": bad, "action": "create", "content": "x"},
            ], PROTECTED)

    def test_sibling_prefix_dir_not_writable(self, tmp_path):
        # Historic bug: startswith() check allowed /workdir/repo-evil to pass
        # the containment test for /workdir/repo.
        repo = tmp_path / "repo"
        repo.mkdir()
        (tmp_path / "repo-evil").mkdir()
        with pytest.raises(ValueError):
            apply_changes(repo, [
                {"path": "../repo-evil/x.py", "action": "create", "content": "x"},
            ], PROTECTED)


class TestVerifyChanges:
    def test_syntax_error_caught(self, cfg, tmp_path):
        (tmp_path / "bad.py").write_text("def broken(:\n")
        failures = verify_changes(cfg, tmp_path, ["modify bad.py"])
        assert len(failures) == 1 and "bad.py" in failures[0]

    def test_clean_file_passes(self, cfg, tmp_path):
        (tmp_path / "good.py").write_text("x = 1\n")
        assert verify_changes(cfg, tmp_path, ["create good.py"]) == []

    def test_deletes_and_non_python_ignored(self, cfg, tmp_path):
        assert verify_changes(cfg, tmp_path,
                              ["delete gone.py", "create notes.md"]) == []
