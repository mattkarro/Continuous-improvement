from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class RepoConfig:
    name: str
    github: str
    log_globs: list[str] = field(default_factory=lambda: ["logs/**/*.log", "*.log"])


@dataclass
class Config:
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

    return Config(
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
        workdir=ROOT / paths.get("workdir", "workdir"),
        artifacts_dir=ROOT / paths.get("artifacts", "artifacts"),
        state_dir=ROOT / paths.get("state", "state"),
    )


def github_token() -> str:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GH_TOKEN (or GITHUB_TOKEN) is not set")
    return token
