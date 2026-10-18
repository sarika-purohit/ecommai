"""Goes with your recent demo orders: pure scoring, no Firestore and no Gemini.

The only purchases we know are orders placed in our own demo store (nothing is paid or delivered). The top
TOP_ANCHORS orders by anchor weight each get their complete-the-look rows (agent/outfit.py); those rows are
combined into one rail.

  recency   r = 0.5 ** (age_days / 60) for ages 0..365 days, 0 after; a future date counts as 0 days
  frequency n_X(v) = sum of r over the orders with value v (X: product type, usage, colour family);
            F_X(v) = n_X(v) / max n_X
  anchor    A(o) = r(o) * (0.6 + 0.4 * F_type(o))
  pairing   S(c, o) = A(o) * match / 100, for rows with a match of at least MIN_MATCH
  score     (1 - prod_o(1 - S(c, o))) * (1 + 0.2 * h(c)); h = mean of F_usage and F_colour (those we know), so a
            sneaker that goes with both the shirt and the trousers beats one that goes with one.

Never suggested: a product already ordered or disliked, or a product type ordered in the last RECENT_DAYS days.
At most PER_ANCHOR picks per anchor order, RAIL_SIZE in all. Things we don't sell (NOT_SOLD) come back as ideas.
"""

from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from agent.browse import CatalogItem
from agent.outfit import Slot, Suggestion
from data_prep.schemas import colour_family

HALF_LIFE_DAYS = 60.0
MAX_AGE_DAYS = 365
RECENT_DAYS = 45
TOP_ANCHORS = 3
PER_ANCHOR = 3
RAIL_SIZE = 8
MIN_MATCH = 50
KEEP_ORDERS = 50
TYPE_BASE, TYPE_WEIGHT = 0.6, 0.4
HABIT_WEIGHT = 0.2
# Fashion we don't carry, by the type of the order it goes with: shown as ideas, never as products.
NOT_SOLD: dict[str, tuple[str, ...]] = {
    "Kurtas": ("pyjama or churidar", "juttis or mojaris"),
    "Kurtis": ("leggings or palazzos", "dupatta"),
    "Tunics": ("leggings or palazzos", "dupatta"),
}


class Order(BaseModel):
    """One order placed in the demo store (no payment, nothing delivered), saved on the shopper's document."""

    id: str
    product_id: str
    title: str
    article_type: str
    gender: str
    base_colour: str | None = None
    usage: str | None = None
    size: str | None = None
    price_inr: int | None = None
    ordered_at: datetime = Field(description="Server time, or backdated when seeded for a demo")
    source: Literal["demo_catalog"] = "demo_catalog"
    seeded: bool = False


class GoesWith(BaseModel):
    product_id: str
    title: str
    ordered_at: datetime
    seeded: bool


class Pick(BaseModel):
    product: CatalogItem
    slot: Slot
    match: int = Field(description="0-100, Gemini's styling match with the order it goes with best")
    reason: str
    goes_with: list[GoesWith]


class Idea(BaseModel):
    goes_with_title: str
    text: str


class OrderSuggestions(BaseModel):
    items: list[Pick] = Field(default_factory=list)
    ideas: list[Idea] = Field(default_factory=list)
    note: str | None = None
    sample_data: bool = True
    demo_orders: bool = True


class Anchor(BaseModel):
    order: Order
    weight: float


def as_utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def age_days(ordered_at: datetime, now: datetime) -> float:
    return max(0.0, (as_utc(now) - as_utc(ordered_at)).total_seconds() / 86400)


def recency(days: float) -> float:
    days = max(0.0, days)
    return 0.0 if days > MAX_AGE_DAYS else 0.5 ** (days / HALF_LIFE_DAYS)


class Frequencies(BaseModel):
    """F_X(v) for the three kinds of value; a value no order has is 0."""

    type: dict[str, float] = Field(default_factory=dict)
    usage: dict[str, float] = Field(default_factory=dict)
    colour: dict[str, float] = Field(default_factory=dict)


def _relative(counts: Mapping[str, float]) -> dict[str, float]:
    top = max(counts.values(), default=0.0)
    return {value: (count / top if top else 0.0) for value, count in counts.items()}


def _colour(order_or_item: object) -> str | None:
    return colour_family(getattr(order_or_item, "base_colour", None))


def frequencies(orders: Iterable[Order], now: datetime) -> Frequencies:
    counts: dict[str, dict[str, float]] = {"type": defaultdict(float), "usage": defaultdict(float),
                                           "colour": defaultdict(float)}
    for order in orders:
        r = recency(age_days(order.ordered_at, now))
        for kind, value in (("type", order.article_type), ("usage", order.usage), ("colour", _colour(order))):
            if value:
                counts[kind][value] += r
    return Frequencies(**{kind: _relative(found) for kind, found in counts.items()})


