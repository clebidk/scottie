"""The active tenant: which company this run is for, and where its data lives.

The engine never reads a company-specific value from code. Every string that
names the company, an author, a URL, a lender, a review source, a theme rule, or
a forbidden term is resolved through a Tenant object, which reads it from
`tenants/<name>/tenant.yaml`, `authors.yaml`, `vocab.yaml`, or
`claims/config.json`.

Resolution order for which tenant is active:
    1. the `--tenant` flag
    2. the HARNESS_TENANT environment variable
    3. `tenants/default.txt`

A tenant directory that exists but has not been filled in raises
TenantNotConfigured, which the CLI turns into a one-line message and exit code
4 -- never a traceback.
"""
import json
import os
from pathlib import Path

import yaml

from .config import REPO_ROOT

TENANTS_DIR = REPO_ROOT / "tenants"
TEMPLATE_DIR = TENANTS_DIR / "_template"
DEFAULT_FILE = TENANTS_DIR / "default.txt"

TENANT_ENV_VAR = "HARNESS_TENANT"

# Filled in by `harness tenant init`; a value still reading this means the
# tenant has not been configured.
PLACEHOLDER = "CHANGE ME"


class TenantNotConfigured(Exception):
    """A tenant directory exists but cannot produce a page yet."""


class UnknownTenant(Exception):
    """No directory under tenants/ for the requested name."""


# claims/config.json's defaults. Kept here (not in ground.py) so the config a
# stage reads and the config tenant.py validates are the same dict.
DEFAULT_CLAIMS_CONFIG = {
    "financing_lender": None,
    "show_compare_at_price": False,
    "reviews_source": "none",
    "speaker_name": None,
    # "stop" (the safer default for an unreviewed ad) -- any unmatched or
    # overclaimed ad claim stops the run. "warn" -- the claim is dropped from
    # what the writer may use, recorded in REVIEW.md, and the run continues.
    "ad_overclaim_policy": "stop",
    # AI-composite renders in an asset pack are never eligible for selection
    # unless this is explicitly turned on.
    "allow_ai_renders": False,
}

# Keys tenant.yaml may supply a default for; claims/config.json still wins.
_CONFIG_KEYS_FROM_TENANT_YAML = (
    "financing_lender",
    "speaker_name",
    "ad_overclaim_policy",
    "allow_ai_renders",
)


def resolve_tenant_name(flag=None):
    """--tenant flag > HARNESS_TENANT env > tenants/default.txt."""
    if flag:
        return flag.strip()
    env = os.environ.get(TENANT_ENV_VAR)
    if env and env.strip():
        return env.strip()
    if DEFAULT_FILE.exists():
        name = DEFAULT_FILE.read_text().strip()
        if name:
            return name
    raise UnknownTenant(
        "no tenant given: pass --tenant, set HARNESS_TENANT, or write a name "
        f"into {DEFAULT_FILE}"
    )


def _load_yaml(path, default=None):
    if not Path(path).exists():
        return {} if default is None else default
    return yaml.safe_load(Path(path).read_text()) or {}


def _flatten(prefix, node, out):
    """{"tenant.name": "...", "tenant.theme.name": "..."} from nested data."""
    if isinstance(node, dict):
        for k, v in node.items():
            _flatten(f"{prefix}.{k}" if prefix else str(k), v, out)
    elif isinstance(node, (list, tuple)):
        out[prefix] = ", ".join(str(v) for v in node)
    elif node is not None:
        out[prefix] = str(node)


