"""The harness's reading of the vendored skill.

rules.json is the source of truth: every extracted rule is take, adapt, or
decline, with a one-line reason. This module loads that file and formats
it for `harness design-skills list` / `explain`. It does not run checks --
that is gate.py.
"""
from . import load_rules, load_source, skill_names


def rules(*, action=None):
    """The rule list, optionally filtered to one decision."""
    items = load_rules()["rules"]
    if action:
        items = [r for r in items if r.get("action") == action]
    return items


def counts():
    tallies = {"take": 0, "adapt": 0, "decline": 0}
    for rule in rules():
        action = rule.get("action")
        if action in tallies:
            tallies[action] += 1
    return tallies


def format_list():
    """One line per vendored skill, plus the take/adapt/decline tally."""
    source = load_source()
    n = counts()
    lines = [
        f"{skill_names()[0]:24s}  take={n['take']}  adapt={n['adapt']}  decline={n['decline']}  "
        f"from {source['repo']} @{source['vendored_commit'][:12]}",
        "",
        "The raw skill never overrides the claims gate, the block gate, or a",
        "tenant's locked topics. `harness design-skills explain` prints the",
        "per-rule decisions.",
    ]
    return "\n".join(lines) + "\n"


def format_explain(*, action=None):
    """The decision table, one block per rule."""
    source = load_source()
    lines = [
        f"# {load_rules()['skill']}",
        "",
        f"Source: {source['repo']} commit {source['vendored_commit']}",
        f"License: {source['license']} — {source['copyright']}",
        "",
        "action = take (gate or block, as written) / adapt (same intent, harness shape) / decline (conflicts with a harness rule).",
        "",
    ]
    for rule in rules(action=action):
        lines.append(f"## {rule['id']} — {rule['title']}")
        lines.append(f"- kind: {rule['kind']}  measurable: {rule['measurable']}  action: {rule['action']}")
        if rule.get("check"):
            lines.append(f"- check: {rule['check']}")
        lines.append(f"- {rule['harness']}")
        lines.append("")
    return "\n".join(lines)
