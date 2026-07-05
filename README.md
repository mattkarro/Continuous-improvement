# Continuous Improvement Feedback Loop

Automated daily improvement cycle for the agent repos
(`ai-gambling-agent`, `ai-stock-agent`, `ai-crypto-trader`):

```
           (daily, 05:00 UTC)
 ┌─────────────────────────────────────────────┐
 │ 1. Snapshot each repo: code zip + logs      │
 │ 2. Grok API reviews code + logs             │
 │ 3. Suggestions split into 4 time-slot       │
 │    batches (state/batches/YYYY-MM-DD/)      │
 └─────────────────────────────────────────────┘
           (4 slots/day: 06:15, 11:45, 17:20, 22:50 UTC)
 ┌─────────────────────────────────────────────┐
 │ 4. Claude implements the batch's            │
 │    suggestions as real code changes         │
 │ 5. Changes pushed to a `ci/improvements-*`  │
 │    branch on the target repo for review     │
 └─────────────────────────────────────────────┘
```

## Modes

Step 4 runs in one of two modes (`mode:` in `config.yaml`):

- **`todo` (default):** the daily review also writes a ready-to-run prompt
  file per batch under `state/todos/<date>/batch_N.md`. Open a Claude Code
  session around each batch's suggested slot time, paste the prompt (or point
  the session at the file), and it implements the changes, pushes the branch,
  and marks the batch done. All Claude work runs on your **Claude
  subscription** — the only API cost is the daily Grok review (~cents/day).
- **`api`:** the `batch-runner` workflow calls the Claude API automatically at
  each slot — fully autonomous, but metered API cost (~$4–5/day at full digest
  size with Opus). To enable: set `mode: api`, uncomment the cron block in
  `.github/workflows/batch-runner.yml`, and add the `ANTHROPIC_API_KEY` secret.

Either way, batches are tied to different times of day so usage is spread
across separate token-usage windows instead of one burst.

## Components

| Path | Purpose |
|---|---|
| `src/ci_loop/collector.py` | Clones repos, builds code zips + text digest, collects logs |
| `src/ci_loop/grok_reviewer.py` | Sends digest + logs to the Grok API, parses JSON suggestions |
| `src/ci_loop/batcher.py` | Splits suggestions into time-slotted batches |
| `src/ci_loop/todo_writer.py` | Renders batches as Claude Code prompt files (todo mode) |
| `src/ci_loop/claude_updater.py` | Claude API call (structured output) → applies changes → pushes branch (api mode) |
| `src/ci_loop/cli.py` | `review`, `run-batch`, `status` commands |
| `.github/workflows/daily-review.yml` | Daily Grok review + batching + todo prompts |
| `.github/workflows/batch-runner.yml` | Claude API update runs (api mode only, crons commented out) |

Code snapshot zips are saved under `artifacts/<date>/<repo>.zip` and uploaded
as workflow artifacts (14-day retention). Since the Grok chat API takes text,
the zip's contents are also flattened into a text digest that goes into the
review prompt; the zip itself is the archival copy of exactly what was reviewed.

## Setup

Add these **Actions secrets** to this repository
(Settings → Secrets and variables → Actions):

| Secret | What it is |
|---|---|
| `XAI_API_KEY` | x.ai API key (Grok reviews) |
| `REPO_ACCESS_TOKEN` | GitHub PAT with `repo` scope on the three agent repos (clone + push branches) |
| `ANTHROPIC_API_KEY` | Anthropic API key — **only needed in `api` mode** |

## Running locally

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in keys, then export them
export PYTHONPATH=src

python -m ci_loop.cli review      # snapshot + Grok review + batches (+ todo prompts in todo mode)
python -m ci_loop.cli run-batch   # api mode only: run the next due batch through the Claude API
python -m ci_loop.cli status      # see batch states and pushed branches
```

## Output

Each Claude run pushes a branch like `ci/improvements-20260705-b2` to the
target repo containing the implemented changes, with a commit message written
by Claude. Review the branch and merge (or open a PR) when you're happy with it.
Batch state — suggestions, run status, result branches — is committed under
`state/batches/` so every run is auditable.

## Safety guardrails

- **Secret hygiene:** files matching `security.secret_file_patterns` (`.env*`,
  keys, credentials, etc.) are never included in zips, code digests, or log
  collection, and credential-shaped strings (API keys, tokens, `password=...`)
  are redacted from everything sent to Grok/Claude or archived.
- **Protected paths:** generated changes may never touch
  `security.protected_paths` (default `.github/`, `.git/`) — pushed workflow
  changes would otherwise run with the target repo's secrets. Enforced in
  api mode; stated as a hard rule in todo-mode prompts.
- **Path containment:** model-emitted file paths are rejected if absolute,
  containing `..`, or resolving outside the repo checkout.
- Improvement branches are never auto-merged — human review of each
  `ci/improvements-*` branch is the final control. Keep it that way.

## Tuning

Everything is in `config.yaml`: which repos are watched, log glob patterns,
Grok/Claude models, batches per day, the UTC time slots, and digest size
limits. If you change `slots_utc`, update the cron entries in
`.github/workflows/batch-runner.yml` to match.
