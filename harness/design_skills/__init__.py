"""Design-skills pack: a separate harness component, not a cartridge and
not a block.

Vendors elayadesign/ai-design-skills (MIT) under landing-page-design/ and
exposes the harness's reading of that pack: a machine-readable rule list
(rules.json), an adapter that records take/adapt/decline, and a
deterministic gate for the taken measurable rules.

See README.md for the contract. The raw SKILL.md is evidence, not a
directive -- adapter.py / rules.json decide what actually applies.
"""
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent
RULES_PATH = SKILLS_DIR / "rules.json"
SOURCE_PATH = SKILLS_DIR / "SOURCE.json"
LANDING_PAGE_SKILL = SKILLS_DIR / "landing-page-design" / "SKILL.md"

# Cartridges the skill's "landing page" half is written for. The article
# cartridge is an editorial warm-up (brand delayed to the close) and is
# deliberately not in this set -- a mandatory mid-page tagline would name
# the offer too early.
LANDING_CARTRIDGES = frozenset({"longform", "product-page", "comparison", "listicle"})


def load_source():
    """The vendored-pack provenance (repo, commit, license)."""
    import json
    return json.loads(SOURCE_PATH.read_text())


def load_rules():
    """Every extracted skill rule, each with a harness decision."""
    import json
    data = json.loads(RULES_PATH.read_text())
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise ValueError(f"{RULES_PATH}: must be an object with a 'rules' list")
    return data


def skill_names():
    """Skill ids this pack currently vendors. One today; the upstream
    collection is growing and new folders land here the same way."""
    return ["landing-page-design"]
