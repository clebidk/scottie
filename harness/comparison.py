"""Cycle 56: the comparison cartridge (v1.0.0) -- the model table, the
alternatives allowlist, and the page's structural gates.

A comparison page compares on two axes, both from verified facts only:

  A. model vs model -- the run's own product (the featured column) and two
     more of the tenant's own active products, picked by ground.py's
     `_model_options` (closest in price). Every cell of the table is a
     fragment of one verified claim's own text, with that claim's id, or
     "--" when no verified claim states it. The RENDERER builds the table
     from facts_pack.comparison (built by ground.py with build_model_cells
     below); the writer never writes a cell.
  B. the alternatives the ad names -- category-level, never a brand. The
     writer picks 2-3 ids from the cartridge's own allowlist
     (cartridges/comparison/schema.json "alternatives") and describes each
     one in plain words with no numbers at all: no verified claim describes
     an alternative, so a number about one could only be invented. The
     tenant's own side of each difference cites claim ids.

Three callers share this module so the prompt and the gate can never drift
apart, the same split harness/listicle.py uses:

  - harness/ground.py calls build_model_cells to turn verified claims into
    table cells (facts_pack.comparison);
  - harness/write.py appends writer_lines(facts_pack) to the writer's hard
    constraints;
  - harness/repair.py's check_page_gates runs find_comparison_violations
    (writer-owned, writer-fixable; every problem carries a stable
    "comparison:*" key), and harness/render.py builds render_context and
    runs find_table_violations as a post-render backstop over what the
    renderer itself drew.

Everything here is tenant-neutral: claim ids are matched by the namespaces
ground.py already cites a model's facts under ("spec-<model>-",
"gbrain-<model>-", "pdp-<model>-") and a generic suffix, and every value is
cut from the claim's own sentence by a plain-English pattern.
"""
import json
import re

from . import config
from . import vocab
from .claims import _trigger_reason, warranty_claim_id
from .listicle import GENERIC_AUDIENCE_WORDS, hsa_claim, rating_line, trust_line_items
from .textutil import walk_page

AXES = ("models", "alternatives")

# The cell a model gets when no verified claim states the value.
MISSING = "—"

# Namespaces a model's own facts are cited under, in lookup order -- the
# same prefixes harness/ground.py's facts_for already treats as the model's
# citable spec claims.
_MODEL_NAMESPACES = ("spec", "gbrain", "pdp")

# Longest cell the renderer will draw. A pattern that would cut more than
# this from a claim's sentence is treated as "no value" rather than shipping
# a paragraph inside a table cell.
MAX_CELL_CHARS = 72


def _label_value(label):
    """"<Model> -- <Label>: <value>." -> <value> (the shape ground.py's spec
    claims are written in), up to the end of the claim's first sentence."""
    return re.compile(r"\b" + label + r":\s*([^.]+?)\s*(?:\.\s|\.?\s*$)", re.IGNORECASE)


# One table row. `suffixes` are claim id suffixes tried, in order, under
# each of _MODEL_NAMESPACES; `patterns` cut the value out of that claim's
# own text (group 1). The first claim whose text a pattern matches wins.
ROWS = {
    "capacity": {
        "label": "Capacity",
        "suffixes": ("capacity",),
        "patterns": (_label_value("Capacity"),),
    },
    "dimensions": {
        "label": "Footprint (W × D × H)",
        "suffixes": ("dimensions",),
        "patterns": (re.compile(
            r"(\d[\d.]*\s*(?:\"\s*W|in\.?\s+wide)\s*[x×]\s*\d[\d.]*\s*(?:\"\s*D|in\.?\s+deep)"
            r"\s*[x×]\s*\d[\d.]*\s*(?:\"\s*H|in\.?\s+tall))",
            re.IGNORECASE,
        ),),
    },
    "placement": {
        "label": "Indoor / outdoor",
        "suffixes": ("placement", "persons"),
        "patterns": (_label_value("Placement"), re.compile(r"\b(indoor|outdoor)\b", re.IGNORECASE)),
    },
    "infrared": {
        "label": "Infrared wavelengths",
        "suffixes": ("infrared-wavelength-range",),
        "patterns": (_label_value("wavelength range"),),
    },
    "red_light": {
        "label": "Red light therapy",
        # (cycle 60: the table's heat/light row -- every model states it;
        # "heaters" below is missing for some models, so it stays a cell the
        # writer can cite, not a row)
        "suffixes": ("red-light", "rlt"),
        "patterns": (re.compile(r"therapy:\s*([^,.]+,\s*\d+\s+\w+\s+panels?)", re.IGNORECASE),),
    },
    "controls": {
        "label": "Controls / app",
        "suffixes": ("app-control",),
        "patterns": (re.compile(r"\b(app\s*\([^)]*\))", re.IGNORECASE),),
    },
    "power": {
        "label": "Power",
        "suffixes": ("electrical", "power", "electrical-requirement"),
        "patterns": (
            re.compile(r"electrical:\s*(.+?)(?:\s+(?:--|—)\s+|\.\s|\.?\s*$)", re.IGNORECASE),
            _label_value("requirement"),
        ),
    },
    "max_temperature": {
        "label": "Max temperature",
        "suffixes": ("max-temperature",),
        "patterns": (_label_value("temperature"),),
    },
    "cabin_material": {
        "label": "Wood",
        "suffixes": ("cabin-material", "wood"),
        "patterns": (
            _label_value("material"),
            re.compile(r"(?:crafted|built|made)(?:\s+entirely)?\s+from\s+([^—;.]+?)\s*(?:—|;|\.|$)",
                       re.IGNORECASE),
        ),
    },
    "heaters": {
        "label": "Heaters",
        "suffixes": ("heater", "wavelengths"),
        "patterns": (
            _label_value("heating"),
            re.compile(r"(\d+\s+infrared heater panels)", re.IGNORECASE),
        ),
    },
    "audio": {
        "label": "Audio",
        "suffixes": ("audio",),
        "patterns": (_label_value("Audio"),),
    },
}

