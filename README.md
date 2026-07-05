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
           (4x daily: 06:15, 11:45, 17:20, 22:50 UTC)
 ┌─────────────────────────────────────────────┐
 │ 4. Claude implements the batch's            │
 │    suggestions as real code changes         │
 │ 5. Changes pushed to a `ci/improvements-*`  │
 │    branch on the target repo for review     │
 └─────────────────────────────────────────────┘
```

Running the Claude batches at different times of day spreads API usage
across separate token-usage windows instead of one burst.

## Components

| Path | Purpose |
|---|---|
| `src/ci_loop/collector.py` | Clones repos, builds code zips + text digest, collects logs |
| `src/ci_loop/grok_reviewer.py` | Sends digest + logs to the Grok API, parses JSON suggestions |
| `src/ci_loop/batcher.py` | Splits suggestions into time-slotted batches |
| `src/ci_loop/claude_updater.py` | Claude API call (structured output) → applies changes → pushes branch |
| `src/ci_loop/cli.py` | `review`, `run-batch`, `status` commands |
| `.github/workflows/daily-review.yml` | Daily Grok review + batching |
| `.github/workflows/batch-runner.yml` | 4x-daily Claude update runs |

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
| `ANTHROPIC_API_KEY` | Anthropic API key (Claude code updates) |
| `REPO_ACCESS_TOKEN` | GitHub PAT with `repo` scope on the three agent repos (clone + push branches) |

## Running locally

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in keys, then export them
export PYTHONPATH=src

python -m ci_loop.cli review      # snapshot + Grok review + create today's batches
python -m ci_loop.cli run-batch   # run the next due batch through Claude
python -m ci_loop.cli status      # see batch states and pushed branches
```

## Output

Each Claude run pushes a branch like `ci/improvements-20260705-b2` to the
target repo containing the implemented changes, with a commit message written
by Claude. Review the branch and merge (or open a PR) when you're happy with it.
Batch state — suggestions, run status, result branches — is committed under
`state/batches/` so every run is auditable.

## Tuning

Everything is in `config.yaml`: which repos are watched, log glob patterns,
Grok/Claude models, batches per day, the UTC time slots, and digest size
limits. If you change `slots_utc`, update the cron entries in
`.github/workflows/batch-runner.yml` to match.
