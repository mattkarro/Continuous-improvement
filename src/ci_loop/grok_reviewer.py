"""Send code + logs to the Grok API (x.ai) and parse improvement suggestions."""

from __future__ import annotations

import json
import os
import re
import time

import requests

from .collector import RepoSnapshot
from .config import Config

SYSTEM_PROMPT = """\
You are a senior software reviewer for automated trading/betting agents.
You are given a full snapshot of a repository's code and its recent logs.

Identify concrete improvements and new features. Consider: bugs and error
patterns visible in the logs, risk management, strategy quality, reliability,
observability, testing, and missing features that would make the agent more
profitable or safer.

Respond with ONLY a JSON object, no prose, in this shape:
{
  "suggestions": [
    {
      "title": "short imperative title",
      "type": "bugfix" | "improvement" | "feature",
      "priority": 1-5,           // 1 = most important
      "details": "what to change and why, specific enough to implement",
      "files": ["paths/likely/involved.py"]
    }
  ]
}
Return at most 10 suggestions, ordered by priority.
"""


def _api_key() -> str:
    key = os.environ.get("XAI_API_KEY")
    if not key:
        raise RuntimeError("XAI_API_KEY is not set")
    return key


def _extract_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to the largest {...} span (handles markdown fences / prose).
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise ValueError(f"Grok response was not valid JSON: {text[:500]}")


def _call_grok(cfg: Config, messages: list[dict]) -> str:
    """POST to Grok with backoff on transport/transient errors only."""
    payload = {"model": cfg.grok_model, "messages": messages, "temperature": 0.2}
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            resp = requests.post(
                f"{cfg.grok_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {_api_key()}"},
                json=payload,
                timeout=600,
            )
            if resp.status_code in (429, 500, 502, 503, 529):
                raise RuntimeError(f"Grok transient error {resp.status_code}: {resp.text[:200]}")
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except (requests.RequestException, RuntimeError, KeyError) as exc:
            last_error = exc
            time.sleep(2 ** (attempt + 1))
    raise RuntimeError(f"Grok request failed after retries: {last_error}")


def review_snapshot(cfg: Config, snap: RepoSnapshot) -> list[dict]:
    """Return a list of suggestion dicts for one repo."""
    user_content = (
        f"Repository: {snap.repo.github}\n\n"
        f"--- RECENT LOGS ---\n{snap.logs}\n\n"
        f"--- CODE SNAPSHOT ---\n{snap.code_digest}"
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    text = _call_grok(cfg, messages)
    try:
        data = _extract_json(text)
    except (ValueError, json.JSONDecodeError):
        # One cheap corrective retry — do NOT resend the full prompt N times
        # for what is a deterministic formatting failure.
        messages = messages + [
            {"role": "assistant", "content": text[:4000]},
            {"role": "user", "content": "Your previous reply was not valid JSON. "
                                        "Reply with ONLY the JSON object in the required schema."},
        ]
        text = _call_grok(cfg, messages)
        data = _extract_json(text)

    suggestions = data.get("suggestions", [])
    for s in suggestions:
        s["repo"] = snap.repo.name
        s["github"] = snap.repo.github
    return suggestions
