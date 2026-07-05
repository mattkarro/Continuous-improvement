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

from . import batcher, claude_updater, grok_reviewer, todo_writer
from .collector import clone_or_update, head_sha, snapshot_from_checkout
from .config import load_config


def _last_review_path(cfg):
    return cfg.state_dir / "last_review.json"


def _load_last_review(cfg) -> dict:
    path = _last_review_path(cfg)
    return json.loads(path.read_text()) if path.exists() else {}


def _save_last_review(cfg, data: dict) -> None:
    path = _last_review_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _report_expired(expired) -> None:
    for p in expired:
        print(f"[state] marked stale: {p}")


def cmd_review(force: bool = False) -> int:
    cfg = load_config()
    _report_expired(batcher.expire_stale(cfg))

    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if any(batcher.batch_dir(cfg, date).glob("batch_*.json")):
        print(f"[review] batches for {date} already exist; not overwriting. "
              f"Delete state/batches/{date}/ to re-run today's review.")
        return 0

    last_review = _load_last_review(cfg)
    all_suggestions: list[dict] = []
    for repo in cfg.repos:
        print(f"[review] refreshing {repo.github} ...")
        checkout = clone_or_update(repo, cfg.workdir)
        sha = head_sha(checkout)
        if not force and last_review.get(repo.name, {}).get("sha") == sha:
            print(f"[review] {repo.name}: unchanged since last review "
                  f"({sha[:10]}), skipping (--force to override)")
            continue

        snap = snapshot_from_checkout(cfg, repo, checkout)
        print(f"[review] zip: {snap.zip_path}")
        print(f"[review] sending to Grok ({cfg.grok_model}) ...")
        try:
            suggestions = grok_reviewer.review_snapshot(cfg, snap)
        except RuntimeError as exc:
            print(f"[review] WARNING: {exc}", file=sys.stderr)
            continue  # sha not recorded -> this repo is retried next run
        print(f"[review] {len(suggestions)} suggestions for {repo.name}")
        all_suggestions.extend(suggestions)
        last_review[repo.name] = {
            "sha": sha,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }

    _save_last_review(cfg, last_review)
    if not all_suggestions:
        print("[review] no suggestions produced; nothing to batch")
        return 0

    paths = batcher.create_batches(cfg, all_suggestions)
    for p in paths:
        data = json.loads(p.read_text())
        print(f"[review] {p.name}: slot {data['slot_utc']} UTC, "
              f"{len(data['suggestions'])} suggestions ({data['status']})")

    if cfg.mode == "todo":
        todos = todo_writer.write_todos(cfg, paths)
        for t in todos:
            print(f"[review] todo prompt written: {t}")
        print("[review] todo mode: work these in Claude Code sessions through "
              "the day (no Claude API usage)")
    return 0


def cmd_run_batch() -> int:
    cfg = load_config()
    if cfg.mode != "api":
        print("[run-batch] mode is 'todo' — batches are worked via the prompt "
              "files in state/todos/ using Claude Code, not the Claude API. "
              "Set mode: api in config.yaml to enable automatic runs.")
        return 0
    _report_expired(batcher.expire_stale(cfg))
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
            for s in result.get("skipped_protected", []):
                print(f"[run-batch] {repo_name}: SKIPPED {s}")
            if result.get("verify_failures"):
                failed = True
                for f in result["verify_failures"]:
                    print(f"[run-batch] {repo_name}: VERIFY FAILED (not pushed): {f}",
                          file=sys.stderr)
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
            if d.get("stale_reason"):
                print(f"    stale: {d['stale_reason']}")
            for r in d.get("results", []):
                if "error" in r:
                    print(f"    {r['repo']}: ERROR {r['error']}")
                elif r.get("verify_failures"):
                    print(f"    {r['repo']}: VERIFY FAILED, not pushed "
                          f"({len(r['verify_failures'])} failures)")
                else:
                    print(f"    {r['repo']}: {len(r.get('applied', []))} changes "
                          f"-> {r.get('branch')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="ci_loop")
    sub = parser.add_subparsers(dest="command", required=True)
    p_review = sub.add_parser("review", help="snapshot repos, run Grok review, create batches")
    p_review.add_argument("--force", action="store_true",
                          help="review repos even if unchanged since the last review")
    sub.add_parser("run-batch", help="run the next due batch through Claude")
    sub.add_parser("status", help="show recent batch state")
    args = parser.parse_args()
    if args.command == "review":
        return cmd_review(force=args.force)
    if args.command == "run-batch":
        return cmd_run_batch()
    return cmd_status()


if __name__ == "__main__":
    sys.exit(main())
