"""Cycle 76: TypeSafe's Jev model, the judge between listicle drafts.

harness/drafts.py writes N listicle drafts per run; every gate runs on each;
this module scores the drafts that passed every gate, and the draft with the
higher composite ships. Jev is an advisor, never a gate: no key, an HTTP
error, a timeout or an answer we cannot read raises JevUnavailable, and the
caller ships the first passing draft. A Jev problem never fails or STOPs a
run.

API (docs.typesafe.ai, checked 2026-10-05): POST API_URL with
`Authorization: Bearer <key>` and a JSON body {"model", "state",
"questions"}; each question is {"type": "score", "instructions",
"criteria": [lowest, ..., highest]}. The answer per question carries
"score" (the probability-weighted level index, 0 .. levels-1) and
"confidence"; the body carries "usage" {input_tokens, output_tokens}. 429 and
529 are retried with backoff.

The key is TYPESAFE_API_KEY in the environment (Tenant.load_env reads the
tenant .env). It is never logged, printed or written to state.json.

Tenant settings (tenant.yaml `jev:`): `enabled` (default false: one draft,
exactly the pre-cycle-76 run), `drafts` (default 1, clamped to 1..MAX_DRAFTS;
the environment variable HARNESS_JEV_DRAFTS overrides it, e.g. `=1` for one
run with a single draft), `timeout_s` (one Jev call, default 60) and
`min_margin` (default 0.02: how much a later draft must beat the first
passing draft by to ship instead of it) and `second_draft_below` (default
unset: every draft is written; a number: in real time a later draft is
written only when the drafts so far fail or score below it --
harness/drafts.py).

The rubric is data: cartridges/listicle/jev-rubric.yaml, or the tenant's own
tenants/<tenant>/jev-rubric.yaml when present.
"""
import http.client
import json
import os
import statistics
import time
import urllib.error
import urllib.request

import yaml

from .config import REPO_ROOT

API_URL = "https://api.typesafe.ai/v1/systemone"
KEY_ENV = "TYPESAFE_API_KEY"
DRAFTS_ENV = "HARNESS_JEV_DRAFTS"
RUBRIC_PATH = REPO_ROOT / "cartridges" / "listicle" / "jev-rubric.yaml"
DEFAULT_TIMEOUT_S = 60
# A later draft must beat the first passing draft by this much (harness/drafts.py).
DEFAULT_MIN_MARGIN = 0.02
MAX_DRAFTS = 3
RETRY_STATUSES = (429, 529)
RETRY_DELAYS_S = (1, 2, 4)


class JevUnavailable(Exception):
    """Jev cannot score this run: no key, HTTP error, timeout, bad answer."""


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------

def settings(tenant):
    cfg = (tenant.get("jev") if tenant is not None else None) or {}
    enabled = bool(cfg.get("enabled", False))
    drafts = os.environ.get(DRAFTS_ENV) or cfg.get("drafts", 1)
    try:
        drafts = int(drafts)
    except (TypeError, ValueError):
        drafts = 1
    drafts = max(1, min(drafts, MAX_DRAFTS)) if enabled else 1
    try:
        timeout_s = float(cfg.get("timeout_s", DEFAULT_TIMEOUT_S))
    except (TypeError, ValueError):
        timeout_s = DEFAULT_TIMEOUT_S
    try:
        min_margin = max(0.0, float(cfg.get("min_margin", DEFAULT_MIN_MARGIN)))
    except (TypeError, ValueError):
        min_margin = DEFAULT_MIN_MARGIN
    second = cfg.get("second_draft_below")
    try:
        second = None if second is None else float(second)
    except (TypeError, ValueError):
        second = None
    return {"enabled": enabled, "drafts": drafts, "timeout_s": timeout_s, "min_margin": min_margin,
            "second_draft_below": second}


def drafts_for_run(tenant, selected):
    """How many listicle drafts this run writes: the tenant's jev.drafts when
    the listicle cartridge is selected, else 1."""
    return settings(tenant)["drafts"] if "listicle" in (selected or []) else 1


def api_key():
    return (os.environ.get(KEY_ENV) or "").strip()


# ---------------------------------------------------------------------------
# rubric
# ---------------------------------------------------------------------------

def rubric_path(tenant=None):
    root = getattr(tenant, "root", None)
    if root is not None and (root / "jev-rubric.yaml").exists():
        return root / "jev-rubric.yaml"
    return RUBRIC_PATH


def load_rubric(tenant=None):
    """{"model", "composite": [ids], "questions": {id: {instructions,
    criteria}}}. Raises JevUnavailable when the file is malformed -- a bad
    rubric costs the run its judge, never the run."""
    path = rubric_path(tenant)
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as e:
        raise JevUnavailable(f"rubric {path.name} unreadable: {e}") from e
    questions = (data or {}).get("questions")
    if not isinstance(questions, dict) or not questions:
        raise JevUnavailable(f"rubric {path.name} has no questions")
    for qid, q in questions.items():
        criteria = (q or {}).get("criteria")
        if not isinstance((q or {}).get("instructions"), str) or not isinstance(criteria, list) \
                or not 2 <= len(criteria) <= 10:
            raise JevUnavailable(f"rubric {path.name}: question {qid!r} needs instructions and 2-10 criteria")
    composite = list(data.get("composite") or [])
    if not composite or any(c not in questions for c in composite):
        raise JevUnavailable(f"rubric {path.name}: composite must name questions it defines")
    return {"model": data.get("model") or "jev-latest", "composite": composite, "questions": questions}


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------