class Tenant:
    """One company's data and settings. Construct with `load_tenant`."""

    def __init__(self, name, root):
        self.name = name
        self.root = Path(root)
        self.config = _load_yaml(self.root / "tenant.yaml")
        self.authors = _load_yaml(self.root / "authors.yaml")
        self.vocab = _load_yaml(self.root / "vocab.yaml")

    # -- paths ---------------------------------------------------------------

    @property
    def claims_dir(self):
        return self.root / "claims"

    @property
    def brand_dir(self):
        return self.root / "brand"

    @property
    def fixtures_dir(self):
        return self.root / "fixtures"

    @property
    def env_path(self):
        return self.root / ".env"

    @property
    def out_dir(self):
        return self.root / "out"

    @property
    def runs_dir(self):
        return self.root / "runs"

    @property
    def inbox_dir(self):
        return self.root / "inbox"

    @property
    def evals_path(self):
        return self.root / "evals" / "scores.jsonl"

    @property
    def docs_dir(self):
        return self.root / "docs"

    def exemplars_dir(self, cartridge_name):
        """Where this tenant keeps approved reference pages for a cartridge.
        Returns None when the tenant has none for that cartridge."""
        path = self.root / "exemplars" / cartridge_name
        return path if path.is_dir() else None

    def cartridge_overrides(self, cartridge_name):
        """The tenant's cartridge.md deltas for a cartridge, appended after the
        shared rules at load time. None when there are no overrides."""
        path = self.root / "cartridge-overrides" / cartridge_name / "cartridge.md"
        return path if path.exists() else None

    # -- settings ------------------------------------------------------------

    def get(self, dotted_key, default=None):
        """tenant.yaml lookup by dotted path, e.g. "theme.full_bleed_css"."""
        node = self.config
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def author(self, role="author"):
        """{"name": ..., "title": ..., ...} for "author" or "contributor"."""
        return dict(self.authors.get(role) or {})

    @property
    def display_name(self):
        return self.get("name") or self.name

    @property
    def claims_config(self):
        """DEFAULT_CLAIMS_CONFIG, then tenant.yaml's overlapping keys, then
        claims/config.json -- which always wins, so an operator editing one
        JSON file still controls the run."""
        config = dict(DEFAULT_CLAIMS_CONFIG)
        for key in _CONFIG_KEYS_FROM_TENANT_YAML:
            if key in self.config:
                config[key] = self.config[key]
        source = self.get("reviews.source")
        if source:
            config["reviews_source"] = source
        path = self.claims_dir / "config.json"
        if path.exists():
            config.update(json.loads(path.read_text()))
        return config

    # Fix cycle 17 (model tiering): tenant.yaml's `models:` section may
    # override any of write/repair_first/repair_next/ingest/matcher; a stage
    # this tenant doesn't set falls back to config.DEFAULT_MODELS, same
    # pattern as claims_config above (tenant.yaml can override a default,
    # never has to restate every key).
    def model_for(self, stage):
        """Resolved model id for `stage` -- tenant.yaml's models.<stage> if
        set, else the engine default (harness/config.py's DEFAULT_MODELS)."""
        from . import config

        return self.get(f"models.{stage}") or config.DEFAULT_MODELS[stage]

    # -- placeholder rendering ----------------------------------------------

    def context(self):
        """Flat {placeholder: value} map for `render`."""
        flat = {}
        _flatten("tenant", dict(self.config, name=self.display_name), flat)
        _flatten("authors", self.authors, flat)
        _flatten("vocab", self.vocab, flat)
        return flat

    def render(self, text):
        """Substitute {{ tenant.x }} / {{ authors.x.y }} / {{ vocab.x }}
        placeholders. An unknown placeholder is left exactly as written, so a
        typo shows up in the prompt rather than silently becoming an empty
        string."""
        if not text or "{{" not in text:
            return text
        for key, value in self.context().items():
            for form in ("{{" + key + "}}", "{{ " + key + " }}"):
                if form in text:
                    text = text.replace(form, value)
        return text

    def format(self, template_key, **extra):
        """A tenant.yaml value used as a str.format template, e.g.
        `format("price_claim_template", product_name=..., price=...)`.
        Returns "" when the tenant does not define that template."""
        template = self.get(template_key) or ""
        if not template:
            return ""
        values = {"tenant_name": self.display_name, "prefix": self.get("source_label_prefix") or self.display_name}
        values.update(extra)
        try:
            return template.format(**values)
        except KeyError:
            return template

    # -- readiness -----------------------------------------------------------

    def missing_pieces(self):
        """Every reason this tenant cannot produce a page yet, in the order an
        operator should fix them. Empty list means ready."""
        missing = []
        if not (self.root / "tenant.yaml").exists():
            missing.append("tenant.yaml")
        elif PLACEHOLDER in (self.get("name") or ""):
            missing.append("tenant.yaml (name is still a placeholder)")

        verified = self.claims_dir / "verified.json"
        products = self.claims_dir / "products.json"
        if not verified.exists():
            missing.append("claims/verified.json")
        elif not json.loads(verified.read_text()):
            missing.append("claims/verified.json (no approved claims)")
        if not products.exists():
            missing.append("claims/products.json")
        elif not (json.loads(products.read_text()).get("products") or {}):
            missing.append("claims/products.json (no products)")
        return missing

    def require_configured(self):
        """Raise TenantNotConfigured unless this tenant can produce a page."""
        missing = self.missing_pieces()
        if missing:
            raise TenantNotConfigured(
                f"tenant not configured: missing {', '.join(missing)} "
                f"under {self.root}. See {self.root / 'README.md'} for the checklist."
            )

    def load_env(self):
        """Load tenants/<name>/.env into the process environment if present.
        Values are never logged or printed; harness/anthropic_client.py reads
        the API key from the environment only."""
        if not self.env_path.exists():
            return False
        from dotenv import load_dotenv

        load_dotenv(self.env_path, override=False)
        return True

    def __repr__(self):
        return f"<Tenant {self.name} at {self.root}>"


