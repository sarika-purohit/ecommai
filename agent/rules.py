"""Personal rules: add the shopper's saved rules (e.g. "never polyester") to a checklist as locked must-haves."""

from collections.abc import Set
from itertools import count

from agent.models import MustHave, ShoppingBrief
from data_prep import schemas

ATTRIBUTE_WORDS = {"cod": "cash on delivery"}

LABELS = {"is": "{}", "is_not": "not {}", "at_most": "≤ {}", "at_least": "≥ {}"}
FAILURE_LABELS = {"is": "not {}", "is_not": "{}", "at_most": "over {}", "at_least": "under {}"}


def label(must_have: MustHave) -> str:
    """Short chip text for the app: 'cotton', 'not polyester', 'pockets', 'no logo visible', '≤ 40'."""
    wanted = _wanted_presence(must_have)
    if wanted is not None:
        words = _words(must_have.attribute)
        return words if wanted else f"no {words}"
    return LABELS[must_have.rule].format(_value_text(must_have.value))


def failure_label(must_have: MustHave) -> str:
    """Why a product failed this must-have, as used in rejection counts: 'no pockets', 'see-through'."""
    wanted = _wanted_presence(must_have)
    if wanted is not None:
        words = _words(must_have.attribute)
        return f"no {words}" if wanted else words
    return FAILURE_LABELS[must_have.rule].format(_value_text(must_have.value))


def apply_rules(brief: ShoppingBrief, rules: list[MustHave], disabled: Set[str] = frozenset()) -> ShoppingBrief:
    """Return a new brief with every saved rule that applies to this search added as a locked must-have.

    `disabled` holds the `key()` of rules switched off for this search. Rules added by an earlier call are
    replaced, so it's safe to call again after the brief is edited or a rule is switched off.
    """
    group = schemas.group_for(brief.category)
    if group is None:
        raise ValueError(f"unknown category '{brief.category}'")
    allowed = schemas.attributes_for(group)
    own = [m.model_copy() for m in brief.must_haves if not m.from_rule]
    own_attributes = {m.attribute for m in own}
    used_ids = {m.id for m in own}

    added: list[MustHave] = []
    for rule in rules:
        if rule.key() in disabled or rule.attribute not in allowed or rule.attribute in own_attributes:
            continue
        if any(rule.key() == a.key() for a in added):
            continue
        rule_id = _next_id(used_ids)
        used_ids.add(rule_id)
        added.append(rule.model_copy(update={"id": rule_id, "from_rule": True}))

    return brief.model_copy(
        deep=True, update={"must_haves": own + added, "rules_applied": [label(rule) for rule in added]}
    )


def _wanted_presence(must_have: MustHave) -> bool | None:
    """True when the must-have asks for the detail to be there, False when it asks for it to be missing.

    None for must-haves about a specific value. Yes/no details (cod, polarised, ...) count as presence.
    """
    if must_have.rule in ("present", "absent"):
        return must_have.value == (must_have.rule == "present")
    if must_have.rule in ("is", "is_not") and str(must_have.value).lower() in ("yes", "no"):
        return (str(must_have.value).lower() == "yes") == (must_have.rule == "is")
    return None


def _words(attribute: str) -> str:
    return ATTRIBUTE_WORDS.get(attribute, attribute.replace("_", " "))


def _value_text(value: str | bool | float) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _next_id(used: set[str]) -> str:
    return next(f"r{n}" for n in count(1) if f"r{n}" not in used)
