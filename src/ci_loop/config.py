from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]

DEFAULT_SECRET_FILE_PATTERNS = [
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.keystore",
    "*credential*", "*secret*", "*apikey*", "*api_key*",
    "id_rsa*", "id_ed25519*", ".netrc", ".npmrc", ".pypirc",
]
DEFAULT_PROTECTED_PATHS = [".github/", ".git/"]


@dataclass
class RepoConfig:
    name: str
    github: str
    log_globs: list[str] = field(default_factory=lambda: ["logs/**/*.log", "*.log"])


@dataclass
class Config:
    mode: str  # "todo" or "api"
    repos: list[RepoConfig]
    grok_model: str
    grok_base_url: str
    max_code_chars: int
    max_log_chars: int
    claude_model: str
    claude_max_tokens: int
    batches_per_day: int
    slots_utc: list[str]
    max_suggestions_per_batch: int
    update_branch_prefix: str
    secret_file_patterns: list[str]
    protected_paths: list[str]
    verify_compile: bool
    verify_run_tests: bool
    workdir: Path
    artifacts_dir: Path
    state_dir: Path


def load_config(path: Path | None = None) -> Config:
    path = path or ROOT / "config.yaml"
    raw = yaml.safe_load(path.read_text())

    repos = [RepoConfig(**r) for r in raw["repos"]]
    grok = raw.get("grok", {})
    claude = raw.get("claude", {})
    batching = raw.get("batching", {})
    paths = raw.get("paths", {})
    security = raw.get("security", {})

    mode = raw.get("mode", "todo")
    if mode not in ("todo", "api"):
        raise ValueError(f"config 'mode' must be 'todo' or 'api', got {mode!r}")

    return Config(
        mode=mode,
        repos=repos,
        grok_model=grok.get("model", "grok-4"),
        grok_base_url=grok.get("base_url", "https://api.x.ai/v1"),
        max_code_chars=int(grok.get("max_code_chars", 300_000)),
        max_log_chars=int(grok.get("max_log_chars", 60_000)),
        claude_model=claude.get("model", "claude-opus-4-8"),
        claude_max_tokens=int(claude.get("max_tokens", 64_000)),
        batches_per_day=int(batching.get("batches_per_day", 4)),
        slots_utc=list(batching.get("slots_utc", ["06:15", "11:45", "17:20", "22:50"])),
        max_suggestions_per_batch=int(batching.get("max_suggestions_per_batch", 5)),
        update_branch_prefix=raw.get("update_branch_prefix", "ci/improvements"),
        secret_file_patterns=list(
            security.get("secret_file_patterns", DEFAULT_SECRET_FILE_PATTERNS)),
        protected_paths=list(security.get("protected_paths", DEFAULT_PROTECTED_PATHS)),
        verify_compile=bool(raw.get("verify", {}).get("compile_check", True)),
        verify_run_tests=bool(raw.get("verify", {}).get("run_tests", False)),
        workdir=ROOT / paths.get("workdir", "workdir"),
        artifacts_dir=ROOT / paths.get("artifacts", "artifacts"),
        state_dir=ROOT / paths.get("state", "state"),
    )


def github_token() -> str:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GH_TOKEN (or GITHUB_TOKEN) is not set")
    return token
