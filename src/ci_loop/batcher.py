"""Split Grok suggestions into time-slotted batches for the Claude runs."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Config


def batch_dir(cfg: Config, date: str) -> Path:
    return cfg.state_dir / "batches" / date


def create_batches(cfg: Config, suggestions: list[dict]) -> list[Path]:
    """Round-robin suggestions (already priority-ordered) into slot batches.

    Refuses to overwrite a day that already has batches — re-running the
    review must not wipe the status of batches already worked.
    """
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = batch_dir(cfg, date)
    if out_dir.exists() and any(out_dir.glob("batch_*.json")):
        raise FileExistsError(
            f"Batches for {date} already exist in {out_dir}; "
            f"delete that directory to re-run today's review."
        )
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


def _iter_batches(cfg: Config):
    root = cfg.state_dir / "batches"
    if not root.exists():
        return
    for day_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for path in sorted(day_dir.glob("batch_*.json")):
            yield path, json.loads(path.read_text())


def next_due_batch(cfg: Config) -> Path | None:
    """Earliest pending batch whose slot time has passed.

    Run expire_stale() first so long-forgotten batches don't suddenly run
    against code that has since changed.
    """
    now = datetime.now(timezone.utc)
    candidates = [
        (data["date"], data["slot_utc"], path)
        for path, data in _iter_batches(cfg)
        if data["status"] == "pending" and _slot_passed(data["date"], data["slot_utc"], now)
    ]
    return sorted(candidates)[0][2] if candidates else None


def expire_stale(cfg: Config, pending_days: int = 3, running_hours: int = 6) -> list[Path]:
    """Close out batches that will never legitimately complete.

    - pending for more than `pending_days`: suggestions are stale relative to
      the code by now — mark stale rather than run them late.
    - running for more than `running_hours` (or with no start time): the
      runner crashed mid-batch; mark stale so it's visible instead of stuck.
    """
    now = datetime.now(timezone.utc)
    expired = []
    for path, data in _iter_batches(cfg):
        status = data["status"]
        if status == "pending":
            batch_day = datetime.strptime(data["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if now - batch_day >= timedelta(days=pending_days):
                update_batch(path, status="stale",
                             stale_reason=f"pending for more than {pending_days} days")
                expired.append(path)
        elif status == "running":
            started = data.get("started_at")
            started_dt = datetime.fromisoformat(started) if started else None
            if started_dt is None or now - started_dt >= timedelta(hours=running_hours):
                update_batch(path, status="stale",
                             stale_reason=f"running for more than {running_hours}h "
                                          f"(runner likely crashed)")
                expired.append(path)
    return expired


def update_batch(path: Path, **fields) -> None:
    data = json.loads(path.read_text())
    data.update(fields)
    path.write_text(json.dumps(data, indent=2))
