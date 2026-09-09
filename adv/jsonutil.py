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
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as e:
        # Cycle 6 verification: the model occasionally appends trailing
        # content after a complete page.json object (a stray note, a
        # duplicated blob) -- json.loads rejects the whole response over
        # text nobody reads. Fall back to parsing just the first complete
        # JSON value and ignoring everything after it. A genuinely broken
        # JSON body (any other error) still raises, so write_page's own
        # retry-with-a-fresh-call path is unaffected.
        if e.msg != "Extra data":
            raise
        obj, _end = json.JSONDecoder().raw_decode(stripped)
        return obj
