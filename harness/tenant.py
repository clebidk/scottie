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
from .errors import HarnessError
from .textutil import is_safe_tenant_name
from . import exits

TENANTS_DIR = REPO_ROOT / "tenants"
TEMPLATE_DIR = TENANTS_DIR / "_template"
DEFAULT_FILE = TENANTS_DIR / "default.txt"

TENANT_ENV_VAR = "HARNESS_TENANT"

# Filled in by `harness tenant init`; a value still reading this means the
# tenant has not been configured.
PLACEHOLDER = "CHANGE ME"

# Cycle 22 finding R22: the tenant file format's own version. Bumped only when
# a change to tenant.yaml's shape would make an older file WRONG -- a renamed
# key, changed nesting, or a default that silently comes to mean something
# else. Not bumped when a key is merely added, since Tenant.get already falls
# back for a key a tenant never set.
TENANT_SCHEMA_VERSION = 1

# Without these a run cannot produce a correct page: name and slug identify the
# company, site_host/site_url decide what counts as an internal link and how a
# source is labelled.
REQUIRED_TENANT_KEYS = ("name", "slug", "site_url", "site_host")

# Keys whose type the engine relies on. A `models:` that parsed as a string
# because of a YAML indentation slip used to surface as a TypeError deep inside
# the writer; this turns it into one line at load time.
TENANT_KEY_TYPES = {
    "shopify": dict,
    "reviews": dict,
    "theme": dict,
    "models": dict,
    "cta_variants": dict,
    "notifications": dict,
    "pdp_facts": dict,
    "source_path_labels": dict,
    "default_cartridge_pool": list,
    "benefit_allowlist_ids": list,
    "universal_claim_ids": list,
    "excluded_benefit_ids": list,
    "listicle_pack_models": list,
    "claim_id_prefixes": list,
    "reviewers": list,
    # Cycle 30
    "cartridges": dict,
    "design_reference": list,
    # Cycle 68
    "meta": dict,
    # Cycle 67
    "abtest": dict,
}


class TenantNotConfigured(HarnessError):
    """A tenant directory exists but cannot produce a page yet."""

    exit_code = exits.TENANT_NOT_CONFIGURED


class UnknownTenant(HarnessError):
    """No directory under tenants/ for the requested name."""

    exit_code = exits.TENANT_NOT_CONFIGURED


class TenantFileInvalid(TenantNotConfigured):
    """A tenant file exists but does not parse, or fails validation. A subclass
    of TenantNotConfigured so it keeps the same exit code and the same one-line
    treatment in cli.main -- an operator fixing a typo in their own YAML should
    never see a parser traceback."""


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
    path = Path(path)
    if not path.exists():
        return {} if default is None else default
    try:
        return yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise TenantFileInvalid(f"{path} is not valid YAML: {e}") from None


def _load_json(path):
    """json.loads with the file named in the message. A malformed
    claims/verified.json used to surface as a bare JSONDecodeError with no
    indication of which of a tenant's several JSON files it came from."""
    path = Path(path)
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise TenantFileInvalid(f"{path} is not valid JSON: {e}") from None


def _flatten(prefix, node, out):
    """{"tenant.name": "...", "tenant.theme.name": "..."} from nested data."""
    if isinstance(node, dict):
        for k, v in node.items():
            _flatten(f"{prefix}.{k}" if prefix else str(k), v, out)
    elif isinstance(node, (list, tuple)):
        out[prefix] = ", ".join(str(v) for v in node)
    elif node is not None:
        out[prefix] = str(node)


# ---------------------------------------------------------------------------
# Cycle 64: product naming. One rule for every cartridge: the full name is
# tenant.yaml's product_name_format with the model filled in ("Acme {model}"
# -> "Acme One"), the short name is the model alone ("One"), and the long
# catalog title's capacity/style words are never part of either -- they are
# a separate sentence-case descriptor ("1-person infrared sauna") a template
# may show on its own line.
# ---------------------------------------------------------------------------

DEFAULT_PRODUCT_NAME_FORMAT = "{model}"


def _name_prefixes(tenant):
    """Words a product title may carry in front of its model: the old
    storefront prefix (product_display_strip_prefix) and the brand word of
    product_name_format, longest first so "Acme Saunas" goes before "Acme"."""
    fmt = (tenant.get("product_name_format") if tenant else None) or DEFAULT_PRODUCT_NAME_FORMAT
    candidates = [fmt.split("{model}")[0], (tenant.get("product_display_strip_prefix") if tenant else "") or ""]
    return sorted({c.strip() for c in candidates if c.strip()}, key=len, reverse=True)