def anchors(orders: Sequence[Order], now: datetime, limit: int = TOP_ANCHORS) -> list[Anchor]:
    """The top distinct purchases by A(o); each product counts once, at its best order."""
    freq = frequencies(orders, now)
    best: dict[str, Anchor] = {}
    for order in orders:
        r = recency(age_days(order.ordered_at, now))
        weight = r * (TYPE_BASE + TYPE_WEIGHT * freq.type.get(order.article_type, 0.0))
        if weight > 0 and (order.product_id not in best or weight > best[order.product_id].weight):
            best[order.product_id] = Anchor(order=order, weight=weight)
    return sorted(best.values(), key=lambda a: (-a.weight, a.order.product_id))[:limit]


def habit(item: CatalogItem, freq: Frequencies) -> float:
    """h(c): the mean of how usual its usage and colour family are among the orders, for those we know."""
    known = [freq.usage.get(item.usage or "", 0.0)] if item.usage else []
    family = _colour(item)
    if family:
        known.append(freq.colour.get(family, 0.0))
    return sum(known) / len(known) if known else 0.0


def pairing(anchor: Anchor, match: int | None) -> float:
    """S(c, o); 0 for an unscored row or a match under MIN_MATCH."""
    return 0.0 if match is None or match < MIN_MATCH else anchor.weight * match / 100


def _s(pair: tuple[Anchor, Suggestion]) -> float:
    return pairing(pair[0], pair[1].match)


def kept(orders: Sequence[Order], now: datetime) -> list[Order]:
    """Oldest first: the orders still under a year old, at most KEEP_ORDERS of the newest."""
    fresh = sorted((o for o in orders if age_days(o.ordered_at, now) <= MAX_AGE_DAYS), key=lambda o: as_utc(o.ordered_at))
    return fresh[-KEEP_ORDERS:]


def ideas(chosen: Sequence[Anchor]) -> list[Idea]:
    return [
        Idea(goes_with_title=anchor.order.title, text=text)
        for anchor in chosen for text in NOT_SOLD.get(anchor.order.article_type, ())
    ]


def suggest(
    rows: Sequence[tuple[Anchor, Sequence[Suggestion]]],
    orders: Sequence[Order],
    disliked: Collection[str],
    now: datetime,
) -> list[Pick]:
    """The rail: `rows` are each anchor's complete-the-look suggestions."""
    freq = frequencies(orders, now)
    ordered = {order.product_id for order in orders}
    recent = {order.article_type for order in orders if age_days(order.ordered_at, now) <= RECENT_DAYS}
    found: dict[str, dict[str, tuple[Anchor, Suggestion]]] = defaultdict(dict)
    for anchor, suggestions in rows:
        for row in suggestions:
            item = row.product
            if item.id in ordered or item.id in disliked or item.article_type in recent:
                continue
            if pairing(anchor, row.match) <= 0:
                continue
            kept = found[item.id].get(anchor.order.product_id)
            if kept is None or (row.match or 0) > (kept[1].match or 0):
                found[item.id][anchor.order.product_id] = (anchor, row)

    scored = []
    for by_anchor in found.values():
        none_of = 1.0
        for anchor, row in by_anchor.values():
            none_of *= 1 - pairing(anchor, row.match)
        row = next(iter(by_anchor.values()))[1]
        scored.append(((1 - none_of) * (1 + HABIT_WEIGHT * habit(row.product, freq)), by_anchor))
    scored.sort(key=lambda s: (-s[0], min(r.product.id for _, r in s[1].values())))

    picks: list[Pick] = []
    used: dict[str, int] = defaultdict(int)
    for _, by_anchor in scored:
        main_anchor, main_row = max(by_anchor.values(), key=lambda pair: (_s(pair), pair[0].order.product_id))
        if used[main_anchor.order.product_id] >= PER_ANCHOR:
            continue
        used[main_anchor.order.product_id] += 1
        pairs = sorted(by_anchor.values(), key=lambda pair: -_s(pair))
        picks.append(Pick(
            product=main_row.product, slot=main_row.slot, match=main_row.match or 0, reason=main_row.reason,
            goes_with=[
                GoesWith(product_id=a.order.product_id, title=a.order.title, ordered_at=a.order.ordered_at,
                         seeded=a.order.seeded)
                for a, _ in pairs
            ],
        ))
        if len(picks) == RAIL_SIZE:
            break
    return picks



PARTLY = "Some ideas couldn't be worked out just now. Please try again in a moment."


def from_orders(
    orders: Sequence[Order],
    disliked: Collection[str],
    now: datetime,
    look: Callable[[Order], Sequence[Suggestion] | None],
) -> OrderSuggestions:
    """The rail for these orders. `look(order)` gives that order's complete-the-look suggestions, None if it couldn't.

    No orders means no `look` call at all, so nothing asks Gemini.
    """
    chosen = anchors(orders, now)
    if not chosen:
        return OrderSuggestions()
    rows = [(anchor, found) for anchor in chosen if (found := look(anchor.order)) is not None]
    return OrderSuggestions(
        items=suggest(rows, orders, set(disliked), now), ideas=ideas(chosen),
        note=PARTLY if len(rows) < len(chosen) else None,
    )