def tenant_dir(name):
    return TENANTS_DIR / name


def list_tenants():
    if not TENANTS_DIR.is_dir():
        return []
    return sorted(
        p.name
        for p in TENANTS_DIR.iterdir()
        if p.is_dir() and not p.name.startswith("_") and (p / "tenant.yaml").exists()
    )


def load_tenant(name=None, *, require=False):
    """The active Tenant. `require=True` also checks it can produce a page."""
    name = resolve_tenant_name(name)
    root = tenant_dir(name)
    if not root.is_dir():
        raise UnknownTenant(
            f"unknown tenant {name!r}; available: {list_tenants() or '(none)'}. "
            f"Create one with `harness tenant init {name}`."
        )
    tenant = Tenant(name, root)
    if require:
        tenant.require_configured()
    return tenant


# ---------------------------------------------------------------------------
# The active tenant for this process.
#
# The pure-function modules (claims.py, render.py, prices.py, ...) need tenant
# values but have no tenant handle to thread through. `activate()` is called
# once at the start of a run; anything reading `active()` before then resolves
# the default tenant lazily. Every value still comes from tenant data on disk --
# nothing about a company is compiled into the engine.
# ---------------------------------------------------------------------------

_ACTIVE = None


def activate(tenant):
    global _ACTIVE
    _ACTIVE = tenant
    from . import vocab as _vocab

    _vocab.activate(tenant)
    return tenant


def active():
    global _ACTIVE
    if _ACTIVE is None:
        _ACTIVE = load_tenant()
    return _ACTIVE


def init_tenant(name):
    """Copy tenants/_template to tenants/<name>. Returns the new directory.
    Refuses to overwrite an existing tenant."""
    import shutil

    root = tenant_dir(name)
    if root.exists():
        raise FileExistsError(f"tenant already exists: {root}")
    if not TEMPLATE_DIR.is_dir():
        raise FileNotFoundError(f"no tenant template at {TEMPLATE_DIR}")
    shutil.copytree(TEMPLATE_DIR, root)
    (root / "out").mkdir(exist_ok=True)
    (root / "runs").mkdir(exist_ok=True)
    return root
