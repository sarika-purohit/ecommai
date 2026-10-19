"""Personal rules: add the shopper's saved rules (e.g. "never polyester") to a checklist as locked must-haves."""

from collections.abc import Set
from itertools import count

from agent.models import MustHave, ShoppingBrief
from data_prep import schemas

ATTRIBUTE_WORDS = {"cod": "cash on delivery"}
# Yes/no details that read as adjectives, so their absence is "not polarised" rather than "no polarised".
ADJECTIVE_DETAILS = {"polarised", "returnable"}

# How a value reads on a chip; "{}" is the value. Details not listed read as the bare value ("cotton").
PHRASES = {
    "sleeve_length": "{} sleeves",
    "neckline": "{} neck",
    "pockets": "{} pocket",
    "print_intensity": "{} print",
    "fit": "{} fit",
    "length": "{} length",
    "closure": "{} closure",
    "usage": "for {}",
    "rise": "{} rise",
    "wash": "{} wash",
    "distressed": "{} distressing",
    "sole_type": "{} sole",
    "sole_colour": "{} sole",
    "upper_material": "{} upper",
    "logo_visible": "{} logo",
    "heel_height": "{} heel",
    "display": "{} display",
    "dial_colour": "{} dial",
    "dial_markings": "{} on the dial",
    "case_shape": "{} case",
    "case_material": "{} case",
    "case_size": "{} case",
    "strap_material": "{} strap",
    "strap_colour": "{} strap",
    "size": "{} size",
    "compartments": "{} compartments",
    "frame_shape": "{} frames",
    "frame_material": "{} frames",
    "lens_colour": "{} lenses",
    "durability": "{} durability",
}
VALUE_PHRASES = {
    ("sleeve_length", "sleeveless"): "sleeveless",
    ("neckline", "v-neck"): "v-neck",
    ("neckline", "collar"): "collar",
    ("neckline", "mandarin"): "mandarin collar",
    ("neckline", "polo"): "polo collar",
    ("pockets", "none"): "no pockets",
    ("pockets", "front and back"): "front and back pockets",
    ("pockets", "cargo"): "cargo pockets",
    ("print_intensity", "none"): "no print",
    ("closure", "none"): "no closure",
    ("distressed", "none"): "no distressing",
    ("wash", "none"): "no wash",
    ("logo_visible", "none"): "no logo",
    ("dial_markings", "none"): "plain dial",
    ("compartments", "one"): "one compartment",
    ("colour_match", "as shown"): "colour as shown",
    ("colour_match", "different"): "colour differs from the photo",
    ("colour_fastness", "good"): "colour doesn't fade",
    ("colour_fastness", "fades"): "colour fades",
}

LIMITS = {"at_most": "≤ {}", "at_least": "≥ {}"}
LIMIT_FAILURES = {"at_most": "over {}", "at_least": "under {}"}


def phrase(attribute: str, value: str | bool | float) -> str:
    """A detail value as words: ('sleeve_length', 'full') -> 'full sleeves', ('neckline', 'mandarin') -> 'mandarin collar'."""
    text = _value_text(value)
    return VALUE_PHRASES.get((attribute, text.lower()), PHRASES.get(attribute, "{}").format(text))


def label(must_have: MustHave) -> str:
    """Short chip text for the app: 'cotton', 'full sleeves', 'not polyester', 'pockets', 'no logo visible', '≤ 40'."""
    wanted = _wanted_presence(must_have)
    if wanted is not None:
        return _presence_words(must_have.attribute, wanted)
    if must_have.rule in LIMITS:
        return LIMITS[must_have.rule].format(_value_text(must_have.value))
    words = phrase(must_have.attribute, must_have.value)
    if must_have.rule == "is":
        return words
    return words.removeprefix("no ") if words.startswith("no ") else f"not {words}"


def failure_label(must_have: MustHave) -> str:
    """Why a product failed this must-have, as used in rejection counts: 'no chest pocket', 'see-through'."""
    wanted = _wanted_presence(must_have)
    if wanted is not None:
        return _presence_words(must_have.attribute, not wanted)
    if must_have.rule in LIMIT_FAILURES:
        return LIMIT_FAILURES[must_have.rule].format(_value_text(must_have.value))
    words = phrase(must_have.attribute, must_have.value)
    if must_have.rule == "is_not":
        return words
    if words.startswith("no "):
        return f"has {words.removeprefix('no ')}"
    return f"no {words}" if must_have.attribute == "pockets" else f"not {words}"


def _presence_words(attribute: str, present: bool) -> str:
    words = _words(attribute)
    if present:
        return words
    return f"not {words}" if attribute in ADJECTIVE_DETAILS else f"no {words}"


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