def _text(node):
    if isinstance(node, dict):
        node = node.get("text")
    return node.strip() if isinstance(node, str) else ""


def page_text(page):
    """The listicle page.json as the reader meets it, in reading order:
    headline, dek, each item's heading / body / proof, who it is for and not
    for, the FAQ, the closing block, the CTA. No ids, urls, claim ids, asset
    ids or layout keys. (The renderer-owned trust line, pull quote and model
    picker are not in page.json, so they are not in the text.)"""
    page = page or {}
    lines = [_text(page.get("headline")), _text(page.get("dek"))]
    for i, item in enumerate(page.get("reasons") or [], 1):
        lines.append(f"{item.get('number') or i}. {_text(item.get('heading'))}")
        lines += [_text(item.get("text")), _text(item.get("proof"))]
    fit = page.get("audience_fit") or {}
    for key, label in (("for_you", "Who this is for:"), ("not_for_you", "Who this is not for:")):
        entries = [_text(x) for x in fit.get(key) or []]
        if any(entries):
            lines.append(label)
            lines += [f"- {x}" for x in entries if x]
    questions = (page.get("faq") or {}).get("questions") or []
    if questions:
        lines.append("FAQ")
        for q in questions:
            lines += [f"Q: {_text(q.get('question'))}", f"A: {_text(q.get('answer'))}"]
    closing = page.get("closing") or {}
    lines.append(_text(closing.get("headline")))
    lines += [f"- {_text(x)}" for x in closing.get("recap") or [] if _text(x)]
    lines += [_text(closing.get("warranty_line")), _text(closing.get("financing_line"))]
    if _text(page.get("cta_text")):
        lines.append(f"[{_text(page.get('cta_text'))}]")
    return "\n".join(line for line in lines if line)


def ad_state(ad_brief):
    ad_brief = ad_brief or {}
    return {k: str(ad_brief.get(k) or "") for k in ("hook", "promise", "angle")}


def request_body(page, ad_brief, rubric):
    return {
        "model": rubric["model"],
        "state": {"page": page_text(page), "ad": ad_state(ad_brief)},
        "questions": {
            qid: {"type": "score", "instructions": q["instructions"], "criteria": list(q["criteria"])}
            for qid, q in rubric["questions"].items()
        },
    }


# ---------------------------------------------------------------------------
# the call
# ---------------------------------------------------------------------------

def _http_post(body, key, timeout_s):
    """(status, response text). Network failures raise JevUnavailable."""
    req = urllib.request.Request(
        API_URL, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as e:
        raise JevUnavailable(f"network: {type(e).__name__}: {getattr(e, 'reason', e)}") from e


def _post_with_retry(body, key, timeout_s, *, post, sleep, deadline):
    for attempt in range(len(RETRY_DELAYS_S) + 1):
        status, text = post(body, key, timeout_s)
        if status in RETRY_STATUSES and attempt < len(RETRY_DELAYS_S):
            delay = RETRY_DELAYS_S[attempt]
            if deadline is not None and time.monotonic() + delay + timeout_s > deadline:
                raise JevUnavailable(f"HTTP {status}; no time left to retry")
            sleep(delay)
            continue
        if status != 200:
            raise JevUnavailable(f"HTTP {status}: {(text or '')[:120]}")
        try:
            return json.loads(text)
        except ValueError as e:
            raise JevUnavailable(f"response is not JSON: {e}") from e
    raise JevUnavailable("retries exhausted")  # unreachable: the last attempt returns or raises


def score_page(page, ad_brief, rubric, *, key, timeout_s=DEFAULT_TIMEOUT_S, post=None, sleep=time.sleep,
               deadline=None):
    """One Jev call for one draft. Returns {"scores": {qid: 0-1}, "raw":
    {qid: level index}, "confidence": {qid: 0-1}, "composite": 0-1,
    "overall": 0-1 or None, "usage": {input_tokens, output_tokens},
    "model"}. Raises JevUnavailable on any failure."""
    if not key:
        raise JevUnavailable(f"no {KEY_ENV} in the environment")
    body = request_body(page, ad_brief, rubric)
    data = _post_with_retry(body, key, timeout_s, post=post or _http_post, sleep=sleep, deadline=deadline)
    answers = (data or {}).get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict):
        raise JevUnavailable("response has no answers")
    scores, raw, confidence = {}, {}, {}
    for qid, q in rubric["questions"].items():
        answer = answers.get(qid)
        try:
            value = float(answer["score"])
        except (TypeError, KeyError, ValueError):
            if qid in rubric["composite"]:
                raise JevUnavailable(f"no score for {qid!r}") from None
            continue
        top = len(q["criteria"]) - 1
        raw[qid] = round(value, 3)
        scores[qid] = round(min(max(value / top, 0.0), 1.0), 4)
        if isinstance(answer.get("confidence"), (int, float)):
            confidence[qid] = round(float(answer["confidence"]), 3)
    usage = data.get("usage") or {}
    return {
        "scores": scores,
        "raw": raw,
        "confidence": confidence,
        "composite": round(statistics.mean(scores[q] for q in rubric["composite"]), 4),
        "overall": scores.get("overall"),
        "usage": {"input_tokens": int(usage.get("input_tokens") or 0),
                  "output_tokens": int(usage.get("output_tokens") or 0)},
        "model": data.get("model") or rubric["model"],
    }
