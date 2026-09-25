"""Winner-skeleton library for one-shot listicle (and related) generation.

Skeletons live in cartridges/listicle/skeletons/ -- tenant-neutral component
maps. Headline swipe templates live in .../skeletons/headlines/. Peak fill
hints sit on each file under peak_saunas. Selection is by explicit id
(--skeleton / --headline) or a light tag match against ad_brief.angle / hook.
"""
from __future__ import annotations

import json
import re

from .config import REPO_ROOT

SKELETONS_DIR = REPO_ROOT / "cartridges" / "listicle" / "skeletons"
INDEX_PATH = SKELETONS_DIR / "index.json"
HEADLINES_DIR = SKELETONS_DIR / "headlines"
HEADLINES_INDEX_PATH = HEADLINES_DIR / "index.json"
DEFAULT_SKELETON_ID = "classic-n-reasons"
DEFAULT_HEADLINE_ID = "everyones-switching"


def _index():
    return json.loads(INDEX_PATH.read_text())


def _headlines_index():
    return json.loads(HEADLINES_INDEX_PATH.read_text())


def list_skeleton_ids():
    return [row["id"] for row in _index()["skeletons"]]


def list_headline_ids():
    return [row["id"] for row in _headlines_index()["headlines"]]


def load_skeleton(skeleton_id):
    """Return one skeleton dict by id, or raise KeyError."""
    for row in _index()["skeletons"]:
        if row["id"] == skeleton_id:
            path = SKELETONS_DIR / row["file"]
            data = json.loads(path.read_text())
            if data.get("id") != skeleton_id:
                raise ValueError(f"{path.name} id {data.get('id')!r} != index id {skeleton_id!r}")
            return data
    raise KeyError(f"unknown skeleton id: {skeleton_id!r}; known: {list_skeleton_ids()}")


def load_headline(headline_id):
    for row in _headlines_index()["headlines"]:
        if row["id"] == headline_id:
            path = HEADLINES_DIR / row["file"]
            data = json.loads(path.read_text())
            if data.get("id") != headline_id:
                raise ValueError(f"{path.name} id {data.get('id')!r} != index id {headline_id!r}")
            return data
    raise KeyError(f"unknown headline id: {headline_id!r}; known: {list_headline_ids()}")


def load_all_skeletons():
    return [load_skeleton(sid) for sid in list_skeleton_ids()]


def load_all_headlines():
    return [load_headline(hid) for hid in list_headline_ids()]


def skeleton_for_writer(skeleton):
    """Compact payload for the writer user message: enough to fill slots,
    without multi-kilobyte commentary. Keeps peak_saunas when present so a
    Peak run can use reason_fills / image_role_map."""
    keys = (
        "id", "name", "family", "target_cartridge", "tags", "density",
        "components", "slots", "cta", "voice", "compliance", "peak_saunas",
        "writer_brief",
    )
    return {k: skeleton[k] for k in keys if k in skeleton}


def headline_for_writer(headline):
    keys = (
        "id", "n_suggested", "n_harness", "template", "formula", "slots",
        "pairs_with_skeletons", "compliance", "note", "peak_saunas",
    )
    return {k: headline[k] for k in keys if k in headline and headline[k] is not None}


def _haystack(ad_brief):
    parts = [
        ad_brief.get("angle") or "",
        ad_brief.get("hook") or "",
        ad_brief.get("promise") or "",
        ad_brief.get("tone") or "",
        " ".join(ad_brief.get("features_shown") or []),
        " ".join(ad_brief.get("objections_raised") or []),
    ]
    return " ".join(parts).lower()


