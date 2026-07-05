"""Continuous improvement feedback loop.

Daily: snapshot each agent repo (code zip + logs), have Grok review it and
propose improvements/features. The suggestions are batched into Claude prompts
that run at different times of day; each run produces real code updates pushed
to a branch on the target repo.
"""

__version__ = "0.1.0"
