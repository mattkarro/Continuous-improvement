"""CLI entry points for the continuous improvement loop.

  python -m ci_loop.cli review     # daily: snapshot repos, Grok review, create batches
  python -m ci_loop.cli run-batch  # scheduled: run the next due batch through Claude
  python -m ci_loop.cli status     # show recent batches
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from . import batcher, claude_updater, grok_reviewer
from .collector import snapshot_repo
from .config import load_config


def cmd_review() -> int:
    cfg = load_config()
    all_suggestions: list[dict] = []
    for repo in cfg.repos:
        print(f"[review] snapshotting {repo.github} ...")
        snap = snapshot_repo(cfg, repo)
        print(f"[review] zip: {snap.zip_path}")
        print(f"[review] sending to Grok ({cfg.grok_model}) ...")
        try:
            suggestions = grok_reviewer.review_snapshot(cfg, snap)
        except RuntimeError as exc:
            print(f"[review] WARNING: {exc}", file=sys.stderr)
            continue
        print(f"[review] {len(suggestions)} suggestions for {repo.name}")
        all_suggestions.extend(suggestions)

    if not all_suggestions:
        print("[review] no suggestions produced; nothing to batch")
        return 0

    paths = batcher.create_batches(cfg, all_suggestions)
    for p in paths:
        data = json.loads(p.read_text())
        print(f"[review] {p.name}: slot {data['slot_utc']} UTC, "
              f"{len(data['suggestions'])} suggestions ({data['status']})")
    return 0


def cmd_run_batch() -> int:
    cfg = load_config()
    path = batcher.next_due_batch(cfg)
    if path is None:
        print("[run-batch] no batch due; nothing to do")
        return 0

    data = json.loads(path.read_text())
    print(f"[run-batch] running {path} (slot {data['slot_utc']} UTC, "
          f"{len(data['suggestions'])} suggestions)")
    batcher.update_batch(path, status="running",
                         started_at=datetime.now(timezone.utc).isoformat())

    by_repo: dict[str, list[dict]] = {}
    for s in data["suggestions"]:
        by_repo.setdefault(s["repo"], []).append(s)

    results, failed = [], False
    for repo_name, suggestions in by_repo.items():
        repo = next(r for r in cfg.repos if r.name == repo_name)
        print(f"[run-batch] {repo_name}: {len(suggestions)} suggestions -> Claude")
        try:
            result = claude_updater.run_batch_for_repo(
                cfg, repo, suggestions, data["date"], data["index"])
            print(f"[run-batch] {repo_name}: {len(result['applied'])} changes, "
                  f"branch={result['branch']}")
        except Exception as exc:  # keep going on the other repos
            print(f"[run-batch] ERROR on {repo_name}: {exc}", file=sys.stderr)
            result = {"repo": repo_name, "error": str(exc)}
            failed = True
        results.append(result)

    batcher.update_batch(path, status="failed" if failed else "done", results=results)
    return 1 if failed else 0


def cmd_status() -> int:
    cfg = load_config()
    root = cfg.state_dir / "batches"
    if not root.exists():
        print("no batches yet")
        return 0
    for day_dir in sorted(root.iterdir())[-3:]:
        for p in sorted(day_dir.glob("batch_*.json")):
            d = json.loads(p.read_text())
            print(f"{d['date']} batch {d['index']} @ {d['slot_utc']} UTC: "
                  f"{d['status']} ({len(d['suggestions'])} suggestions)")
            for r in d.get("results", []):
                if "error" in r:
                    print(f"    {r['repo']}: ERROR {r['error']}")
                else:
                    print(f"    {r['repo']}: {len(r.get('applied', []))} changes "
                          f"-> {r.get('branch')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="ci_loop")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("review", help="snapshot repos, run Grok review, create batches")
    sub.add_parser("run-batch", help="run the next due batch through Claude")
    sub.add_parser("status", help="show recent batch state")
    args = parser.parse_args()
    return {"review": cmd_review, "run-batch": cmd_run_batch, "status": cmd_status}[
        args.command.replace("_", "-")
    ]()


if __name__ == "__main__":
    sys.exit(main())
