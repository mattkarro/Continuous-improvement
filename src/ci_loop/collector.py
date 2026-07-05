"""Clone target repos, zip their code, and collect logs for review."""

from __future__ import annotations

import fnmatch
import re
import subprocess
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import Config, RepoConfig, github_token

TEXT_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".toml",
    ".md", ".txt", ".cfg", ".ini", ".sh", ".sql", ".html", ".css",
}
EXCLUDE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
MAX_FILE_BYTES = 200_000

# Credential-shaped content is scrubbed from anything sent to an external
# API (Grok/Claude) or archived in a zip. Conservative patterns: better to
# miss an exotic key format than to mangle ordinary code.
REDACTION_PATTERNS = [
    re.compile(r"\b(sk|xai|ghp|gho|ghs|glpat|xoxb|xoxp)[-_][A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-_.=+/]{16,}"),
    re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password|passwd|private[_-]?key|auth)"
        r"(\s*[=:]\s*)['\"]?[A-Za-z0-9\-_.=+/]{12,}['\"]?"
    ),
]


def redact(text: str) -> str:
    for pattern in REDACTION_PATTERNS[:-1]:
        text = pattern.sub("[REDACTED]", text)
    # Keep the key name, drop the value, for key=value shapes.
    text = REDACTION_PATTERNS[-1].sub(r"\1\2[REDACTED]", text)
    return text


def is_secret_file(rel_path: Path, patterns: list[str]) -> bool:
    """True if a file should never leave the machine (zip, digest, or logs)."""
    name = rel_path.name.lower()
    rel = rel_path.as_posix().lower()
    return any(
        fnmatch.fnmatch(name, p.lower()) or fnmatch.fnmatch(rel, p.lower())
        for p in patterns
    )


@dataclass
class RepoSnapshot:
    repo: RepoConfig
    checkout: Path
    zip_path: Path
    code_digest: str
    logs: str


def _default_branch(dest: Path) -> str:
    """Resolve the remote's default branch (e.g. 'main' or 'master')."""
    result = subprocess.run(
        ["git", "-C", str(dest), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        subprocess.run(["git", "-C", str(dest), "remote", "set-head", "origin", "--auto"],
                       check=True, capture_output=True)
        result = subprocess.run(
            ["git", "-C", str(dest), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
            capture_output=True, text=True, check=True,
        )
    return result.stdout.strip().removeprefix("origin/")


def clone_or_update(repo: RepoConfig, workdir: Path) -> Path:
    """Clone or refresh a repo and ALWAYS leave it clean on the default branch.

    A previous run may have left the checkout on a ci/improvements-* branch
    or with leftover files; without this reset, later snapshots and updates
    would build on top of the improvement branch instead of the real code.
    """
    dest = workdir / repo.name
    url = f"https://x-access-token:{github_token()}@github.com/{repo.github}.git"
    if dest.exists():
        subprocess.run(["git", "-C", str(dest), "fetch", "origin", "--prune"], check=True)
    else:
        workdir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", url, str(dest)], check=True)

    branch = _default_branch(dest)
    subprocess.run(["git", "-C", str(dest), "checkout", "-B", branch, f"origin/{branch}"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(dest), "clean", "-fd"], check=True, capture_output=True)
    return dest


def _iter_code_files(checkout: Path, secret_patterns: list[str]):
    for path in sorted(checkout.rglob("*")):
        if not path.is_file():
            continue
        if any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        if is_secret_file(path.relative_to(checkout), secret_patterns):
            continue
        yield path


def build_zip(checkout: Path, zip_path: Path, secret_patterns: list[str]) -> Path:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in _iter_code_files(checkout, secret_patterns):
            zf.write(path, path.relative_to(checkout))
    return zip_path


def build_code_digest(checkout: Path, max_chars: int, secret_patterns: list[str]) -> str:
    """Flatten the repo into a text digest (tree + file contents) for the LLM."""
    tree_lines = []
    file_sections = []
    used = 0
    for path in _iter_code_files(checkout, secret_patterns):
        rel = path.relative_to(checkout)
        tree_lines.append(str(rel))
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            continue
        try:
            content = redact(path.read_text(errors="replace"))
        except OSError:
            continue
        section = f"\n===== FILE: {rel} =====\n{content}\n"
        if used + len(section) > max_chars:
            file_sections.append(f"\n===== FILE: {rel} ===== (omitted: digest size limit)\n")
            continue
        file_sections.append(section)
        used += len(section)

    return "FILE TREE:\n" + "\n".join(tree_lines) + "\n" + "".join(file_sections)


def collect_logs(checkout: Path, repo: RepoConfig, max_chars: int,
                 secret_patterns: list[str]) -> str:
    """Gather log files by glob, plus recent git activity as a fallback signal."""
    chunks = []
    used = 0
    for pattern in repo.log_globs:
        for path in sorted(checkout.glob(pattern)):
            if not path.is_file():
                continue
            if is_secret_file(path.relative_to(checkout), secret_patterns):
                continue
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            # Keep the tail of each log — most recent entries matter most.
            text = redact(text[-20_000:])
            section = f"\n===== LOG: {path.relative_to(checkout)} =====\n{text}\n"
            if used + len(section) > max_chars:
                break
            chunks.append(section)
            used += len(section)

    git_log = subprocess.run(
        ["git", "-C", str(checkout), "log", "-20", "--stat", "--date=iso"],
        capture_output=True, text=True,
    ).stdout
    chunks.append(f"\n===== RECENT GIT ACTIVITY =====\n{redact(git_log[:10_000])}\n")
    return "".join(chunks) if chunks else "(no logs found)"


def head_sha(checkout: Path) -> str:
    return subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


def snapshot_from_checkout(cfg: Config, repo: RepoConfig, checkout: Path) -> RepoSnapshot:
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    zip_path = cfg.artifacts_dir / date / f"{repo.name}.zip"
    build_zip(checkout, zip_path, cfg.secret_file_patterns)
    return RepoSnapshot(
        repo=repo,
        checkout=checkout,
        zip_path=zip_path,
        code_digest=build_code_digest(checkout, cfg.max_code_chars, cfg.secret_file_patterns),
        logs=collect_logs(checkout, repo, cfg.max_log_chars, cfg.secret_file_patterns),
    )


def snapshot_repo(cfg: Config, repo: RepoConfig) -> RepoSnapshot:
    checkout = clone_or_update(repo, cfg.workdir)
    return snapshot_from_checkout(cfg, repo, checkout)
