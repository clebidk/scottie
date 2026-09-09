"""Shared helpers for parsing JSON out of a Claude text response."""
import json
import re

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?|\n?```\s*$")


def extract_json(text):
    """Strip optional markdown code fences and parse the remaining text as JSON.

    Raises json.JSONDecodeError if the result still isn't valid JSON.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = _FENCE_RE.sub("", stripped).strip()
    return json.loads(stripped)
