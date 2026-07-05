import zipfile
from pathlib import Path

from ci_loop.collector import (
    build_code_digest,
    build_zip,
    is_secret_file,
    redact,
)


class TestRedact:
    def test_provider_key_prefixes(self):
        for sample in [
            "key = 'sk-abc123def456ghi789jkl'",
            'XAI_API_KEY="xai-1234567890abcdefghij"',
            "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
            "github_pat_11ABCDEFGHIJKLMNOPQRST_abcdef",
        ]:
            assert "[REDACTED]" in redact(sample), sample

    def test_aws_key_and_bearer(self):
        assert "AKIAIOSFODNN7EXAMPLE" not in redact("id AKIAIOSFODNN7EXAMPLE here")
        assert "eyJhbGci" not in redact("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5")

    def test_quoted_assignment_redacted(self):
        out = redact("password = 'hunter2hunter2hunter2'")
        assert "hunter2" not in out
        assert out.startswith("password = ")  # key name preserved

    def test_bare_value_with_digits_redacted(self):
        assert "9x8y7z" not in redact("AUTH=abc123def4569x8y7z")

    def test_code_reading_env_var_not_mangled(self):
        # Regression: this exact shape was redacted, corrupting the digest
        # and causing the reviewer to report a nonexistent bug.
        code = 'token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")'
        assert redact(code) == code

    def test_ordinary_code_untouched(self):
        code = "def get_price(symbol):\n    return prices[symbol]\n"
        assert redact(code) == code


class TestSecretFiles:
    PATTERNS = [".env", ".env.*", "*.pem", "*.key", "*credential*", "*secret*",
                "id_rsa*", ".netrc"]

    def test_secret_names_excluded(self):
        for name in [".env", ".env.production", "certs/server.pem",
                     "config/credentials.json", "my_secrets.yaml", "keys/id_rsa"]:
            assert is_secret_file(Path(name), self.PATTERNS), name

    def test_normal_names_included(self):
        for name in ["main.py", "README.md", "config.yaml", "environment.py"]:
            assert not is_secret_file(Path(name), self.PATTERNS), name


class TestDigestAndZip:
    def _repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "main.py").write_text("print('hi')\n")
        (repo / ".env").write_text("XAI_API_KEY=xai-supersecret123456789\n")
        (repo / "todos").mkdir()
        (repo / "todos" / "t.md").write_text("todo\n")
        return repo

    def test_digest_excludes_secrets_and_globs(self, tmp_path, cfg):
        repo = self._repo(tmp_path)
        digest = build_code_digest(repo, 100_000, cfg.secret_file_patterns, ["todos/**"])
        assert "main.py" in digest
        assert ".env" not in digest and "supersecret" not in digest
        assert "todos/" not in digest

    def test_zip_excludes_secrets_and_globs(self, tmp_path, cfg):
        repo = self._repo(tmp_path)
        zp = build_zip(repo, tmp_path / "out" / "snap.zip",
                       cfg.secret_file_patterns, ["todos/**"])
        assert zipfile.ZipFile(zp).namelist() == ["main.py"]
