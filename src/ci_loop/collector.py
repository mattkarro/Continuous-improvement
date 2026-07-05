"""Clone target repos, zip their code, and collect logs for review."""

from __future__ import annotations

import subprocess
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import Config, RepoConfig, github_token

TEXT_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".toml",
    ".md", ".txt", ".cfg", ".ini", ".sh", ".sql", ".html", ".css", ".env.example",
}
EXCLUDE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
MAX_FILE_BYTES = 200_000


@dataclass
class RepoSnapshot:
    repo: RepoConfig
    checkout: Path
    zip_path: Path
    code_digest: str
    logs: str


def clone_or_update(repo: RepoConfig, workdir: Path) -> Path:
    dest = workdir / repo.name
    url = f"https://x-access-token:{github_token()}@github.com/{repo.github}.git"
    if dest.exists():
        subprocess.run(["git", "-C", str(dest), "fetch", "origin"], check=True)
        subprocess.run(
            ["git", "-C", str(dest), "reset", "--hard", "@{upstream}"], check=False
        )
        subprocess.run(["git", "-C", str(dest), "pull", "--ff-only"], check=False)
    else:
        workdir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", url, str(dest)], check=True)
    return dest


def _iter_code_files(checkout: Path):
    for path in sorted(checkout.rglob("*")):
        if not path.is_file():
            continue
        if any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        yield path


def build_zip(checkout: Path, zip_path: Path) -> Path:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in _iter_code_files(checkout):
            zf.write(path, path.relative_to(checkout))
    return zip_path


def build_code_digest(checkout: Path, max_chars: int) -> str:
    """Flatten the repo into a text digest (tree + file contents) for the LLM."""
    tree_lines = []
    file_sections = []
    used = 0
    for path in _iter_code_files(checkout):
        rel = path.relative_to(checkout)
        tree_lines.append(str(rel))
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            continue
        try:
            content = path.read_text(errors="replace")
        except OSError:
            continue
        section = f"\n===== FILE: {rel} =====\n{content}\n"
        if used + len(section) > max_chars:
            file_sections.append(f"\n===== FILE: {rel} ===== (omitted: digest size limit)\n")
            continue
        file_sections.append(section)
        used += len(section)

    return "FILE TREE:\n" + "\n".join(tree_lines) + "\n" + "".join(file_sections)


def collect_logs(checkout: Path, repo: RepoConfig, max_chars: int) -> str:
    """Gather log files by glob, plus recent git activity as a fallback signal."""
    chunks = []
    used = 0
    for pattern in repo.log_globs:
        for path in sorted(checkout.glob(pattern)):
            if not path.is_file():
                continue
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            # Keep the tail of each log — most recent entries matter most.
            text = text[-20_000:]
            section = f"\n===== LOG: {path.relative_to(checkout)} =====\n{text}\n"
            if used + len(section) > max_chars:
                break
            chunks.append(section)
            used += len(section)

    git_log = subprocess.run(
        ["git", "-C", str(checkout), "log", "-20", "--stat", "--date=iso"],
        capture_output=True, text=True,
    ).stdout
    chunks.append(f"\n===== RECENT GIT ACTIVITY =====\n{git_log[:10_000]}\n")
    return "".join(chunks) if chunks else "(no logs found)"


def snapshot_repo(cfg: Config, repo: RepoConfig) -> RepoSnapshot:
    checkout = clone_or_update(repo, cfg.workdir)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    zip_path = cfg.artifacts_dir / date / f"{repo.name}.zip"
    build_zip(checkout, zip_path)
    return RepoSnapshot(
        repo=repo,
        checkout=checkout,
        zip_path=zip_path,
        code_digest=build_code_digest(checkout, cfg.max_code_chars),
        logs=collect_logs(checkout, repo, cfg.max_log_chars),
    )
