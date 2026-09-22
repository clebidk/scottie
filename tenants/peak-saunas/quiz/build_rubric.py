"""Build tenants/peak-saunas/quiz/rubric.yaml from verified claims (cycle 57).

    ~/advertorial/.venv/bin/python tenants/peak-saunas/quiz/build_rubric.py

Reads claims/verified.json and claims/products.json and writes rubric.yaml
next to this file. Every score comes from a verified claim, and the comment
above each option names the claim ids it was derived from. Active models
only; a model with `active: false` never gets a score.

The weights are a first cut. Caleb tunes them by hand in rubric.yaml; re-run
this script only to rebuild from the claims (it overwrites hand edits).

What is NOT scored, and why:
- App control. No verified claim names app or Wi-Fi control for any model
  (the storefront handles say "smart-wifi-app-control", but a handle is not
  a claim). The "priority" question uses verified attributes instead.
- Indoor placement is not claimed for every indoor model (only two carry an
  "indoor" claim). A model is scored as indoor when it has no verified
  "Placement: Outdoor" claim; the comment names the indoor claim where one
  exists.
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLAIMS = HERE.parent / "claims"
OUT = HERE / "rubric.yaml"

# Placement must outweigh every other question together, so the answer
# "inside" never returns an outdoor model and "outside" never returns an
# indoor one. The sum of every other question's top score is 3+3+3+3+2 = 14.
PLACEMENT_WEIGHT = 15
MATCH = 3        # the option describes this model exactly
NEAR = 1         # the model still fits, one step away
PRIORITY = 2     # priority question, top match
PRIORITY_NEAR = 1

_DOLLAR_RE = re.compile(r"\$\s?([\d,]+)")
_WIDTH_RE = re.compile(r'(\d+(?:\.\d+)?)\s*(?:"|in)\s*(?:W\b|wide)')
_DEPTH_RE = re.compile(r'(\d+(?:\.\d+)?)\s*(?:"|in)\s*(?:D\b|deep)')
_CAPACITY_RE = re.compile(r"(\d+)-Person")
_PANELS_RE = re.compile(r"(\d+)\s+RLT panel")
_TEMP_RE = re.compile(r"(\d+)\s*°F")


def _slug(name):
    return name.lower().replace(" ", "-")


def _first(by_id, *ids):
    for cid in ids:
        if cid in by_id:
            return cid, by_id[cid]["text"]
    return None, None


def load_models():
    products = json.loads((CLAIMS / "products.json").read_text())["products"]
    verified = json.loads((CLAIMS / "verified.json").read_text())
    by_id = {c["id"]: c for c in verified}
    models = []
    for p in products.values():
        if not p.get("active", True):
            continue
        s = _slug(p["name"])
        m = {"slug": s, "name": p["name"]}

        cid, text = _first(by_id, f"spec-{s}-capacity")
        m["capacity"] = int(_CAPACITY_RE.search(text).group(1))
        m["capacity_claim"] = cid

        cid, text = _first(by_id, f"spec-{s}-placement")
        m["outdoor"] = bool(text and "outdoor" in text.lower())
        m["placement_claim"] = cid
        indoor_cid, indoor_text = _first(by_id, f"gbrain-{s}-persons")
        m["indoor_claim"] = indoor_cid if indoor_text and "indoor" in indoor_text.lower() else None

        cid, text = _first(by_id, f"spec-{s}-dimensions", f"gbrain-{s}-dimensions")
        width = float(_WIDTH_RE.search(text).group(1))
        depth = float(_DEPTH_RE.search(text).group(1))
        m["footprint"] = max(width, depth)
        m["dimensions_claim"] = cid

        cid, text = _first(by_id, f"spec-{s}-electrical", f"gbrain-{s}-power", f"spec-{s}-electrical-requirement")
        compact = text.replace(" ", "")
        if "240V" in compact:
            m["power"] = "240v"
        elif "120V/20A" in compact:
            m["power"] = "20a"
        elif "120V/15A" in compact or "standard outlet" in text.lower():
            m["power"] = "standard"
        else:
            raise SystemExit(f"{s}: cannot classify electrical claim {cid!r}: {text!r}")
        m["power_claim"] = cid

        cid, text = _first(by_id, f"price-{s}")
        m["price"] = int(_DOLLAR_RE.search(text).group(1).replace(",", ""))
        m["price_claim"] = cid

        cid, text = _first(by_id, f"spec-{s}-red-light", f"gbrain-{s}-rlt")
        m["rlt_panels"] = int(_PANELS_RE.search(text).group(1))
        m["rlt_claim"] = cid

        cid, text = _first(by_id, f"spec-{s}-cabin-material")
        m["cedar"] = bool(text and "red cedar" in text.lower())
        m["wood_claim"] = cid

        cid, text = _first(by_id, f"spec-{s}-max-temperature")
        m["max_temp"] = int(_TEMP_RE.search(text).group(1))
        m["temp_claim"] = cid
        models.append(m)
    models.sort(key=lambda m: (m["price"], m["slug"]))
    return models


def _option(label, scored, why):
    """scored: [(model, points, claim_id), ...]; points of 0 are dropped."""
    scores = {m["slug"]: pts for m, pts, _ in scored if pts}
    claims = sorted({cid for m, pts, cid in scored if pts and cid})
    return {"label": label, "scores": scores, "claims": claims, "why": why}


def build_questions(models):
    q = []

    # 1. Who uses it, and how many at once (people count + solo/couple/family).
    def cap(fn):
        return [(m, fn(m["capacity"]), m["capacity_claim"]) for m in models]
    q.append({
        "id": "household",
        "prompt": "Who will use your sauna at the same time?",
        "options": [
            _option("Just me", cap(lambda c: MATCH if c == 1 else NEAR if c == 2 else 0),
                    "capacity 1-Person scores 3, 2-Person scores 1"),
            _option("Two of us", cap(lambda c: MATCH if c == 2 else NEAR if c == 3 else 0),
                    "capacity 2-Person scores 3, 3-Person scores 1"),
            _option("Three or four of us", cap(lambda c: MATCH if c in (3, 4) else NEAR if c == 5 else 0),
                    "capacity 3- or 4-Person scores 3, 5-Person scores 1"),
            _option("Five of us", cap(lambda c: MATCH if c >= 5 else NEAR if c == 4 else 0),
                    "capacity 5-Person scores 3, 4-Person scores 1"),
        ],
    })

    # 2. Placement: indoor or outdoor.
    inside = [(m, 0 if m["outdoor"] else PLACEMENT_WEIGHT, m["indoor_claim"]) for m in models]
    outside = [(m, PLACEMENT_WEIGHT if m["outdoor"] else 0, m["placement_claim"]) for m in models]
    q.append({
        "id": "placement",
        "prompt": "Where will it go?",
        "options": [
            _option("Inside the house", inside,
                    f"every model with no verified 'Placement: Outdoor' claim scores {PLACEMENT_WEIGHT}"),
            _option("Outside, on a patio, deck, or yard", outside,
                    f"every model with a verified 'Placement: Outdoor' claim scores {PLACEMENT_WEIGHT}"),
        ],
    })

    # 3. Floor space: bands on the larger side of the verified exterior footprint.
    bands = [(0, 36), (36, 48), (48, 60), (60, 10_000)]

    def band_of(m):
        return next(i for i, (lo, hi) in enumerate(bands) if lo < m["footprint"] <= hi)

    def space(i):
        return [(m, MATCH if band_of(m) == i else NEAR if band_of(m) == i - 1 else 0, m["dimensions_claim"])
                for m in models]
    q.append({
        "id": "space",
        "prompt": "How much floor space can you give it?",
        "options": [
            _option("A corner under 3 feet wide", space(0),
                    "larger exterior side up to 36 in scores 3"),
            _option("3 to 4 feet wide", space(1),
                    "larger exterior side 36-48 in scores 3, the band below scores 1"),
            _option("4 to 5 feet wide", space(2),
                    "larger exterior side 48-60 in scores 3, the band below scores 1"),
            _option("More than 5 feet wide", space(3),
                    "larger exterior side over 60 in scores 3, the band below scores 1"),
        ],
    })

    # 4. Power: the outlet the buyer can offer.
    def power(kind):
        return [(m, MATCH if m["power"] == kind else 0, m["power_claim"]) for m in models]
    q.append({
        "id": "power",
        "prompt": "Which outlet can it plug into?",
        "options": [
            _option("A standard household outlet", power("standard"),
                    "a verified 120V/15A standard-outlet claim scores 3"),
            _option("A dedicated 20-amp outlet", power("20a"),
                    "a verified 120V/20A dedicated-outlet claim scores 3"),
            _option("A 240-volt circuit I can have an electrician add", power("240v"),
                    "a verified 240V claim scores 3"),
        ],
    })

    # 5. Budget: bands on the verified price claims.
    price_bands = [(0, 6000), (6000, 8000), (8000, 11000), (11000, 10**9)]

    def pband(m):
        return next(i for i, (lo, hi) in enumerate(price_bands) if lo <= m["price"] < hi)

    def budget(i):
        return [(m, MATCH if pband(m) == i else NEAR if pband(m) == i - 1 else 0, m["price_claim"])
                for m in models]
    q.append({
        "id": "budget",
        "prompt": "What budget are you working with?",
        "options": [
            _option("Under $6,000", budget(0), "verified price under $6,000 scores 3"),
            _option("$6,000 to $8,000", budget(1), "verified price $6,000-$7,999 scores 3, the band below scores 1"),
            _option("$8,000 to $11,000", budget(2), "verified price $8,000-$10,999 scores 3, the band below scores 1"),
            _option("More than $11,000", budget(3), "verified price $11,000 or more scores 3, the band below scores 1"),
        ],
    })

    # 6. Priority: what matters most (verified attributes only).
    top_temp = max(m["max_temp"] for m in models)
    q.append({
        "id": "priority",
        "prompt": "Which matters most to you?",
        "options": [
            _option("The most red light therapy",
                    [(m, PRIORITY if m["rlt_panels"] >= 2 else PRIORITY_NEAR, m["rlt_claim"]) for m in models],
                    "2 verified RLT panels score 2, 1 panel scores 1"),
            _option("Red cedar wood",
                    [(m, PRIORITY if m["cedar"] else 0, m["wood_claim"]) for m in models],
                    "a verified 'Canadian red cedar' cabin-material claim scores 2"),
            _option("The hottest sessions",
                    [(m, PRIORITY if m["max_temp"] == top_temp else 0, m["temp_claim"]) for m in models],
                    f"the highest verified max temperature ({top_temp}°F) scores 2"),
        ],
    })
    return q


INTERSTITIALS = [
    ("placement", "What full spectrum infrared means, and that every model is full spectrum"),
    ("power", "Why the outlet decides the model: some plug into a standard outlet, some need an electrician"),
    ("budget", "What every model includes, whatever the price: free shipping and red light therapy standard"),
]


def _yaml_str(text):
    return json.dumps(text, ensure_ascii=False)


def render_yaml(models, questions):
    lines = [
        "# tenants/peak-saunas/quiz/rubric.yaml -- the quiz cartridge's scoring rubric.",
        "# GENERATED by build_rubric.py from claims/verified.json + claims/products.json",
        "# (cycle 57). Weights are a first cut for Caleb to tune by hand; re-running the",
        "# script overwrites hand edits. Model keys are product-name slugs (claim-id",
        "# namespace), active models only. A comment above each option names the claim",
        "# ids its scores were derived from.",
        "#",
        "# Placement scores " + str(PLACEMENT_WEIGHT) + ": more than every other question's top score",
        "# together (3+3+3+3+2 = 14), so placement works as a hard filter.",
        "# Not scored: app control (no verified claim names it for any model).",
        "version: 1",
        "questions:",
    ]
    for q in questions:
        lines.append(f"  - id: {q['id']}")
        lines.append(f"    prompt: {_yaml_str(q['prompt'])}")
        lines.append("    options:")
        for o in q["options"]:
            lines.append(f"      # {o['why']}")
            lines.append(f"      # claims: {', '.join(o['claims']) or '(none: absence of an outdoor claim)'}")
            lines.append(f"      - label: {_yaml_str(o['label'])}")
            pairs = ", ".join(f"{k}: {v}" for k, v in sorted(o["scores"].items()))
            lines.append(f"        scores: {{{pairs}}}")
    lines.append("# Interstitials: one short educational line after each named question, written")
    lines.append("# by the writer from verified claims only. `topic` is guidance, not copy.")
    lines.append("interstitials:")
    for after, topic in INTERSTITIALS:
        lines.append(f"  - after: {after}")
        lines.append(f"    topic: {_yaml_str(topic)}")
    lines.append("# Ties go to the first model in this list: verified price, lowest first.")
    lines.append("tiebreak: [" + ", ".join(m["slug"] for m in models) + "]")
    return "\n".join(lines) + "\n"


def main():
    models = load_models()
    questions = build_questions(models)
    OUT.write_text(render_yaml(models, questions))
    print(f"wrote {OUT} ({len(questions)} questions, {len(models)} active models)")


if __name__ == "__main__":
    main()
