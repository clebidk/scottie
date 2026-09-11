"""The block registry (docs/KIMI-LONG-RUN.md phase 3): tenant-neutral,
static layout blocks a cartridge template composes and a writer selects by
id. Blocks are layout, never copy -- every block.html binds its content from
Jinja placeholders; the copy inside always comes from page.json / verified
claims through the existing gate.

Layout on disk:

    harness/blocks/
      registry.json          shadcn registry-item shape, one item per block
      <name>/block.html      Jinja partial (no literal copy, no scripts)
      <name>/block.css       optional; required when meta.styling == "self"
      <name>/screenshot.svg  wireframe capture of the block's shape

render_page puts this directory on the Jinja loader's search path, so a
cartridge template composes a block with
`{% include "<name>/block.html" %}` (a fixed default) or
`{% include page.blocks.get("<slot>", "<default>") ~ "/block.html" %}`
(the writer's recorded variant choice).
"""
import json
from pathlib import Path

BLOCKS_DIR = Path(__file__).resolve().parent
REGISTRY_PATH = BLOCKS_DIR / "registry.json"

# Block categories, matching the design-notes-batch50.md sections they were
# seeded from.
CATEGORIES = ("hero", "proof", "body", "cta", "comparison")


def load_registry(path=None):
    """The parsed registry.json. Raises ValueError on malformed JSON -- the
    block gate (tests/test_blocks.py) is what keeps this from ever firing in
    practice."""
    path = Path(path) if path else REGISTRY_PATH
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError(f"{path}: registry must be an object with an 'items' list")
    return data


def iter_blocks(path=None):
    """Yield (entry, block_dir) for every registered block."""
    data = load_registry(path)
    root = Path(path).parent if path else BLOCKS_DIR
    for entry in data["items"]:
        yield entry, root / entry["name"]


def block_names(path=None):
    return [entry["name"] for entry in load_registry(path)["items"]]


def get_block(name, path=None):
    for entry, block_dir in iter_blocks(path):
        if entry["name"] == name:
            return entry, block_dir
    return None, None


def choice(page, slot, default):
    """The block id a page's writer selected for `slot`, or `default`.

    The selection comes from page.json's optional top-level "blocks" map,
    which is writer-controlled -- so the value is only ever returned when it
    is a registered block name (kebab-case, gate-enforced). Anything else
    falls back to the default. This is what makes the dynamic
    `{% include choice ~ "/block.html" %}` safe: a registered name can never
    contain a path separator or "..", so the include cannot escape the
    blocks directory (the same class of bug review R36 closed elsewhere)."""
    blocks = page.get("blocks") if isinstance(page, dict) else None
    if isinstance(blocks, dict):
        selected = blocks.get(slot)
        if isinstance(selected, str) and selected in block_names():
            return selected
    return default
