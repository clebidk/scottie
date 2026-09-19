"""Single source of truth for forbidden vocabulary -- loaded as data, never
hardcoded.

Every list here comes from the active tenant's `vocab.yaml`. The writer prompt
(write.py) and the deterministic gate (claims.py) both read this module, so a
word added to a tenant's vocab.yaml is enforced by the gate and told to the
writer in the same edit, with no code change at all.

Import the MODULE, not its names:

    from . import vocab
    ... vocab.ALWAYS_FORBIDDEN_TERMS ...

`from .vocab import ALWAYS_FORBIDDEN_TERMS` binds the value at import time and
would not see a later `activate()`. Reading the attribute off the module always
gets the active tenant's list.

The active vocabulary is set explicitly by `activate(tenant)` at the start of a
run, and otherwise resolved lazily from the default tenant on first access.
"""
import re

_ACTIVE = None


class Vocabulary:
    """One tenant's vocab.yaml, plus the derived lists and regexes the gate
    and the writer prompt need."""

    def __init__(self, data=None):
        data = data or {}
        self.emf_terms = tuple(data.get("emf_terms") or ())
        self.banned_names = tuple(data.get("banned_names") or ())
        self.hype_words = tuple(data.get("hype_words") or ())
        self.ban_exclamation_mark = bool(data.get("ban_exclamation_mark", True))
        self.hype_synonyms = dict(data.get("hype_synonyms") or {})
        self.forbidden_lender_names = tuple(data.get("forbidden_lender_names") or ())
        self.trigger_words = tuple(data.get("trigger_words") or ())
        self.trigger_word_synonyms = dict(data.get("trigger_word_synonyms") or {})
        self.implied_claim_forbidden_terms = tuple(data.get("implied_claim_forbidden_terms") or ())
        self.allowed_financing_sentence_no_lender = data.get(
            "allowed_financing_sentence_no_lender", ""
        )
        # Fix cycle 21: the with-lender counterpart to
        # allowed_financing_sentence_no_lender above -- tenant-neutral (no
        # lender name baked in), formatted with the tenant's actual
        # configured lender at read time via allowed_financing_sentence().
        self.allowed_financing_sentence_with_lender_template = data.get(
            "allowed_financing_sentence_with_lender_template", ""
        )
        self.allowed_warranty_sentence = data.get("allowed_warranty_sentence", "")
        self.allowed_warranty_spec_label = data.get("allowed_warranty_spec_label", "Warranty")
        self.allowed_warranty_spec_value = data.get("allowed_warranty_spec_value", "")
        self.visible_text_forbidden_terms = tuple(data.get("visible_text_forbidden_terms") or ())
        self.competitor_aliases = dict(data.get("competitor_aliases") or {})

    # -- derived -------------------------------------------------------------

    @property
    def exclamation_mark(self):
        return "!" if self.ban_exclamation_mark else None

    @property
    def always_forbidden_terms(self):
        """Absolute bans: never allowed anywhere in writer-composed prose, no
        matter which claim_id backs the sentence."""
        terms = self.emf_terms + self.banned_names + self.hype_words
        if self.ban_exclamation_mark:
            terms += ("!",)
        return terms

    @property
    def lender_name_re(self):
        """Matches any forbidden lender name, or nothing at all when the tenant
        lists none (a bare `re.compile("")` would match every string)."""
        if not self.forbidden_lender_names:
            return re.compile(r"(?!x)x")
        return re.compile("|".join(re.escape(n) for n in self.forbidden_lender_names), re.IGNORECASE)

    @property
    def trigger_word_re(self):
        """Cycle 43: a plain `\\b...\\b` boundary treats a hyphen as a word
        edge the same as a space, so "outdoor-rated"/"IP65-rated" tripped the
        "rated" trigger word -- the hyphen gave it a boundary on both sides
        even though it's plainly one compound word. A trigger word now has to
        stand alone: not immediately preceded or followed by a letter OR a
        hyphen. "rated 4.8" and "top rated" (space on both sides) still
        match; "frustrated" still doesn't (no boundary at all, unchanged from
        before)."""
        if not self.trigger_words:
            return re.compile(r"(?!x)x")
        return re.compile(
            r"(?<![A-Za-z-])(?:" + "|".join(self.trigger_words) + r")(?![A-Za-z-])"
        )

    def forbidden_words_block(
        self,
        heading="Forbidden words -- never use any of these, in any form, anywhere on the page:",
    ):
        """The forbidden-word list, verbatim, one per line. Quoted both at the
        top of the writer's system prompt and inside every REVISION REQUIRED
        block, so a repair attempt cannot claim it forgot the list."""
        return heading + "\n" + "\n".join(self.always_forbidden_terms)

    def allowed_financing_sentence(self, lender=None):
        """The one allowed financing sentence for this run: the with-lender
        template filled in with `lender` when given (truthy), else the
        no-lender sentence. Fix cycle 21 -- single source of truth for both
        the writer prompt (harness/write.py) and the gate
        (harness/claims.py's find_financing_violations/
        evaluate_financing_claim), mirroring ALLOWED_FINANCING_SENTENCE_NO_LENDER's
        existing role for the no-lender case."""
        if lender:
            return self.allowed_financing_sentence_with_lender_template.format(lender=lender)
        return self.allowed_financing_sentence_no_lender