def select_skeleton(ad_brief, skeleton_id=None, *, for_cartridge=None):
    """Pick a skeleton. Explicit id wins. Else score tags against the ad.
    If for_cartridge is set, only skeletons whose target_cartridge matches
    are eligible (so product-page runs don't get a listicle skeleton)."""
    if skeleton_id:
        sk = load_skeleton(skeleton_id)
        if for_cartridge and sk.get("target_cartridge") not in (None, for_cartridge):
            raise ValueError(
                f"skeleton {skeleton_id!r} targets {sk.get('target_cartridge')!r}, "
                f"not {for_cartridge!r}"
            )
        return sk

    text = _haystack(ad_brief or {})
    best, best_score = None, -1
    for sk in load_all_skeletons():
        if for_cartridge and sk.get("target_cartridge") not in (None, for_cartridge):
            continue
        tags = [t.lower() for t in sk.get("tags") or []]
        peak = sk.get("peak_saunas") or {}
        angle_fit = [t.lower() for t in peak.get("angle_fit") or []]
        score = 0
        for tag in tags + angle_fit:
            if tag and tag in text:
                score += 2 if tag in angle_fit else 1
        if score > best_score:
            best, best_score = sk, score
    if best is not None and best_score > 0:
        return best
    if for_cartridge == "product-page":
        return load_skeleton("simplified-pdp")
    return load_skeleton(DEFAULT_SKELETON_ID)


def select_headline(ad_brief, headline_id=None, *, skeleton_id=None):
    """Pick a headline swipe template. Explicit id wins. Else prefer templates
    that pair with the chosen page skeleton and match ad keywords."""
    if headline_id:
        return load_headline(headline_id)

    text = _haystack(ad_brief or {})
    sk_id = skeleton_id
    if sk_id is None and ad_brief is not None:
        sk_id = select_skeleton(ad_brief).get("id")
    best, best_score = None, -1
    for hl in load_all_headlines():
        # Skip authority template unless the ad explicitly names an authority —
        # Peak has no default doctor endorsement.
        if hl["id"] == "authority-loves" and "doctor" not in text and "authority" not in text:
            continue
        score = 0
        if sk_id and sk_id in (hl.get("pairs_with_skeletons") or []):
            score += 3
        for slot in hl.get("slots") or []:
            token = slot.lower().split("/")[0].strip()
            if token and token in text:
                score += 1
        nudges = {
            "switch": ["everyones-switching", "avatar-started-switching", "social-proof-switched"],
            "mistake": ["most-dont-work", "still-problem-after-trying"],
            "myth": ["concerning-in-common", "most-dont-work"],
            "hidden": ["still-problem-after-trying", "concerning-in-common"],
            "parent": ["swore-they-couldnt", "every-avatar-needs", "game-changer-for-avatar"],
            "apartment": ["only-built-for-niche", "must-have-for-problem"],
            "viral": ["going-viral"],
        }
        for needle, ids in nudges.items():
            if needle in text and hl["id"] in ids:
                score += 2
        if score > best_score:
            best, best_score = hl, score
    if best is not None and best_score > 0:
        return best
    if sk_id:
        for hl in load_all_headlines():
            if sk_id in (hl.get("pairs_with_skeletons") or []) and hl["id"] != "authority-loves":
                return hl
    return load_headline(DEFAULT_HEADLINE_ID)


_WORD_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def validate_skeleton_shape(skeleton):
    """Lightweight required-field check. Returns problem strings; empty = ok."""
    problems = []
    for key in ("id", "version", "name", "family", "sources", "tags", "density",
                "components", "writer_brief", "target_cartridge"):
        if key not in skeleton:
            problems.append(f"missing {key}")
    sid = skeleton.get("id")
    if sid and not _WORD_RE.match(sid):
        problems.append(f"id not kebab-case: {sid!r}")
    density = skeleton.get("density") or {}
    if "approx_body_words" not in density or "item_count" not in density:
        problems.append("density needs approx_body_words and item_count")
    if not skeleton.get("components"):
        problems.append("components empty")
    return problems


def validate_headline_shape(headline):
    problems = []
    for key in ("id", "version", "template", "formula", "slots", "n_suggested", "n_harness"):
        if key not in headline:
            problems.append(f"missing {key}")
    hid = headline.get("id")
    if hid and not _WORD_RE.match(hid):
        problems.append(f"id not kebab-case: {hid!r}")
    tmpl = headline.get("template") or ""
    if "[" not in tmpl or "]" not in tmpl:
        problems.append("template should keep [PLACEHOLDER] slots")
    return problems
