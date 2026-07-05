"""Split Grok suggestions into time-slotted batches for the Claude runs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import Config


def batch_dir(cfg: Config, date: str) -> Path:
    return cfg.state_dir / "batches" / date


def create_batches(cfg: Config, suggestions: list[dict]) -> list[Path]:
    """Round-robin suggestions (already priority-ordered) into slot batches."""
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = batch_dir(cfg, date)
    out_dir.mkdir(parents=True, exist_ok=True)

    ordered = sorted(suggestions, key=lambda s: s.get("priority", 3))
    n = cfg.batches_per_day
    groups: list[list[dict]] = [[] for _ in range(n)]
    for i, suggestion in enumerate(ordered):
        target = groups[i % n]
        if len(target) < cfg.max_suggestions_per_batch:
            target.append(suggestion)

    written = []
    for idx, group in enumerate(groups):
        slot = cfg.slots_utc[idx % len(cfg.slots_utc)]
        path = out_dir / f"batch_{idx}.json"
        path.write_text(json.dumps({
            "date": date,
            "index": idx,
            "slot_utc": slot,
            "status": "pending" if group else "empty",
            "suggestions": group,
            "results": [],
        }, indent=2))
        written.append(path)
    return written


def _slot_passed(date: str, slot: str, now: datetime) -> bool:
    hour, minute = map(int, slot.split(":"))
    slot_dt = datetime.strptime(date, "%Y-%m-%d").replace(
        hour=hour, minute=minute, tzinfo=timezone.utc
    )
    return now >= slot_dt


def next_due_batch(cfg: Config) -> Path | None:
    """Earliest pending batch whose slot time has passed (checks last 3 days)."""
    now = datetime.now(timezone.utc)
    root = cfg.state_dir / "batches"
    if not root.exists():
        return None
    candidates = []
    for day_dir in sorted(root.iterdir())[-3:]:
        for path in sorted(day_dir.glob("batch_*.json")):
            data = json.loads(path.read_text())
            if data["status"] == "pending" and _slot_passed(data["date"], data["slot_utc"], now):
                candidates.append((data["date"], data["slot_utc"], path))
    if not candidates:
        return None
    return sorted(candidates)[0][2]


def update_batch(path: Path, **fields) -> None:
    data = json.loads(path.read_text())
    data.update(fields)
    path.write_text(json.dumps(data, indent=2))