def activate(tenant):
    """Make `tenant`'s vocab.yaml the active vocabulary for this process."""
    global _ACTIVE
    _ACTIVE = Vocabulary(getattr(tenant, "vocab", None) or {})
    return _ACTIVE


def set_active(vocabulary):
    """Install an already-built Vocabulary (used by tests)."""
    global _ACTIVE
    _ACTIVE = vocabulary
    return _ACTIVE


def active():
    """The active Vocabulary, resolving the default tenant on first use."""
    global _ACTIVE
    if _ACTIVE is None:
        from .tenant import load_tenant

        _ACTIVE = Vocabulary(load_tenant().vocab)
    return _ACTIVE


def active_or_none():
    """The active Vocabulary, or None -- unlike active(), never loads the
    default tenant as a side effect. See harness/tenant.py's own pair."""
    return _ACTIVE


# Module-level names, resolved through the active vocabulary on every access.
# PEP 562: this only runs for names not already defined in the module.
_ATTRS = {
    "EMF_TERMS": lambda v: v.emf_terms,
    "BANNED_NAMES": lambda v: v.banned_names,
    "HYPE_WORDS": lambda v: v.hype_words,
    "HYPE_SYNONYMS": lambda v: v.hype_synonyms,
    "EXCLAMATION_MARK": lambda v: v.exclamation_mark,
    "ALWAYS_FORBIDDEN_TERMS": lambda v: v.always_forbidden_terms,
    "FORBIDDEN_LENDER_NAMES": lambda v: v.forbidden_lender_names,
    "LENDER_NAME_RE": lambda v: v.lender_name_re,
    "TRIGGER_WORDS": lambda v: v.trigger_words,
    "TRIGGER_WORD_RE": lambda v: v.trigger_word_re,
    "TRIGGER_WORD_SYNONYMS": lambda v: v.trigger_word_synonyms,
    "IMPLIED_CLAIM_FORBIDDEN_TERMS": lambda v: v.implied_claim_forbidden_terms,
    "ALLOWED_FINANCING_SENTENCE_NO_LENDER": lambda v: v.allowed_financing_sentence_no_lender,
    "ALLOWED_FINANCING_SENTENCE_WITH_LENDER_TEMPLATE": lambda v: v.allowed_financing_sentence_with_lender_template,
    "ALLOWED_WARRANTY_SENTENCE": lambda v: v.allowed_warranty_sentence,
    "ALLOWED_WARRANTY_SPEC_LABEL": lambda v: v.allowed_warranty_spec_label,
    "ALLOWED_WARRANTY_SPEC_VALUE": lambda v: v.allowed_warranty_spec_value,
    "VISIBLE_TEXT_FORBIDDEN_TERMS": lambda v: v.visible_text_forbidden_terms,
    "COMPETITOR_ALIASES": lambda v: v.competitor_aliases,
}


def __getattr__(name):
    if name in _ATTRS:
        return _ATTRS[name](active())
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def forbidden_words_block(
    heading="Forbidden words -- never use any of these, in any form, anywhere on the page:",
):
    return active().forbidden_words_block(heading)


def allowed_financing_sentence(lender=None):
    return active().allowed_financing_sentence(lender)
