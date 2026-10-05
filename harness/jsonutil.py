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


# ---------------------------------------------------------------------------
# Cycle 74: a tolerant parse for the writer's page.json. Five of the eight
# listicle STOPs of 2026-10-05 had a repair call whose page.json did not parse
# ("Expecting property name enclosed in double quotes" ~col 5400 -- a trailing
# comma before "}"; "Expecting ',' delimiter" -- an ad quote's own quotation
# marks left unescaped inside a JSON string). Each one cost the attempt (or the
# run: 20261005-153647-price-comparison-v2-spyo exited on two in a row). The
# two fixes below are lossless -- they only delete a comma that has nothing
# after it, or escape a quotation mark so it stays part of the string it is
# in -- and the caller still validates the result against the schema and runs
# every gate on it.
# ---------------------------------------------------------------------------

_MAX_TOLERANT_FIXES = 40


def _strip_trailing_commas(text):
    """`text` with every comma that is followed (after whitespace) by "}" or
    "]" removed, outside JSON strings only."""
    out = []
    in_string = escaped = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
            out.append(ch)
        elif ch == ",":
            j = i + 1
            while j < len(text) and text[j] in " \t\r\n":
                j += 1
            if j < len(text) and text[j] in "}]":
                i += 1
                continue
            out.append(ch)
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _escape_stray_quote(text, pos):
    """The fix for an "Expecting ',' delimiter" at `pos` when a string closed
    early on an unescaped quotation mark: escape the last '"' before `pos`.
    None when the text at `pos` does not look like that case (it starts a
    new key or value -- a missing comma, not a stray quote)."""
    if pos < len(text) and text[pos] == '"' and text[pos - 1] == '"' and text[pos - 2] != "\\":
        # '..."experience.""' -- the quote's own closing mark sits right
        # against the string's real one.
        return text[:pos - 1] + '\\"' + text[pos:]
    if pos >= len(text) or text[pos] in '"{}[],:':
        return None
    q = text.rfind('"', 0, pos)
    if q <= 0 or text[q - 1] == "\\":
        return None
    # Only whitespace or quote-adjacent punctuation between the stray quote
    # and the error position -- otherwise this is not a string that closed
    # early.
    if text[q + 1:pos].strip(" \t.,;:!?'") != "":
        return None
    return text[:q] + '\\"' + text[q + 1:]


def _escape_quote_before_comma(text, pos):
    """The fix for "Expecting property name enclosed in double quotes" at
    `pos` when a quoted phrase inside a string ended in '",' -- the parser
    closed the string there, took the comma as a separator, and then found
    prose (`... costs", which ...`). Escape that quotation mark. None when
    `pos` is not prose right after such a '",'."""
    if pos >= len(text) or text[pos] in '"{}[],:':
        return None
    j = pos - 1
    while j >= 0 and text[j] in " \t\r\n":
        j -= 1
    if j < 1 or text[j] != "," or text[j - 1] != '"' or text[j - 2] == "\\":
        return None
    return text[:j - 1] + '\\"' + text[j:]


def extract_json_tolerant(text):
    """(value, fixes): extract_json's result, or -- when that fails -- the
    result after the lossless fixes above, with `fixes` naming each one
    applied ("trailing comma", "escaped quote"). Raises the ORIGINAL
    json.JSONDecodeError when the text still does not parse."""
    try:
        return extract_json(text), []
    except json.JSONDecodeError as original:
        first_error = original
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = _FENCE_RE.sub("", stripped).strip()
    fixes = []
    candidate = _strip_trailing_commas(stripped)
    if candidate != stripped:
        fixes.append("trailing comma")
    for _ in range(_MAX_TOLERANT_FIXES):
        try:
            return extract_json(candidate), fixes
        except json.JSONDecodeError as e:
            if e.msg.startswith("Expecting ',' delimiter"):
                fixed = _escape_stray_quote(candidate, e.pos)
            elif e.msg.startswith("Expecting property name enclosed in double quotes"):
                fixed = _escape_quote_before_comma(candidate, e.pos)
            else:
                raise first_error
            if fixed is None:
                raise first_error
            candidate = fixed
            fixes.append("escaped quote")
    raise first_error
