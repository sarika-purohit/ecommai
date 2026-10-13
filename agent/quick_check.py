"""Step 3a, Quick check: drop a candidate straight away when a saved detail clearly breaks a must-have.

It makes no Gemini call. A saved detail is clear enough to drop on when:
- it comes from the catalogue, or Gemini noted it with confidence of at least CONFIDENT;
- its value isn't 'unknown', the product's needs_review list doesn't name it, and Gemini's own reading (kept next
  to a catalogue value) doesn't meet the must-have either.
Shop details (cod, returnable, exchange) come from shop data and are certain. Review-only details (size_fit,
durability, colour_fastness, colour_match) are never decided here; the careful check reads the reviews.
"""

from agent.models import Attribute, MustHave, Product, ShoppingBrief
from agent.retrieve import Hit
from agent.rules import failure_label
from data_prep.schemas import REVIEW_ATTRIBUTES, SHOP_ATTRIBUTES, UNKNOWN

CONFIDENT = 0.85
ABSENT_VALUES = {"none", "no"}

Value = str | bool | float


def quick_check(brief: ShoppingBrief, hits: list[Hit]) -> tuple[list[Hit], list[tuple[Hit, str]]]:
    """Kept hits, and dropped hits with the reason (agent.rules.failure_label of the first must-have broken)."""
    kept: list[Hit] = []
    dropped: list[tuple[Hit, str]] = []
    for hit in hits:
        broken = first_broken(brief.must_haves, hit.product)
        if broken is None:
            kept.append(hit)
        else:
            dropped.append((hit, failure_label(broken)))
    return kept, dropped


def first_broken(must_haves: list[MustHave], product: Product) -> MustHave | None:
    return next((m for m in must_haves if clearly_breaks(m, product)), None)


def clearly_breaks(must_have: MustHave, product: Product) -> bool:
    attribute = must_have.attribute
    if attribute in REVIEW_ATTRIBUTES:
        return False
    if attribute in SHOP_ATTRIBUTES:
        return meets(must_have, shop_value(product, attribute)) is False
    saved = product.attributes.get(attribute)
    if saved is None or not is_certain(saved) or attribute in product.needs_review:
        return False
    if saved.gemini_value is not None and meets(must_have, saved.gemini_value):
        return False
    return meets(must_have, saved.value) is False


def is_certain(saved: Attribute) -> bool:
    return saved.source == "catalogue" or saved.confidence >= CONFIDENT


def meets(must_have: MustHave, value: Value | None) -> bool | None:
    """Whether a saved value meets the must-have; None when the value can't say."""
    value = _plain(value)
    if value is None or value in ("", UNKNOWN):
        return None
    match must_have.rule:
        case "is":
            return value == _plain(must_have.value)
        case "is_not":
            return value != _plain(must_have.value)
        case "present" | "absent":
            wanted = must_have.value == (must_have.rule == "present")
            return (value not in ABSENT_VALUES) == wanted
        case "at_most" | "at_least":
            number = _number(value)
            if number is None:
                return None
            return number <= must_have.value if must_have.rule == "at_most" else number >= must_have.value
    return None


def shop_value(product: Product, attribute: str) -> bool | None:
    if attribute == "cod":
        return product.cod.available if product.cod else None
    if attribute in ("returnable", "exchange") and product.returns:
        return getattr(product.returns, attribute)
    return None


def saved_value(product: Product, attribute: str) -> Value | None:
    """The product's saved value for a detail, whatever its confidence. None for review-only details."""
    if attribute in REVIEW_ATTRIBUTES:
        return None
    if attribute in SHOP_ATTRIBUTES:
        return shop_value(product, attribute)
    saved = product.attributes.get(attribute)
    return saved.value if saved else None


def _plain(value: Value | None) -> str | float | None:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str):
        return value.strip().casefold()
    return value


def _number(value: str | float) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None
