"""Step 3a, Quick check: drop a candidate straight away when a saved detail clearly breaks a must-have.

It makes no Gemini call. A saved detail is clear enough to drop on when:
- it comes from the catalogue, or Gemini noted it with confidence of at least CONFIDENT;
- its value isn't 'unknown', the product's needs_review list doesn't name it, and Gemini's own reading (kept next
  to a catalogue value) doesn't meet the must-have either.
Shop details (cod, returnable, exchange) come from shop data and are certain. Review-only details (size_fit,
durability, colour_fastness, colour_match) are never decided here; the careful check reads the reviews. A saved
colour is a family, so a shade must-have drops a product only when the families differ; the same family with
another or unknown shade goes on to the careful check, which reads the photo.

Live store products have no saved details, so `live_quick_check` only drops what's certain from the store's own
data: a price beyond the soft budget limit, or the shopper's size not available.
"""

from collections.abc import Sequence

from agent.models import Attribute, MustHave, Product, ShoppingBrief
from agent.rank import OVER_BUDGET
from agent.retrieve import Hit
from agent.rules import failure_label
from agent.sources.shopify import same_size
from agent.tradeoffs import raised_budget
from data_prep.schemas import REVIEW_ATTRIBUTES, SHOP_ATTRIBUTES, UNKNOWN, colour_within
from data_prep.simulate import ONE_SIZE

CONFIDENT = 0.85
ABSENT_VALUES = {"none", "no"}
SIZE_NOT_AVAILABLE = "size not in stock"

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


def live_quick_check(brief: ShoppingBrief, products: Sequence[Product]) -> tuple[list[Hit], list[tuple[Hit, str]]]:
    """Live products as hits in the store's order (total = selling price; shipping is shown at the store's checkout).

    Up to the soft budget limit they're kept and marked over budget, as in agent/retrieve.py.
    """
    kept: list[Hit] = []
    dropped: list[tuple[Hit, str]] = []
    budget = brief.budget_max_inr
    for position, product in enumerate(products):
        if product.price is None:
            continue
        total = product.price.selling
        hit = Hit(product=product, distance=position / max(1, len(products)), total_price=total,
                  over_budget=budget is not None and total > budget)
        if hit.over_budget and total > raised_budget(budget):
            dropped.append((hit, OVER_BUDGET))
        elif brief.size and not has_size(product, brief.size):
            dropped.append((hit, SIZE_NOT_AVAILABLE))
        else:
            kept.append(hit)
    return kept, dropped


def has_size(product: Product, size: str) -> bool:
    """False only when the store lists sizes and none of the available ones is the shopper's."""
    sizes = {name: count for name, count in product.sizes.items() if name != ONE_SIZE}
    if not sizes:
        return True
    return any(count > 0 and same_size(name, size) for name, count in sizes.items())


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
    if must_have.attribute == "colour" and must_have.rule in ("is", "is_not") and isinstance(value, str):
        within = colour_within(value, str(must_have.value))
        return None if within is None else within == (must_have.rule == "is")
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
