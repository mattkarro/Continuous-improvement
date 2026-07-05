"""CLI entry points for the continuous improvement loop.

  python -m ci_loop.cli review     # daily: snapshot repos, Grok review, create batches
  python -m ci_loop.cli run-batch  # api mode: run the next due batch through Claude
  python -m ci_loop.cli status     # show recent batches

Logs are human-readable by default; set CI_LOOP_LOG_FORMAT=json for
structured output. Each command emits a metrics summary line at the end.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone

from . import batcher, claude_updater, grok_reviewer, todo_writer
from .collector import clone_or_update, head_sha, snapshot_from_checkout
from .config import load_config, load_dotenv
from .obs import Metrics, log


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
        log(f"[state] marked stale: {p}", logging.WARNING)


def cmd_review(force: bool = False) -> int:
    cfg = load_config()
    metrics = Metrics()
    _report_expired(batcher.expire_stale(cfg))

    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if any(batcher.batch_dir(cfg, date).glob("batch_*.json")):
        log(f"[review] batches for {date} already exist; not overwriting. "
            f"Delete state/batches/{date}/ to re-run today's review.")
        return 0

    last_review = _load_last_review(cfg)
    all_suggestions: list[dict] = []
    outcomes: dict[str, str] = {}
    for repo in cfg.repos:
        log(f"[review] refreshing {repo.github} (branch: {repo.branch or 'default'})")
        try:
            checkout = clone_or_update(repo, cfg.workdir)
        except Exception as exc:  # e.g. configured branch no longer exists
            log(f"[review] cannot check out {repo.name}: {exc}", logging.WARNING)
            metrics.inc("repos_failed")
            outcomes[repo.name] = "checkout-failed"
            continue
        sha = head_sha(checkout)
        if not force and last_review.get(repo.name, {}).get("sha") == sha:
            log(f"[review] {repo.name}: unchanged since last review "
                f"({sha[:10]}), skipping (--force to override)")
            metrics.inc("repos_skipped_unchanged")
            outcomes[repo.name] = "skipped-unchanged"
            continue

        snap = snapshot_from_checkout(cfg, repo, checkout)
        log(f"[review] zip: {snap.zip_path}")
        log(f"[review] sending to Grok ({cfg.grok_model})")
        try:
            suggestions = grok_reviewer.review_snapshot(cfg, snap)
        except RuntimeError as exc:
            log(f"[review] Grok review failed for {repo.name}: {exc}", logging.WARNING)
            metrics.inc("repos_failed")
            outcomes[repo.name] = "grok-failed"
            continue  # sha not recorded -> this repo is retried next run
        log(f"[review] {len(suggestions)} suggestions for {repo.name}")
        metrics.inc("repos_reviewed")
        metrics.inc("suggestions", len(suggestions))
        outcomes[repo.name] = f"reviewed ({len(suggestions)} suggestions)"
        all_suggestions.extend(suggestions)
        last_review[repo.name] = {
            "sha": sha,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }

    _save_last_review(cfg, last_review)
    log("[review] per-repo outcome: "
        + "; ".join(f"{name}: {result}" for name, result in outcomes.items()))
    if not all_suggestions:
        log("[review] no suggestions produced; nothing to batch")
        metrics.emit("review")
        return 0

    paths = batcher.create_batches(cfg, all_suggestions)
    metrics.inc("batches_created", len(paths))
    for p in paths:
        data = json.loads(p.read_text())
        log(f"[review] {p.name}: slot {data['slot_utc']} UTC, "
            f"{len(data['suggestions'])} suggestions ({data['status']})")

    if cfg.mode == "todo":
        if cfg.todo_destination == "repo":
            pushed = todo_writer.push_repo_todos(cfg, paths)
            for name, info in pushed.items():
                log(f"[review] todos pushed to {name}: {', '.join(info['files'])}")
                metrics.inc("todos_pushed", len(info["files"]))
                # The todo commit must not count as "repo changed" tomorrow.
                if name in last_review:
                    last_review[name]["sha"] = info["sha"]
            _save_last_review(cfg, last_review)
            for p in paths:
                if json.loads(p.read_text())["status"] == "pending":
                    batcher.update_batch(p, status="delegated")
            log("[review] todo mode: open a Claude Code session in each repo "
                "and work its todos/ folder (no Claude API usage)")
        else:
            todos = todo_writer.write_todos(cfg, paths)
            metrics.inc("todos_written", len(todos))
            for t in todos:
                log(f"[review] todo prompt written: {t}")
            log("[review] todo mode: work these in Claude Code sessions through "
                "the day (no Claude API usage)")
    metrics.emit("review")
    return 0


def cmd_run_batch() -> int:
    cfg = load_config()
    if cfg.mode != "api":
        log("[run-batch] mode is 'todo' — batches are worked via the todo "
            "files using Claude Code, not the Claude API. Set mode: api in "
            "config.yaml to enable automatic runs.")
        return 0
    metrics = Metrics()
    _report_expired(batcher.expire_stale(cfg))
    path = batcher.next_due_batch(cfg)
    if path is None:
        log("[run-batch] no batch due; nothing to do")
        return 0

    data = json.loads(path.read_text())
    log(f"[run-batch] running {path} (slot {data['slot_utc']} UTC, "
        f"{len(data['suggestions'])} suggestions)")
    batcher.update_batch(path, status="running",
                         started_at=datetime.now(timezone.utc).isoformat())

    by_repo: dict[str, list[dict]] = {}
    for s in data["suggestions"]:
        by_repo.setdefault(s["repo"], []).append(s)

    results, failed = [], False
    for repo_name, suggestions in by_repo.items():
        repo = next(r for r in cfg.repos if r.name == repo_name)
        log(f"[run-batch] {repo_name}: {len(suggestions)} suggestions -> Claude")
        try:
            result = claude_updater.run_batch_for_repo(
                cfg, repo, suggestions, data["date"], data["index"])
            log(f"[run-batch] {repo_name}: {len(result['applied'])} changes, "
                f"branch={result['branch']}")
            metrics.inc("changes_applied", len(result["applied"]))
            metrics.inc("changes_skipped_protected", len(result.get("skipped_protected", [])))
            for s in result.get("skipped_protected", []):
                log(f"[run-batch] {repo_name}: skipped {s}", logging.WARNING)
            for o in result.get("suggestion_outcomes", []):
                if o.get("outcome") != "implemented":
                    log(f"[run-batch] {repo_name}: suggestion "
                        f"{o.get('outcome')}: {o.get('title')} — {o.get('note')}",
                        logging.WARNING)
            if result.get("verify_failures"):
                failed = True
                metrics.inc("verify_failures", len(result["verify_failures"]))
                for f in result["verify_failures"]:
                    log(f"[run-batch] {repo_name}: verify FAILED (not pushed): {f}",
                        logging.ERROR)
        except Exception as exc:  # keep going on the other repos
            log(f"[run-batch] ERROR on {repo_name}: {exc}", logging.ERROR)
            result = {"repo": repo_name, "error": str(exc)}
            metrics.inc("repo_errors")
            failed = True
        results.append(result)

    batcher.update_batch(path, status="failed" if failed else "done", results=results)
    metrics.emit("run-batch")
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
                for o in r.get("suggestion_outcomes", []):
                    print(f"        [{o.get('outcome')}] {o.get('title')}")
    return 0


def main() -> int:
    loaded = load_dotenv()
    if loaded:
        log(f"[env] loaded from .env: {', '.join(loaded)}")
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
