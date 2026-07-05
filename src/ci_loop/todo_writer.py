"""Write batches as ready-to-run Claude Code prompt files (todo mode).

Instead of calling the Claude API, each batch becomes a markdown file under
state/todos/<date>/ containing a complete prompt. Paste it into a Claude Code
session (or point a session at the file) at any time of day — the work then
runs on your Claude subscription instead of metered API tokens.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import Config


def todo_dir(cfg: Config, date: str) -> Path:
    return cfg.state_dir / "todos" / date


def _suggestion_block(i: int, s: dict) -> str:
    files = ", ".join(s.get("files", [])) or "unknown"
    return (
        f"{i}. **{s.get('title', 'untitled')}** "
        f"({s.get('type', 'improvement')}, priority {s.get('priority', '?')})\n"
        f"   - Details: {s.get('details', '')}\n"
        f"   - Likely files: {files}\n"
    )


def write_todo(cfg: Config, batch_path: Path) -> Path | None:
    """Render one batch file into a todo prompt file. Returns None if empty."""
    batch = json.loads(batch_path.read_text())
    suggestions = batch.get("suggestions", [])
    if not suggestions:
        return None

    date, index, slot = batch["date"], batch["index"], batch["slot_utc"]
    branch = f"{cfg.update_branch_prefix}-{date.replace('-', '')}-b{index}"

    by_repo: dict[str, list[dict]] = {}
    for s in suggestions:
        by_repo.setdefault(s["github"], []).append(s)

    repo_sections = []
    for github, items in by_repo.items():
        blocks = "".join(_suggestion_block(i, s) for i, s in enumerate(items, 1))
        repo_sections.append(f"### Repo: `{github}`\n\n{blocks}")

    state_rel = batch_path.relative_to(cfg.state_dir.parent)
    prompt = f"""\
# Improvement batch {date} / batch {index}

- **Suggested work window:** around {slot} UTC (spreads usage through the day)
- **Status:** pending — delete this file and update the batch state when done
- **Source:** daily Grok review of code + logs

## Prompt

Copy everything below the line into a Claude Code session that has access to
the target repo(s) and to `mattkarro/Continuous-improvement`.

---

Implement the following reviewed improvement suggestions.

{chr(10).join(repo_sections)}

Instructions:
1. For each repo above: create branch `{branch}` from the default branch,
   implement the suggestions as focused, working changes that match the
   existing code style, and skip (with a note) anything unsafe or impossible
   from the available context. Run tests if the repo has them.
2. Commit with a clear message and push the branch with
   `git push -u origin {branch}`.
3. In `mattkarro/Continuous-improvement`, record completion:
   - In `{state_rel}` set `"status": "done"` and add a `results` entry per
     repo: `{{"repo", "summary", "applied", "branch"}}`.
   - Delete this todo file.
   - Commit both state changes and push.
"""

    out = todo_dir(cfg, date) / f"batch_{index}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(prompt)
    return out


def write_todos(cfg: Config, batch_paths: list[Path]) -> list[Path]:
    written = []
    for path in batch_paths:
        todo = write_todo(cfg, path)
        if todo is not None:
            written.append(todo)
    return written