def _strip_name_prefixes(text, prefixes):
    """`text` with any leading prefix word(s) removed, matched
    case-insensitively and repeatedly ("Acme Saunas Acme One" -> "One")."""
    text = " ".join(str(text or "").split())
    stripped = True
    while stripped:
        stripped = False
        for prefix in prefixes:
            if text.casefold().startswith(prefix.casefold() + " "):
                text = text[len(prefix) + 1:]
                stripped = True
    return text


def _model_words(text):
    """The leading words of a prefix-free title, up to the first word that
    starts with a digit (the capacity, "1-Person"): "Big Sky 4-Person
    Outdoor Cabin" -> "Big Sky". A curated model name with no
    capacity word comes back whole."""
    words = []
    for word in text.split():
        if word[:1].isdigit():
            break
        words.append(word)
    return " ".join(words)


def product_names(product, tenant=None):
    """{"full_name", "short_name", "descriptor"} for one product.

    `product` is any product-shaped dict the harness carries -- a catalog
    entry (claims/products.json), facts_pack.product (old or new), a
    model_options row, a comparison or quiz model -- or a bare title string.
    The model comes from the first of model_name/name/seo_title/short_name/
    title that yields one; the descriptor is whatever a long title says after
    the model, in sentence case. Both come back "" when nothing names the
    product. A tenant with no product_name_format names a product by its
    model alone."""
    if isinstance(product, str):
        product = {"title": product}
    product = product or {}
    fmt = (tenant.get("product_name_format") if tenant else None) or DEFAULT_PRODUCT_NAME_FORMAT
    prefixes = _name_prefixes(tenant)
    model = ""
    for key in ("model_name", "name", "seo_title", "short_name", "title"):
        model = _model_words(_strip_name_prefixes(product.get(key), prefixes))
        if model:
            break
    descriptor = ""
    for key in ("seo_title", "short_name", "title", "name"):
        rest = _strip_name_prefixes(product.get(key), prefixes)
        if model and rest.casefold().startswith(model.casefold() + " "):
            descriptor = rest[len(model):].strip(" -–—,").lower()
            if descriptor:
                break
    # a dict this helper already named (a cycle 64 facts_pack row) keeps its own
    descriptor = descriptor or str(product.get("descriptor") or "")
    return {
        "full_name": fmt.format(model=model) if model else "",
        "short_name": model,
        "descriptor": descriptor[:1].upper() + descriptor[1:],
    }


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
    def abtests_dir(self):
        """Cycle 67: A/B/C test records and the beacon event database. Not
        under out/, so clearing old run output never deletes a test."""
        return self.root / "abtests"

    @property
    def jobs_dir(self):
        """Cycle 69: the listicle site's job queue and audit log
        (harness/jobs.py). Runtime data, like abtests/."""
        return self.root / "jobs"

    @property
    def inbox_dir(self):
        return self.root / "inbox"

    @property
    def meta_inbox_dir(self):
        """Cycle 68: ads pulled from the tenant's Meta ad account, one
        directory per ad id (harness/meta_ingest.py)."""
        return self.root / "meta_inbox"

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

    def display_product_name(self, name):
        """A product name as a reader should see it and as the writer should
        be handed it.

        A storefront's own product titles often lead with the full company
        name ("<Company> <Model> 2-Person ..."), which reads as a stutter on a
        page that already says who published it, and which the writer would
        otherwise copy into its prose. tenant.yaml's optional
        `product_display_strip_prefix` names that prefix and it is removed
        here. Matching is case-insensitive and the result is trimmed.

        Only COPY goes through this. Slugs, URLs, claim ids and every other
        identifier are left exactly as they are -- they are how a page links
        and cites, not what a reader reads. A tenant that sets no prefix gets
        its name back unchanged, and stripping an already-stripped name is a
        no-op, so a call site may be defensive without double-stripping."""
        prefix = self.get("product_display_strip_prefix") or ""
        text = name or ""
        if prefix and text.lower().startswith(prefix.lower()):
            # A title that is nothing BUT the prefix would be left nameless;
            # keep the original rather than render an empty product.
            return text[len(prefix):].strip() or text
        return text

    def product_names(self, product):
        """Cycle 64: the one naming rule for a product, for every cartridge.
        See the module-level product_names below."""
        return product_names(product, self)

    def catalog_products(self):
        """Every product dict in this tenant's claims/products.json, read
        from disk (never a live fetch). [] when the file is missing or has
        no products map."""
        path = self.claims_dir / "products.json"
        if not path.exists():
            return []
        products = (_load_json(path) or {}).get("products")
        return [p for p in products.values() if isinstance(p, dict)] if isinstance(products, dict) else []

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
            config.update(_load_json(path))
        return config

    def config_disagreements(self):
        """Review 2026-09-11 R23: keys set in BOTH tenant.yaml and
        claims/config.json with different values. The precedence itself is
        deliberate and unchanged (claims/config.json always wins) -- this
        exists so an operator who edited the file that loses can SEE the
        disagreement (run log, `harness doctor`) instead of wondering why
        their edit did nothing. Returns [(key, tenant.yaml value,
        claims/config.json value)], sorted by key."""
        path = self.claims_dir / "config.json"
        if not path.exists():
            return []
        json_config = _load_json(path)
        disagreements = []
        for key in _CONFIG_KEYS_FROM_TENANT_YAML:
            if key in self.config and key in json_config and self.config[key] != json_config[key]:
                disagreements.append((key, self.config[key], json_config[key]))
        return sorted(disagreements)

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
        elif not _load_json(verified):
            missing.append("claims/verified.json (no approved claims)")
        if not products.exists():
            missing.append("claims/products.json")
        elif not (_load_json(products).get("products") or {}):
            missing.append("claims/products.json (no products)")
        return missing

    @property
    def schema_version(self):
        return self.config.get("schema_version", TENANT_SCHEMA_VERSION)

    def validate(self):
        """(errors, warnings) for this tenant's tenant.yaml.

        Cycle 22 finding R21: nothing checked a tenant file at load. A typo'd
        key was silently ignored -- Tenant.get returns the default for a key
        that is not there, so a misconfigured tenant produced plausible-looking
        wrong pages instead of stopping.

        An error means the file is wrong in a way that produces a bad page or a
        crash; require_configured raises on any. A warning means something an
        operator should look at but that the engine can run through, and is
        reported by `harness doctor` and `harness tenant list` rather than
        blocking a run."""
        errors, warnings = [], []

        version = self.config.get("schema_version")
        if version is None:
            warnings.append(
                f"tenant.yaml has no schema_version; assuming {TENANT_SCHEMA_VERSION}. "
                f"Add `schema_version: {TENANT_SCHEMA_VERSION}` to be explicit."
            )
        elif version != TENANT_SCHEMA_VERSION:
            errors.append(
                f"tenant.yaml schema_version is {version!r}, but this harness reads version "
                f"{TENANT_SCHEMA_VERSION}. Compare against tenants/_template/tenant.yaml."
            )

        for key in REQUIRED_TENANT_KEYS:
            value = self.config.get(key)
            if not value:
                errors.append(f"tenant.yaml is missing a value for {key!r}")
            elif PLACEHOLDER in str(value):
                errors.append(f"tenant.yaml's {key!r} is still the {PLACEHOLDER!r} placeholder")

        for key, expected in TENANT_KEY_TYPES.items():
            value = self.config.get(key)
            if key in self.config and value is not None and not isinstance(value, expected):
                errors.append(
                    f"tenant.yaml's {key!r} must be a {expected.__name__}, "
                    f"got {type(value).__name__}"
                )

        unknown = sorted(set(self.config) - known_tenant_keys())
        if unknown:
            warnings.append(
                "tenant.yaml has key(s) this harness never reads: "
                + ", ".join(repr(k) for k in unknown)
                + " -- check the spelling against tenants/_template/tenant.yaml."
            )
        return errors, warnings

    def require_configured(self):
        """Raise TenantNotConfigured unless this tenant can produce a page."""
        missing = self.missing_pieces()
        if missing:
            raise TenantNotConfigured(
                f"tenant not configured: missing {', '.join(missing)} "
                f"under {self.root}. See {self.root / 'README.md'} for the checklist."
            )
        errors, _warnings = self.validate()
        if errors:
            raise TenantFileInvalid(
                f"{self.root / 'tenant.yaml'} is not valid:\n"
                + "\n".join(f"  - {e}" for e in errors)
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


def known_tenant_keys():
    """Every top-level key tenants/_template/tenant.yaml declares, plus
    schema_version. Derived from the template rather than restated here, so
    adding a key to the template is all it takes -- there is no second list to
    forget to update."""
    return set(_load_yaml(TEMPLATE_DIR / "tenant.yaml")) | {"schema_version"}


def tenant_dir(name):
    """tenants/<name>, for a name that is a single path component.

    Cycle 22 finding R36: this used to join whatever it was handed, so
    `--tenant ../../x` read a tenant.yaml from outside the repository and
    `harness tenant init ../evil` copied the template outside it."""
    if not is_safe_tenant_name(name):
        raise UnknownTenant(
            f"invalid tenant name {name!r}: a tenant name is one directory under "
            f"{TENANTS_DIR} -- letters, digits, dot, dash and underscore only."
        )
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


def active_or_none():
    """The active tenant, or None if nothing has activated one yet -- unlike
    active(), this never resolves the default tenant as a side effect. Exists
    so a caller can snapshot and restore this module's global without forcing
    it to be set (harness/vocab.py has the same pair)."""
    return _ACTIVE


def set_active(tenant):
    """Install an already-loaded Tenant (or None) as the active one, without
    the vocab cascade activate() performs. Used to put the global back where a
    caller found it."""
    global _ACTIVE
    _ACTIVE = tenant
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