# Cycle 60: the table is the 8 rows a buyer decides on, in this order, and
# nothing else (price and warranty close it). v1 drew 9-11 rows -- 7 fixed
# plus 1-2 the writer picked -- which made the table a spec dump. Every key
# in ROWS still becomes a cell in facts_pack.comparison, so the writer can
# cite a model's placement, wavelength range, app or audio claim in prose;
# only these rows are drawn.
FIXED_ROWS = ("capacity", "dimensions", "power", "red_light", "max_temperature", "cabin_material")
PRICE_ROW = "price"
WARRANTY_ROW = "warranty"
ROW_LABELS = {key: spec["label"] for key, spec in ROWS.items()}
ROW_LABELS[PRICE_ROW] = "Price"

_PRICE_VALUE_RE = re.compile(r"\$[\d,]+(?:\.\d{2})?")

ALTERNATIVE_RANGE = (2, 3)
ALTERNATIVE_LIST_RANGE = (2, 3)
FAQ_COUNT_RANGE = (5, 7)
NUMBERS_MEAN_COUNT = 3
RECAP_BULLET_COUNT = 3
# Cycle 60: the header dek and each best-for line are one short line each.
DEK_MAX_WORDS = 22
BEST_FOR_MAX_WORDS = 14

# page.json keys for sections the RENDERER owns. A writer that invents one
# is rejected rather than quietly overriding data that must come from
# facts_pack.
RENDERER_OWNED_KEYS = (
    "comparison_table", "table", "cells", "trust_line", "rating_line", "warranty_line",
    "financing_line", "hsa_line", "model_picker", "models", "sticky_cta", "proof_row",
    "proof_stats",
)

_SCHEMA_PATH = config.REPO_ROOT / "cartridges" / "comparison" / "schema.json"


