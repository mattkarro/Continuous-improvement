import json
from datetime import datetime, timedelta, timezone

import pytest

from ci_loop import batcher


def _suggestion(i, priority):
    return {"title": f"s{i}", "priority": priority, "repo": "repo-a",
            "github": "owner/repo-a", "type": "improvement",
            "details": "d", "files": []}


class TestCreateBatches:
    def test_round_robin_by_priority(self, cfg):
        suggestions = [_suggestion(i, (i % 5) + 1) for i in range(8)]
        paths = batcher.create_batches(cfg, suggestions)
        assert len(paths) == cfg.batches_per_day
        batches = [json.loads(p.read_text()) for p in paths]
        # Highest priority (lowest number) lands in the earliest batch.
        assert batches[0]["suggestions"][0]["priority"] == 1
        total = sum(len(b["suggestions"]) for b in batches)
        assert total == 8
        assert {b["slot_utc"] for b in batches} == set(cfg.slots_utc)

    def test_refuses_to_overwrite_same_day(self, cfg):
        batcher.create_batches(cfg, [_suggestion(0, 1)])
        with pytest.raises(FileExistsError):
            batcher.create_batches(cfg, [_suggestion(1, 1)])


class TestSlotLogic:
    def test_slot_passed(self):
        now = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)
        assert batcher._slot_passed("2026-07-05", "06:15", now)
        assert not batcher._slot_passed("2026-07-05", "17:20", now)
        assert batcher._slot_passed("2026-07-04", "22:50", now)


def _write_batch(cfg, date, index, status, **extra):
    day = cfg.state_dir / "batches" / date
    day.mkdir(parents=True, exist_ok=True)
    path = day / f"batch_{index}.json"
    path.write_text(json.dumps({
        "date": date, "index": index, "slot_utc": "06:15",
        "status": status, "suggestions": [_suggestion(0, 1)], "results": [],
        **extra,
    }))
    return path


class TestExpireStale:
    def test_old_pending_marked_stale(self, cfg):
        old = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")
        path = _write_batch(cfg, old, 0, "pending")
        expired = batcher.expire_stale(cfg)
        assert path in expired
        assert json.loads(path.read_text())["status"] == "stale"

    def test_stuck_running_marked_stale(self, cfg):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        started = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
        path = _write_batch(cfg, today, 0, "running", started_at=started)
        assert path in batcher.expire_stale(cfg)

    def test_fresh_pending_untouched(self, cfg):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = _write_batch(cfg, today, 0, "pending")
        assert batcher.expire_stale(cfg) == []
        assert json.loads(path.read_text())["status"] == "pending"

    def test_stale_never_picked_as_due(self, cfg):
        old = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")
        _write_batch(cfg, old, 0, "pending")
        batcher.expire_stale(cfg)
        assert batcher.next_due_batch(cfg) is None
