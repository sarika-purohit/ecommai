"""Memory mode's colour mix: when past chats about a product chose different colours, the colours become weighted
nice-to-haves (NiceToHave.weight, their shares) instead of one must-have. The search fetches each colour and the
results interleave them by share, so "mostly blue, some black, a little red" is what the shopper sees.
"""

from collections import deque
from collections.abc import Callable, Sequence
from typing import TypeVar

from agent.models import Product, ShoppingBrief
from data_prep import schemas

MIXED = ("colour",)
T = TypeVar("T")


def family(colour: str) -> str:
    """A colour value's family ('navy blue' is 'blue'), or the value itself when it has none."""
    return schemas.family_of(colour) or schemas.colour_family(colour) or colour.strip().casefold()


def shares(brief: ShoppingBrief) -> dict[str, float]:
    """Each mixed colour family's share of the places, adding up to 1. Empty without a mix."""
    weights: dict[str, float] = {}
    for nice in brief.nice_to_haves:
        if nice.weight and nice.attribute in MIXED and isinstance(nice.value, str):
            key = family(nice.value)
            weights[key] = weights.get(key, 0.0) + nice.weight
    total = sum(weights.values())
    return {key: weight / total for key, weight in weights.items()} if total else {}


def one_colour(brief: ShoppingBrief, colour: str) -> ShoppingBrief:
    """The brief with only this colour of the mix left, for a search that fetches that colour."""
    kept = [n for n in brief.nice_to_haves if not n.weight or n.attribute not in MIXED or n.value == colour]
    return brief.model_copy(update={"nice_to_haves": kept})


def product_family(product: Product) -> str | None:
    """The product's colour family from its catalogue label, else from its colour detail."""
    if found := schemas.colour_family(product.base_colour):
        return found
    detail = product.attributes.get("colour")
    return family(detail.value) if detail is not None and isinstance(detail.value, str) else None


def interleave(items: Sequence[T], family_of: Callable[[T], str | None], mix: dict[str, float]) -> list[T]:
    """The items in turns by share (D'Hondt), keeping their order within each colour; other colours come last."""
    if not mix:
        return list(items)
    buckets: dict[str, deque[T]] = {key: deque() for key in mix}
    rest: list[T] = []
    for item in items:
        key = family_of(item)
        if key in buckets:
            buckets[key].append(item)
        else:
            rest.append(item)
    taken = dict.fromkeys(mix, 0)
    result: list[T] = []
    while live := [key for key in buckets if buckets[key]]:
        key = max(live, key=lambda k: (mix[k] / (taken[k] + 1), mix[k]))
        result.append(buckets[key].popleft())
        taken[key] += 1
    return result + rest


def pick(items: Sequence[T], family_of: Callable[[T], str | None], mix: dict[str, float], limit: int) -> list[T]:
    """Up to `limit` items, closest first, with each colour's share of the places kept for it when it has enough."""
    if not mix:
        return list(items[:limit])
    quota = {key: round(share * limit) for key, share in mix.items()}
    chosen: list[T] = []
    rest: list[T] = []
    for item in items:
        key = family_of(item)
        if key in quota and quota[key] > 0:
            quota[key] -= 1
            chosen.append(item)
        else:
            rest.append(item)
    picked = {id(item) for item in [*chosen, *rest[: max(limit - len(chosen), 0)]]}
    return [item for item in items if id(item) in picked][:limit]