def allowed_alternatives():
    """The cartridge's own alternatives allowlist (schema.json's top-level
    "alternatives"). The allowlist is cartridge data, not engine code: a
    cartridge for another category names its own alternatives there."""
    try:
        schema = json.loads(_SCHEMA_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return ()
    return tuple(a for a in schema.get("alternatives") or () if isinstance(a, str))


def alternative_label(alternative_id):
    """"far-infrared-only cabin" -> "Far-infrared-only cabin" (a display
    heading in sentence case; the id itself is what the writer and the gate
    agree on)."""
    return alternative_id[:1].upper() + alternative_id[1:]


# Cycle 60: sentence case. Words a sentence-case headline leaves lower case
# even in a title-case writer's hands, so they never count either way.
_MINOR_WORDS = frozenset({
    "a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to", "vs", "with",
})
# A capitalised ordinary word: "Home", "At-Home", "Buyer's". Not an acronym
# ("HSA", "RLT"), not a mixed-case name ("iOS"), not a number token.
_CAPITALISED_WORD_RE = re.compile(r"^[A-Z][a-z'’]+(?:-[A-Za-z][a-z'’]*)*$")


def _headline_words(headline):
    return re.findall(r"[A-Za-z0-9][\w'’-]*", headline or "")


def _proper_words(proper_names):
    words = set()
    for name in proper_names or ():
        words.update(w.lower() for w in _headline_words(str(name)))
    return words


def _capitalised_positions(headline, proper_names):
    """Indexes (into _headline_words) of every capitalised ordinary word
    after the first word -- the words sentence case would write lower
    case. Proper names (model and tenant names) never count."""
    proper = _proper_words(proper_names)
    positions = []
    for i, word in enumerate(_headline_words(headline)):
        if i == 0 or word.lower() in proper or word.lower() in _MINOR_WORDS:
            continue
        if _CAPITALISED_WORD_RE.match(word):
            positions.append(i)
    return positions


def is_title_case(headline, proper_names=()):
    """True when the headline capitalises ordinary words the way a title
    does ("Which Home Infrared Cabin Fits ..."). Two or more capitalised
    ordinary words after the first word is title case; one is allowed, since
    it can be a proper name nothing here knows (a place, a wood)."""
    return len(_capitalised_positions(headline, proper_names)) >= 2


def display_headline(headline, proper_names=()):
    """The headline as the page shows it: a title-case headline (a page
    written before cycle 60) in sentence case, every other headline exactly
    as written. A CASE rule, like brand.headline_case upper: no word is
    added, removed or reordered, and the gate still reads page.json."""
    if not is_title_case(headline, proper_names):
        return headline
    lower = set(_capitalised_positions(headline, proper_names))
    out, index, pos = [], 0, 0
    for m in re.finditer(r"[A-Za-z0-9][\w'’-]*", headline):
        out.append(headline[pos:m.start()])
        word = m.group(0)
        out.append(word.lower() if index in lower else word)
        pos, index = m.end(), index + 1
    out.append(headline[pos:])
    return "".join(out)


def _alternative_re(alternative_id):
    """The allowlisted phrase as a headline may write it: any mix of spaces
    and hyphens between its words, the last word singular or plural."""
    words = [w for w in re.split(r"[\s-]+", alternative_id.strip()) if w]
    if not words:
        return None
    body = r"[\s-]+".join(re.escape(w) for w in words)
    return re.compile(r"\b" + body + r"s?\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Grounding: verified claims -> table cells (called from ground.facts_for)
# ---------------------------------------------------------------------------

def _display(value):
    value = value.strip().rstrip(",;")
    return value[:1].upper() + value[1:] if value else value


def extract_cell(row_key, name_slug, claims_by_id):
    """{"text", "claim_ids"} for one model's row: a fragment cut from the
    first matching verified claim's own sentence, with that claim's id, or
    the MISSING cell when no verified claim states it."""
    spec = ROWS[row_key]
    for suffix in spec["suffixes"]:
        for namespace in _MODEL_NAMESPACES:
            claim = claims_by_id.get(f"{namespace}-{name_slug}-{suffix}")
            if not claim:
                continue
            for pattern in spec["patterns"]:
                m = pattern.search(claim.get("text") or "")
                if not m:
                    continue
                value = _display(m.group(1))
                if value and len(value) <= MAX_CELL_CHARS:
                    return {"text": value, "claim_ids": [claim["id"]]}
    return {"text": MISSING, "claim_ids": []}


def price_cell(price_claim):
    """The first dollar figure in the model's own price claim, or MISSING.
    Only the figure is drawn -- a claim that also states a list/compare-at
    price never shows it in the table."""
    if not price_claim:
        return {"text": MISSING, "claim_ids": []}
    m = _PRICE_VALUE_RE.search(price_claim.get("text") or "")
    if not m:
        return {"text": MISSING, "claim_ids": []}
    return {"text": m.group(0), "claim_ids": [price_claim["id"]]}


def warranty_cell(claims_by_id):
    """Every model shares the tenant's one warranty: the fixed spec-table
    value from vocab.yaml (the only warranty wording the gate allows in a
    table), citing the tenant's warranty claim. MISSING when that claim is
    not verified."""
    wid = warranty_claim_id()
    if wid not in claims_by_id or not vocab.ALLOWED_WARRANTY_SPEC_VALUE:
        return {"text": MISSING, "claim_ids": []}
    return {"text": vocab.ALLOWED_WARRANTY_SPEC_VALUE, "claim_ids": [wid]}


def build_model_cells(name_slug, claims_by_id, price_claim):
    """Every row's cell for one model: {row_key: {"text", "claim_ids"}}."""
    cells = {key: extract_cell(key, name_slug, claims_by_id) for key in ROWS}
    cells[PRICE_ROW] = price_cell(price_claim)
    cells[WARRANTY_ROW] = warranty_cell(claims_by_id)
    return cells


def row_catalog():
    """What facts_pack.comparison tells the writer about the table: its
    rows, in order. The writer picks none of them (cycle 60)."""
    return {
        "fixed_rows": [{"key": k, "label": ROW_LABELS[k]} for k in FIXED_ROWS]
        + [{"key": PRICE_ROW, "label": ROW_LABELS[PRICE_ROW]},
           {"key": WARRANTY_ROW, "label": vocab.ALLOWED_WARRANTY_SPEC_LABEL or "Warranty"}],
    }


# ---------------------------------------------------------------------------
# Renderer-owned sections, built from facts_pack alone
# ---------------------------------------------------------------------------

def _models(facts_pack):
    block = (facts_pack or {}).get("comparison") or {}
    return [m for m in block.get("models") or [] if isinstance(m, dict)]


def table_rows(facts_pack):
    """[{"key", "label", "cells": [cell per model]}] in table order. A row
    no model has a verified value for is dropped rather than drawn as a line
    of dashes -- it tells a reader nothing. Since cycle 60 the writer picks
    no rows; an older page.json's "extra_rows" is ignored."""
    models = _models(facts_pack)
    if not models:
        return []
    keys = list(FIXED_ROWS) + [PRICE_ROW, WARRANTY_ROW]
    rows = []
    for key in keys:
        cells = [(m.get("cells") or {}).get(key) or {"text": MISSING, "claim_ids": []} for m in models]
        if all(not c.get("claim_ids") for c in cells):
            continue
        label = ROW_LABELS.get(key) or (vocab.ALLOWED_WARRANTY_SPEC_LABEL or "Warranty")
        rows.append({"key": key, "label": label, "cells": cells})
    return rows


def column_assets(facts_pack):
    """Each compared model's own column image (its first storefront image),
    as an asset dict render_page can download like any other."""
    assets = []
    for m in _models(facts_pack):
        image = m.get("image")
        if isinstance(image, dict) and image.get("id") and image.get("url"):
            assets.append(dict(image, model_title=m.get("title") or m.get("name")))
    return assets


def render_context(facts_pack, page, financing_lender=None, tenant_name=None):
    """Everything cartridges/comparison/template.html renders that came
    from facts_pack rather than from the writer, plus the headline as the
    page shows it (display_headline). `claim_ids` is every id the renderer
    itself cites, so the Sources list covers the table too."""
    rows = table_rows(facts_pack)
    trust = trust_line_items(facts_pack)
    rating = rating_line(facts_pack)
    hsa = hsa_claim(facts_pack)
    verified = {c["id"] for c in (facts_pack or {}).get("verified_claims", [])}
    wid = warranty_claim_id()
    warranty_line = (
        {"text": vocab.ALLOWED_WARRANTY_SENTENCE, "claim_ids": [wid]}
        if wid in verified and vocab.ALLOWED_WARRANTY_SENTENCE else None
    )
    financing_text = vocab.allowed_financing_sentence(financing_lender)
    models = _models(facts_pack)
    claim_ids = set()
    source_url_by_claim = {}
    for row in rows:
        for model, cell in zip(models, row["cells"], strict=True):
            claim_ids.update(cell.get("claim_ids") or ())
            if row["key"] != WARRANTY_ROW and model.get("url"):
                for cid in cell.get("claim_ids") or ():
                    source_url_by_claim[cid] = model["url"]
    for item in trust + ([rating] if rating else []) + ([warranty_line] if warranty_line else []):
        claim_ids.update(item.get("claim_ids") or ())
    if hsa:
        claim_ids.add(hsa["id"])
    # Each column head shows its model's price: the price row's own cell
    # (already cited above), never a second reading of the claim.
    price_row = next((r for r in rows if r["key"] == PRICE_ROW), None)
    head_models = [
        dict(m, price=cell["text"] if cell.get("claim_ids") else None)
        for m, cell in zip(models, price_row["cells"] if price_row else [{}] * len(models), strict=True)
    ]
    proper_names = [tenant_name] + [m.get("name") for m in models]
    return {
        "headline": display_headline((page or {}).get("headline") or "", proper_names),
        "models": head_models,
        "rows": rows,
        "trust_items": trust,
        "rating_line": rating,
        "hsa_claim": hsa,
        "warranty_line": warranty_line,
        "financing_line": {"text": financing_text} if financing_text else None,
        "alternative_labels": {a: alternative_label(a) for a in allowed_alternatives()},
        "claim_ids": claim_ids,
        # Sources: a model's own facts fall back to that model's own page,
        # labelled with its own name (render.build_sources_list).
        "source_url_by_claim": source_url_by_claim,
        "model_title_by_url": {m["url"]: m.get("title") or m.get("name") for m in models if m.get("url")},
    }


def find_table_violations(context, facts_pack):
    """Post-render backstop over what the renderer itself drew (a failure
    here is a data/renderer bug, never something a writer repair could fix):
    every drawn cell cites a claim id that exists in this run's
    verified_claims, or is the MISSING dash; and no column names a banned
    name (vocab banned_names -- competitors and retired models)."""
    verified = {c["id"] for c in (facts_pack or {}).get("verified_claims", [])}
    banned = [b.lower() for b in vocab.BANNED_NAMES if b]
    problems = []
    for i, row in enumerate(context.get("rows") or []):
        for j, cell in enumerate(row.get("cells") or []):
            ids = cell.get("claim_ids") or []
            if cell.get("text") == MISSING and not ids:
                continue
            if not ids or any(cid not in verified for cid in ids):
                problems.append({
                    "path": f"table.rows[{i}].cells[{j}]", "key": f"comparison:table_row:{row.get('key')}",
                    "issue": f"table cell {cell.get('text')!r} in row {row.get('label')!r} is not backed by "
                             "a verified claim id in this run's facts_pack",
                })
    for j, model in enumerate(context.get("models") or []):
        shown = " ".join(str(model.get(k) or "") for k in ("name", "title")).lower()
        hit = next((b for b in banned if re.search(r"\b" + re.escape(b) + r"\b", shown)), None)
        if hit:
            problems.append({
                "path": f"table.models[{j}]", "key": "comparison:banned_name",
                "issue": f"table column names the banned name {hit!r}; a comparison page never names one",
            })
    return problems


# ---------------------------------------------------------------------------
# Writer guidance (harness/write.py appends these to the hard constraints)
# ---------------------------------------------------------------------------

def headline_formula(axis, models=None):
    names = [m.get("name") for m in (models or [])] or ["<Model A>", "<Model B>", "<Model C>"]
    if axis == "models":
        return f"{' vs '.join(names)}: which <category> fits <audience>"
    return "<category> vs <alternative>: what <audience> should compare"


def writer_lines(facts_pack):
    """The hard-constraint lines write.py adds for a comparison run. Names
    the run's own three models in column order, so the writer is measured
    against exactly the names it was given."""
    models = _models(facts_pack)
    alternatives = allowed_alternatives()
    lines = []
    if models:
        described = ", ".join(
            f'"{m["name"]}" (id "{m["id"]}"{", featured" if m.get("featured") else ""})' for m in models
        )
        lines.append(
            f"This page compares these {len(models)} models, in this column order: {described}. "
            "Write each name exactly as given."
        )
    lines += [
        'Pick "axis" from the ad brief: when the ad names or weighs ANOTHER WAY to get the same '
        "thing -- a studio, a membership, a gym or spa, a different kind of sauna, a blanket -- "
        "the axis is \"alternatives\", even when the ad also quotes a price or names one of the "
        "tenant's models. Only when the ad is purely about choosing between, pricing, or sizing "
        "the tenant's own cabins (no other way named) is the axis \"models\".",
        f'"models" headline formula, exactly: "{headline_formula("models", models)}". '
        f'"alternatives" headline formula, exactly: "{headline_formula("alternatives")}", where '
        "<alternative> is one of the alternatives you chose, in its own words. <category> is the "
        "product category, never the tenant's name or a model name. <audience> names people by "
        "situation or goal, 2-5 words, never a bare \"people\" or \"buyers\".",
        "Headline in sentence case: capitalize the first word and proper names (model names, "
        "the tenant's name) only -- never title case.",
        f'"dek": one plain sentence of at most {DEK_MAX_WORDS} words. Each "best_for" line: at most '
        f"{BEST_FOR_MAX_WORDS} words naming who that model suits.",
        "Voice: calm and specific. Open every section with its point -- no filler intro "
        "(\"When it comes to\", \"Whether you're\", \"Let's take a look\"). No lists of three "
        "adjectives or three parallel phrases in a row (\"X, Y, and Z\") -- name the one or two "
        "that matter. No exclamation marks.",
        "The comparison table (its rows are fixed: capacity, footprint, power, red light, max "
        "temperature, wood, price, warranty), the trust line, the rating line, the warranty and "
        "financing sentences, the HSA/FSA line and the sticky bar are drawn by the renderer from "
        "facts_pack -- never write any of them, and never write a warranty or financing sentence "
        "anywhere. Read each model's cells in facts_pack.comparison.models for the values and "
        "their claim ids.",
        f'"alternatives": {ALTERNATIVE_RANGE[0]}-{ALTERNATIVE_RANGE[1]} entries, each "id" one of '
        f"{list(alternatives)}. An alternative's summary, similarities and the alternative side "
        "of each difference contain NO digits, dollar amounts or percentages at all -- nothing "
        "verified describes an alternative, so describe it in plain words only. The tenant's "
        "side of each difference (\"ours\") cites claim_ids for any number or spec.",
        '"best_for" and "who_for": exactly one entry per model, in column order, each '
        '{"id": "<model id>", "text": "...", "claim_ids": [...]}; any number or spec cites its '
        "claim_ids from that model's own cells.",
        f'"numbers_mean": exactly {NUMBERS_MEAN_COUNT} short paragraphs explaining what the table\'s '
        "rows mean for a buyer, each citing the claim_ids of the cells it reads from.",
        f'FAQ: {FAQ_COUNT_RANGE[0]}-{FAQ_COUNT_RANGE[1]} questions; an answer with a number, price, '
        'spec or trigger word carries claim_ids. Closing "recap": exactly 3 bullets.',
        '"hero": the featured model\'s hero image -- a room or installation photo of that model '
        "when facts_pack.assets has one, else its product shot; \"lifestyle\": one lifestyle "
        "photo -- both asset ids from facts_pack.assets, different from each other and from every "
        "model's column image id in facts_pack.comparison.models[].image.id.",
    ]
    return lines


# ---------------------------------------------------------------------------
# Gate: structural checks over the writer's page.json
# ---------------------------------------------------------------------------

def _problem(path, key, issue, **extra):
    item = {"path": path, "key": key, "issue": issue}
    item.update(extra)
    return item


def _entries(value):
    return value if isinstance(value, list) else []


def _numeric_reason(text):
    """Why `text` states a number, or None: any digit, dollar sign or
    percent sign at all -- stricter than claims._trigger_reason (which lets
    a capacity token like "2-Person" through), because an alternative has no
    verified claim a number could cite."""
    if "$" in text:
        return "a dollar amount"
    if "%" in text:
        return "a percentage"
    if re.search(r"\d", text):
        return "a number"
    return None


def _trigger_word(text):
    m = vocab.TRIGGER_WORD_RE.search(text.lower())
    return m.group(0) if m else None


def find_axis_and_headline_violations(page, models, alternatives_chosen, tenant_name=None, product_names=None):
    problems = []
    axis = page.get("axis")
    if axis not in AXES:
        return [_problem("$.axis", "comparison:axis", f"axis is {axis!r}; it must be one of {list(AXES)}")]
    headline = (page.get("headline") or "").strip()
    names = [m.get("name") or "" for m in models]
    if axis == "models":
        vs = r"\s+vs\.?\s+"
        pattern = re.compile(
            r"^\s*" + vs.join(re.escape(n) for n in names) + r"\s*:\s*Which\s+(.+?)\s+Fits\s+(.+?)\s*[.?]?\s*$",
            re.IGNORECASE,
        )
        m = pattern.match(headline) if names else None
        if not m:
            return [_problem(
                "$.headline", "comparison:headline_formula",
                f'headline {headline!r} does not follow the "models" formula: '
                f'"{headline_formula("models", models)}"',
            )]
        category, audience = m.group(1), m.group(2)
    else:
        m = re.match(r"^\s*(.+?)\s+vs\.?\s+(.+?)\s*:\s*What\s+(.+?)\s+Should\s+Compare\s*[.?]?\s*$",
                     headline, re.IGNORECASE)
        if not m:
            return [_problem(
                "$.headline", "comparison:headline_formula",
                f'headline {headline!r} does not follow the "alternatives" formula: '
                f'"{headline_formula("alternatives")}"',
            )]
        category, alternative, audience = m.group(1), m.group(2), m.group(3)
        patterns = [p for p in (_alternative_re(a) for a in alternatives_chosen) if p]
        if not any(p.search(alternative) for p in patterns):
            problems.append(_problem(
                "$.headline", "comparison:headline_formula",
                f"headline's <alternative> slot {alternative!r} names none of this page's alternatives "
                f"{list(alternatives_chosen)}",
            ))
    for name in [tenant_name, *(product_names or [])]:
        if name and re.search(r"\b" + re.escape(name) + r"\b", category, re.IGNORECASE):
            problems.append(_problem(
                "$.headline", "comparison:headline_slots",
                f"headline's <category> slot contains {name!r}; it names the product category only",
            ))
            break
    if audience.strip(" .,!?").lower() in GENERIC_AUDIENCE_WORDS:
        problems.append(_problem(
            "$.headline", "comparison:headline_slots",
            f"<audience> slot is just {audience!r}; name people by their situation or goal",
        ))
    return problems


def find_alternative_violations(page, allowlist):
    problems = []
    entries = _entries(page.get("alternatives"))
    lo, hi = ALTERNATIVE_RANGE
    if not lo <= len(entries) <= hi:
        problems.append(_problem(
            "$.alternatives", "comparison:alternatives_count",
            f"page has {len(entries)} alternatives; it needs {lo}-{hi}",
        ))
    seen = set()
    llo, lhi = ALTERNATIVE_LIST_RANGE
    for i, entry in enumerate(entries):
        path = f"$.alternatives[{i}]"
        if not isinstance(entry, dict):
            problems.append(_problem(path, f"comparison:alternative_shape:{i}", "alternative is not an object"))
            continue
        alt_id = entry.get("id")
        if alt_id not in allowlist:
            problems.append(_problem(
                f"{path}.id", f"comparison:alternatives_allowlist:{i}",
                f"alternative id {alt_id!r} is not in the allowlist {list(allowlist)}",
            ))
        elif alt_id in seen:
            problems.append(_problem(
                f"{path}.id", f"comparison:alternatives_allowlist:{i}", f"alternative {alt_id!r} appears twice",
            ))
        seen.add(alt_id)
        similarities = _entries(entry.get("similarities"))
        differences = _entries(entry.get("differences"))
        for field, items in (("similarities", similarities), ("differences", differences)):
            if not llo <= len(items) <= lhi:
                problems.append(_problem(
                    f"{path}.{field}", f"comparison:alternative_shape:{i}:{field}",
                    f"{field} has {len(items)} entries; it needs {llo}-{lhi}",
                ))
        texts = [(f"{path}.summary", entry.get("summary"))]
        texts += [(f"{path}.similarities[{k}]", s) for k, s in enumerate(similarities)]
        for k, diff in enumerate(differences):
            if not isinstance(diff, dict):
                problems.append(_problem(
                    f"{path}.differences[{k}]", f"comparison:alternative_shape:{i}:differences",
                    'each difference is {"theirs": "...", "ours": {"text": "...", "claim_ids": [...]}}',
                ))
                continue
            texts.append((f"{path}.differences[{k}].theirs", diff.get("theirs")))
            ours = diff.get("ours")
            if not isinstance(ours, dict) or not (ours.get("text") or "").strip():
                problems.append(_problem(
                    f"{path}.differences[{k}].ours", f"comparison:alternative_shape:{i}:differences",
                    'the tenant\'s side of a difference is {"text": "...", "claim_ids": [...]}',
                ))
        for text_path, text in texts:
            if not isinstance(text, str):
                continue
            reason = _numeric_reason(text)
            if reason:
                problems.append(_problem(
                    text_path, f"comparison:alternative_digits:{text_path}",
                    f"an alternative's description states {reason}. Nothing verified describes an "
                    "alternative, so it is described in plain words only -- remove every digit, "
                    "dollar amount and percentage from this line (the ad's own figures included)",
                    text=text,
                ))
                continue
            word = _trigger_word(text)
            if word:
                problems.append(_problem(
                    text_path, f"comparison:alternative_claims:{text_path}",
                    f'an alternative\'s description uses the word "{word}", which needs a verified '
                    "claim -- and no claim describes an alternative; rewrite the line without it",
                    text=text,
                ))
    return problems


def find_per_model_violations(page, models, field):
    """best_for / who_for: exactly one {"id", "text"} per model, in column
    order. Claim checks on "text" are validate_page_claim_ids' job, as for
    every other {"text", "claim_ids"} node."""
    entries = _entries(page.get(field))
    ids = [m.get("id") for m in models]
    got = [e.get("id") if isinstance(e, dict) else None for e in entries]
    problems = []
    if got != ids:
        problems.append(_problem(
            f"$.{field}", f"comparison:{field}",
            f"{field} ids are {got}; they must be exactly {ids}, one entry per model in column order",
        ))
    for i, entry in enumerate(entries):
        if isinstance(entry, dict) and not (entry.get("text") or "").strip():
            problems.append(_problem(f"$.{field}[{i}]", f"comparison:{field}", f"{field}[{i}] has no text"))
    return problems


def find_faq_violations(page, digit_exempt_terms=None):
    faq = page.get("faq")
    questions = _entries(faq.get("questions") if isinstance(faq, dict) else faq)
    lo, hi = FAQ_COUNT_RANGE
    problems = []
    if not lo <= len(questions) <= hi:
        problems.append(_problem(
            "$.faq.questions", "comparison:faq_count", f"FAQ has {len(questions)} questions; it needs {lo}-{hi}",
        ))
    for i, entry in enumerate(questions):
        if not isinstance(entry, dict):
            continue
        answer = entry.get("answer") or ""
        reason = _trigger_reason(answer, digit_exempt_terms)
        if reason and not entry.get("claim_ids"):
            problems.append(_problem(
                f"$.faq.questions[{i}].answer", f"comparison:faq_claims:{i}",
                f"FAQ answer needs at least one claim_id ({reason}) -- cite a verified claim_id, "
                "or rewrite the answer without it",
                text=answer,
            ))
    return problems


def find_section_count_violations(page):
    problems = []
    paragraphs = _entries((page.get("numbers_mean") or {}).get("paragraphs")
                          if isinstance(page.get("numbers_mean"), dict) else page.get("numbers_mean"))
    if len(paragraphs) != NUMBERS_MEAN_COUNT:
        problems.append(_problem(
            "$.numbers_mean", "comparison:numbers_mean",
            f"numbers_mean has {len(paragraphs)} paragraphs; it needs exactly {NUMBERS_MEAN_COUNT}",
        ))
    recap = _entries((page.get("closing") or {}).get("recap") if isinstance(page.get("closing"), dict) else None)
    if len(recap) != RECAP_BULLET_COUNT:
        problems.append(_problem(
            "$.closing.recap", "comparison:recap",
            f"closing recap has {len(recap)} bullets; it must have exactly {RECAP_BULLET_COUNT}",
        ))
    return problems


def find_headline_case_violations(page, models, tenant_name=None):
    """Cycle 60: the headline is sentence case -- a title-case headline
    (is_title_case) fails, naming the words to write in lower case."""
    headline = page.get("headline") or ""
    names = [tenant_name] + [m.get("name") for m in models]
    if not isinstance(headline, str) or not is_title_case(headline, names):
        return []
    return [_problem(
        "$.headline", "comparison:headline_case",
        f"headline {headline!r} is in title case; write it in sentence case -- capitalize the first "
        f"word and proper names only: {display_headline(headline, names)!r}",
    )]


def _word_count(text):
    return len(re.findall(r"\S+", text or ""))


def find_copy_length_violations(page):
    """Cycle 60: the dek is one line of at most DEK_MAX_WORDS words, and
    each best-for line at most BEST_FOR_MAX_WORDS."""
    problems = []
    dek = page.get("dek")
    dek_text = dek.get("text") if isinstance(dek, dict) else dek
    if isinstance(dek_text, str) and _word_count(dek_text) > DEK_MAX_WORDS:
        problems.append(_problem(
            "$.dek.text", "comparison:dek_length",
            f"dek is {_word_count(dek_text)} words; keep it to one plain sentence of at most "
            f"{DEK_MAX_WORDS} words",
            text=dek_text,
        ))
    for i, entry in enumerate(_entries(page.get("best_for"))):
        text = entry.get("text") if isinstance(entry, dict) else None
        if isinstance(text, str) and _word_count(text) > BEST_FOR_MAX_WORDS:
            problems.append(_problem(
                f"$.best_for[{i}].text", f"comparison:best_for_length:{i}",
                f"best_for[{i}] is {_word_count(text)} words; name who this model suits in at most "
                f"{BEST_FOR_MAX_WORDS} words",
                text=text,
            ))
    return problems


def find_image_violations(page, models):
    problems = []
    reserved = {((m.get("image") or {}).get("id")) for m in models} - {None}
    picks = {}
    for field in ("hero", "lifestyle"):
        asset_id = (page.get(field) or {}).get("asset_id") if isinstance(page.get(field), dict) else None
        if not asset_id:
            problems.append(_problem(
                f"$.{field}.asset_id", f"comparison:images:{field}", f"page has no {field}.asset_id",
            ))
            continue
        if asset_id in reserved:
            problems.append(_problem(
                f"$.{field}.asset_id", f"comparison:images:{field}",
                f"{field} uses {asset_id!r}, which is already a model's column image; pick another asset",
            ))
        picks[field] = asset_id
    if len(picks) == 2 and picks["hero"] == picks["lifestyle"]:
        problems.append(_problem(
            "$.lifestyle.asset_id", "comparison:images:lifestyle", "lifestyle repeats the hero image",
        ))
    return problems


def find_renderer_owned_violations(page):
    problems = []
    for path, node in walk_page(page):
        if not isinstance(node, dict):
            continue
        for key in RENDERER_OWNED_KEYS:
            if key in node:
                problems.append(_problem(
                    f"{path}.{key}", f"comparison:renderer_owned:{key}",
                    f"{key!r} is built by the renderer from facts_pack, never written here -- remove it",
                ))
    return problems


def find_comparison_violations(page, facts_pack, tenant_name=None, allowlist=None):
    """Every comparison-specific structural check, combined. Wired into
    harness/repair.py's check_page_gates for this cartridge only. Competitor
    and retired names are the existing forbidden-vocab gate's job
    (vocab banned_names), unchanged."""
    if not isinstance(page, dict):
        return []
    models = _models(facts_pack)
    allowlist = tuple(allowlist) if allowlist is not None else allowed_alternatives()
    chosen = [e.get("id") for e in _entries(page.get("alternatives")) if isinstance(e, dict)]
    product_names = (facts_pack or {}).get("digit_exempt_terms") or []
    product_names = list(product_names) + [m.get("name") for m in models if m.get("name")]
    problems = []
    problems += find_axis_and_headline_violations(page, models, chosen, tenant_name, product_names)
    problems += find_headline_case_violations(page, models, tenant_name)
    problems += find_copy_length_violations(page)
    problems += find_alternative_violations(page, allowlist)
    problems += find_per_model_violations(page, models, "best_for")
    problems += find_per_model_violations(page, models, "who_for")
    problems += find_faq_violations(page, (facts_pack or {}).get("digit_exempt_terms"))
    problems += find_section_count_violations(page)
    problems += find_image_violations(page, models)
    problems += find_renderer_owned_violations(page)
    return problems

