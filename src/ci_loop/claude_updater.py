"""Run a batch of suggestions through Claude and push code updates."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .collector import build_code_digest, clone_or_update
from .config import Config, RepoConfig

# Structured output schema: Claude must return concrete file operations.
CHANGES_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "commit_message": {"type": "string"},
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "action": {"type": "string", "enum": ["create", "modify", "delete"]},
                    "content": {
                        "type": "string",
                        "description": "Full new file content (empty string for delete)",
                    },
                },
                "required": ["path", "action", "content"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "commit_message", "changes"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
You are an autonomous code-update engine for automated trading/betting agents.
You receive a repository snapshot and a set of reviewed improvement
suggestions. Implement the suggestions as concrete, working code changes.

Rules:
- Return the FULL content of every file you create or modify — not diffs.
- Keep changes focused on the suggestions; do not refactor unrelated code.
- Match the existing code style of the repository.
- NEVER create, modify, or delete files under .github/ or any CI/workflow
  configuration — such changes will be rejected.
- If a suggestion is unsafe or cannot be implemented from the available
  context, skip it and explain why in the summary.
"""


def _format_suggestions(suggestions: list[dict]) -> str:
    lines = []
    for i, s in enumerate(suggestions, 1):
        lines.append(
            f"{i}. [{s.get('type', 'improvement')}, priority {s.get('priority', '?')}] "
            f"{s.get('title', 'untitled')}\n   {s.get('details', '')}\n"
            f"   Likely files: {', '.join(s.get('files', [])) or 'unknown'}"
        )
    return "\n".join(lines)


def generate_changes(cfg: Config, repo: RepoConfig, checkout: Path,
                     suggestions: list[dict]) -> dict:
    """One Claude call: suggestions + code snapshot -> file operations."""
    import anthropic  # imported lazily: only needed in api mode

    client = anthropic.Anthropic()
    digest = build_code_digest(checkout, cfg.max_code_chars, cfg.secret_file_patterns)
    user_content = (
        f"Repository: {repo.github}\n\n"
        f"--- SUGGESTIONS TO IMPLEMENT ---\n{_format_suggestions(suggestions)}\n\n"
        f"--- CURRENT CODE SNAPSHOT ---\n{digest}"
    )

    with client.messages.stream(
        model=cfg.claude_model,
        max_tokens=cfg.claude_max_tokens,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        output_config={"format": {"type": "json_schema", "schema": CHANGES_SCHEMA}},
        messages=[{"role": "user", "content": user_content}],
    ) as stream:
        message = stream.get_final_message()

    if message.stop_reason == "refusal":
        raise RuntimeError(f"Claude refused the update request for {repo.name}")
    if message.stop_reason == "max_tokens":
        raise RuntimeError(f"Claude output truncated for {repo.name}; raise claude.max_tokens")

    text = next(b.text for b in message.content if b.type == "text")
    return json.loads(text)


def is_protected_path(rel: str, protected_paths: list[str]) -> bool:
    """True if the model must not touch this path (e.g. CI workflow files)."""
    import fnmatch

    rel_norm = rel.replace("\\", "/").lower()
    for raw in protected_paths:
        p = raw.replace("\\", "/").lower()
        if p.endswith("/"):
            if rel_norm.startswith(p) or rel_norm == p.rstrip("/"):
                return True
        elif rel_norm == p or fnmatch.fnmatch(rel_norm, p):
            return True
    return False


def apply_changes(checkout: Path, changes: list[dict],
                  protected_paths: list[str]) -> tuple[list[str], list[str]]:
    """Apply model-generated file operations inside the checkout.

    Model output is untrusted: reject anything that escapes the repo,
    touches .git internals, or lands on a protected path (CI workflows run
    with repo secrets when the branch is pushed, so they are off-limits).

    Returns (applied, skipped) descriptions.
    """
    checkout_root = checkout.resolve()
    applied: list[str] = []
    skipped: list[str] = []
    for change in changes:
        raw_path = change["path"]
        rel_path = Path(raw_path)
        if rel_path.is_absolute() or any(part in ("..", ".git") for part in rel_path.parts):
            raise ValueError(f"Unsafe change path from model: {raw_path!r}")
        rel = rel_path.as_posix()

        if is_protected_path(rel, protected_paths):
            skipped.append(f"{change['action']} {rel} (protected path)")
            continue

        target = (checkout / rel_path).resolve()
        if not target.is_relative_to(checkout_root):
            raise ValueError(f"Change path escapes repo: {raw_path!r}")

        if change["action"] == "delete":
            if target.exists():
                target.unlink()
                applied.append(f"delete {rel}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(change["content"])
            applied.append(f"{change['action']} {rel}")
    return applied, skipped


def _git(checkout: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(checkout), *args],
                          capture_output=True, text=True, check=check)


def push_branch(cfg: Config, checkout: Path, commit_message: str,
                batch_date: str, batch_index: int) -> str | None:
    branch = f"{cfg.update_branch_prefix}-{batch_date.replace('-', '')}-b{batch_index}"
    _git(checkout, "config", "user.name", "continuous-improvement-bot")
    _git(checkout, "config", "user.email", "ci-bot@users.noreply.github.com")
    _git(checkout, "checkout", "-B", branch)
    _git(checkout, "add", "-A")
    status = _git(checkout, "status", "--porcelain").stdout.strip()
    if not status:
        return None
    _git(checkout, "commit", "-m", commit_message)
    _git(checkout, "push", "-f", "-u", "origin", branch)
    return branch


def run_batch_for_repo(cfg: Config, repo: RepoConfig, suggestions: list[dict],
                       batch_date: str, batch_index: int) -> dict:
    checkout = clone_or_update(repo, cfg.workdir)
    result = generate_changes(cfg, repo, checkout, suggestions)
    applied, skipped = apply_changes(checkout, result.get("changes", []),
                                     cfg.protected_paths)
    branch = None
    if applied:
        branch = push_branch(cfg, checkout, result["commit_message"],
                             batch_date, batch_index)
    return {
        "repo": repo.name,
        "summary": result.get("summary", ""),
        "applied": applied,
        "skipped_protected": skipped,
        "branch": branch,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
