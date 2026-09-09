"""One Claude call per cartridge: (ad_brief, facts_pack) -> page.json.

System prompt = cartridge.md + schema.json + a global voice block.
User message = ad_brief + facts_pack + up to 2 exemplars if present.
"""
import json
from pathlib import Path

from .jsonutil import extract_json

TYPE_MAP = {
    "string": str,
    "array": list,
    "object": dict,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
}

GLOBAL_VOICE_BLOCK = """## Voice and output rules

Voice: plain, specific, no hype words ("game-changer", "unlock", "elevate", "journey", "revolutionary", "unleash"). No exclamation marks. Prefer short declarative sentences.

Never write the byline, publish/update dates, the "Advertisement" label, or the disclosure paragraph -- the renderer injects those automatically.

Every piece of text that states a number, a percentage, a dollar amount, or uses the words "medical", "clinical", "study", "proven", "EMF", "rated", or "reviews" MUST carry a non-empty "claim_ids" array referencing an id from facts_pack.verified_claims. Never invent a claim id. If you cannot support a statement with a verified claim, do not make the statement. Each row in facts_pack.specs already carries its own "claim_id" -- when you restate a spec fact in prose (not just in a specs table), copy that same claim_id into the prose sentence's claim_ids array; do not state a spec number in prose without it.

Reference images only by an asset id from facts_pack.assets, in an "asset_id" field -- never by URL directly.

If the user message includes "exemplars", use them only as a voice and structure reference. A JSON exemplar shows the page.json shape; a {"reference_article": "..."} exemplar is a real published Peak Saunas article -- match its tone and rigor, but never copy its numbers, claims, or competitor comparisons into this page unless the same fact also appears in this page's own facts_pack.verified_claims.

Output ONLY a single JSON object matching the schema you were given. No markdown fences, no commentary before or after."""


def validate_schema(data, schema):
    if not isinstance(data, dict):
        return ["page.json is not a JSON object"]
    errors = []
    for key in schema.get("required", []):
        if key not in data:
            errors.append(f"missing required key: {key}")
    for key, prop in schema.get("properties", {}).items():
        if key in data and isinstance(prop, dict):
            typ_name = prop.get("type")
            expected = TYPE_MAP.get(typ_name)
            if expected and not isinstance(data[key], expected):
                errors.append(f"key {key!r} expected type {typ_name}, got {type(data[key]).__name__}")
    return errors


def load_cartridge_prompt(cartridge_dir):
    cartridge_md = (cartridge_dir / "cartridge.md").read_text()
    schema = json.loads((cartridge_dir / "schema.json").read_text())
    return cartridge_md, schema


def load_exemplars(cartridge_dir, limit=2):
    """Up to `limit` exemplars from cartridges/<name>/exemplars/. A .json file
    is parsed as a page.json-shaped object; a .md/.txt file is a real
    reference article and is passed through as text (voice/structure
    reference, not something to copy verbatim)."""
    ex_dir = Path(cartridge_dir) / "exemplars"
    if not ex_dir.exists():
        return []
    files = sorted(ex_dir.glob("*.json")) + sorted(ex_dir.glob("*.md")) + sorted(ex_dir.glob("*.txt"))
    exemplars = []
    for f in files[:limit]:
        if f.suffix == ".json":
            exemplars.append(json.loads(f.read_text()))
        else:
            exemplars.append({"reference_article": f.read_text()})
    return exemplars


def write_page(*, cartridge_name, cartridges_dir, ad_brief, facts_pack, client, model, budget, log):
    cartridge_dir = Path(cartridges_dir) / cartridge_name
    cartridge_md, schema = load_cartridge_prompt(cartridge_dir)
    exemplars = load_exemplars(cartridge_dir)

    system = (
        cartridge_md
        + "\n\n## JSON schema for page.json\n"
        + json.dumps(schema, indent=2)
        + "\n\n"
        + GLOBAL_VOICE_BLOCK
    )

    user_payload = {"ad_brief": ad_brief, "facts_pack": facts_pack}
    if exemplars:
        user_payload["exemplars"] = exemplars

    stage = f"write.{cartridge_name}"
    last_error = None
    for attempt in range(2):
        budget.check()
        response = client.messages.create(
            model=model,
            max_tokens=6000,
            # This is bounded JSON extraction, not a reasoning task -- disable
            # thinking so the full max_tokens budget goes to visible output.
            # Sonnet 5 runs adaptive thinking by default when unset, and
            # thinking tokens count against max_tokens; without this the
            # model can exhaust the budget on hidden reasoning and return an
            # empty/truncated response (observed in practice on longform).
            thinking={"type": "disabled"},
            system=system,
            messages=[{"role": "user", "content": json.dumps(user_payload)}],
        )
        usage = response.usage
        budget.record_call(usage.input_tokens, usage.output_tokens)
        log.call(stage, model, usage.input_tokens, usage.output_tokens)

        text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
        try:
            page = extract_json(text)
            errors = validate_schema(page, schema)
            if errors:
                raise ValueError("; ".join(errors))
            return page
        except Exception as e:
            last_error = e
            log.event(stage, f"invalid page.json on attempt {attempt + 1}: {e}")
            continue

    raise ValueError(f"{stage} failed after retry: {last_error}")
