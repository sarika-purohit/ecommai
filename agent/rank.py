"""Step 4, Show: sort checked products into full matches, 'almost' and rejection counts, with a reason line each."""

from collections import Counter
from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel, Field

from agent.learn import cosine
from agent.models import Evidence, MustHave, NiceToHave, Product, ShoppingBrief, Status, Verification
from agent.rules import failure_label, label

# Each score factor is scaled to about 0-1 before weighting. The score only orders products within
# matches and within almost; it never decides which list a product is in.
NICE_TO_HAVE_WEIGHT = 3.0
STYLE_WEIGHT = 2.0
PRICE_WEIGHT = 1.5
DELIVERY_WEIGHT = 1.0
RATING_WEIGHT = 1.0
RATING_HALF_TRUST_REVIEWS = 20

DUPLICATE_SIMILARITY = 0.98

OVER_BUDGET = "over budget"
LATE_DELIVERY = "late delivery"


class Candidate(BaseModel):
    product: Product
    verification: Verification
    total_price: int = Field(gt=0, description="Rupees, including delivery and any COD fee")
    delivery_date: date | None = None


class Tick(BaseModel):
    must_have_id: str
    label: str
    status: Status
    evidence: Evidence | None = None


class RankedResult(BaseModel):
    product_id: str
    title: str
    total_price: int
    delivery_date: date | None
    ticks: list[Tick]
    reason: str
    score: float


class Results(BaseModel):
    matches: list[RankedResult]
    almost: list[RankedResult]
    rejected: dict[str, int]
    duplicates: int = Field(default=0, description="Near-duplicates of a shown product, left out")
    checked: int
    passed: int
    unclear: int
    tradeoffs: list = Field(default_factory=list, description="Filled in by tradeoffs.suggest_tradeoffs")


@dataclass
class _Entry:
    candidate: Candidate
    ticks: list[Tick]
    score: float

    @property
    def confirmed(self) -> bool:
        return all(tick.status == "pass" for tick in self.ticks)


def rank(brief: ShoppingBrief, candidates: list[Candidate], taste: dict[str, list[float]] | None = None) -> Results:
    """Split checked candidates into matches, almost and rejected, best first.

    checked == passed + unclear + sum(rejected.values()) + duplicates.
    """
    rejected: Counter[str] = Counter()
    shown: list[tuple[Candidate, list[Tick]]] = []
    for candidate in candidates:
        candidate_ticks = ticks_for(brief, candidate.verification)
        problem = _limit_problem(brief, candidate) or _first_failure(brief.must_haves, candidate_ticks)
        if problem:
            rejected[problem] += 1
        else:
            shown.append((candidate, candidate_ticks))

    cheapest = min((c.total_price for c, _ in shown), default=0)
    soonest = min((c.delivery_date for c, _ in shown if c.delivery_date), default=None)
    taste = taste or {}
    entries = [_Entry(c, t, _score(brief.nice_to_haves, c, taste, cheapest, soonest)) for c, t in shown]
    entries.sort(key=lambda e: (not e.confirmed, -e.score))
    unique = _without_duplicates(entries)

    matches = [_result(e) for e in unique if e.confirmed]
    almost = [_result(e) for e in unique if not e.confirmed]
    return Results(
        matches=matches,
        almost=almost,
        rejected=dict(rejected.most_common()),
        duplicates=len(entries) - len(unique),
        checked=len(candidates),
        passed=len(matches),
        unclear=len(almost),
    )


def ticks_for(brief: ShoppingBrief, verification: Verification) -> list[Tick]:
    """One tick per must-have, in brief order. A must-have with no check is unclear."""
    checks = {check.must_have_id: check for check in verification.checks}
    result = []
    for must_have in brief.must_haves:
        check = checks.get(must_have.id)
        result.append(
            Tick(
                must_have_id=must_have.id,
                label=label(must_have),
                status=check.status if check else "unclear",
                evidence=check.evidence if check else None,
            )
        )
    return result


def rupees(amount: int) -> str:
    """Indian digit grouping: ₹999, ₹1,049, ₹1,24,999."""
    digits = str(amount)
    head, tail = digits[:-3], digits[-3:]
    groups: list[str] = []
    while head:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return "₹" + ",".join([*groups, tail])


def _limit_problem(brief: ShoppingBrief, candidate: Candidate) -> str | None:
    if brief.budget_max_inr is not None and candidate.total_price > brief.budget_max_inr:
        return OVER_BUDGET
    if brief.deadline_date and candidate.delivery_date and candidate.delivery_date > brief.deadline_date:
        return LATE_DELIVERY
    return None


def _first_failure(must_haves: list[MustHave], ticks: list[Tick]) -> str | None:
    for must_have, tick in zip(must_haves, ticks):
        if tick.status == "fail":
            return failure_label(must_have)
    return None


def _score(
    nice_to_haves: list[NiceToHave],
    candidate: Candidate,
    taste: dict[str, list[float]],
    cheapest: int,
    soonest: date | None,
) -> float:
    product = candidate.product
    return (
        NICE_TO_HAVE_WEIGHT * _nice_to_have_share(nice_to_haves, product)
        + STYLE_WEIGHT * (_similarity(product.embedding, taste.get(product.article_type)) or 0.0)
        + PRICE_WEIGHT * cheapest / candidate.total_price
        + DELIVERY_WEIGHT * _speed(candidate.delivery_date, soonest)
        + RATING_WEIGHT * _rating(product)
    )


def _nice_to_have_share(nice_to_haves: list[NiceToHave], product: Product) -> float:
    if not nice_to_haves:
        return 0.0
    met = 0
    for nice in nice_to_haves:
        attribute = product.attributes.get(nice.attribute)
        if attribute is not None and _same_value(attribute.value, nice.value):
            met += 1
    return met / len(nice_to_haves)


def _same_value(a: str | bool | float, b: str | bool | float) -> bool:
    if b is True and isinstance(a, str):
        return a.casefold() not in ("none", "no", "unknown")
    if isinstance(a, str) and isinstance(b, str):
        return a.casefold() == b.casefold()
    return a == b


def _similarity(a: list[float] | None, b: list[float] | None) -> float | None:
    if not a or not b or len(a) != len(b):
        return None
    return cosine(a, b)


def _speed(delivery_date: date | None, soonest: date | None) -> float:
    if delivery_date is None or soonest is None:
        return 0.0
    return 1 / (1 + (delivery_date - soonest).days)


def _rating(product: Product) -> float:
    if product.rating_avg is None:
        return 0.0
    trust = product.rating_count / (product.rating_count + RATING_HALF_TRUST_REVIEWS)
    return product.rating_avg / 5 * trust


def _without_duplicates(entries: list[_Entry]) -> list[_Entry]:
    """Keep the first of each group of near-duplicates; entries must already be best first."""
    kept: list[_Entry] = []
    for entry in entries:
        if not any(_same_product(entry.candidate.product, k.candidate.product) for k in kept):
            kept.append(entry)
    return kept


def _same_product(a: Product, b: Product) -> bool:
    if _identity(a) == _identity(b):
        return True
    similarity = _similarity(a.embedding, b.embedding)
    return similarity is not None and similarity >= DUPLICATE_SIMILARITY


def _identity(product: Product) -> tuple[str, str, str]:
    return _plain(product.brand), _plain(product.base_colour), _plain(product.title)


def _plain(text: str | None) -> str:
    return " ".join((text or "").split()).casefold()


def _result(entry: _Entry) -> RankedResult:
    candidate = entry.candidate
    reason = _match_reason(candidate, entry.ticks) if entry.confirmed else _almost_reason(entry.ticks)
    return RankedResult(
        product_id=candidate.product.id,
        title=candidate.product.title,
        total_price=candidate.total_price,
        delivery_date=candidate.delivery_date,
        ticks=entry.ticks,
        reason=reason,
        score=round(entry.score, 4),
    )


def _match_reason(candidate: Candidate, ticks: list[Tick]) -> str:
    parts = []
    if len(ticks) == 1:
        parts.append("1 must-have confirmed")
    elif ticks:
        parts.append(f"All {len(ticks)} must-haves confirmed")
    parts.append(f"{rupees(candidate.total_price)} delivered")
    if candidate.delivery_date:
        parts.append(f"arrives {_day(candidate.delivery_date)}")
    rating = _rating_text(candidate.product)
    if rating:
        parts.append(rating)
    return " · ".join(parts)


def _almost_reason(ticks: list[Tick]) -> str:
    confirmed = sum(tick.status == "pass" for tick in ticks)
    unsure = ", ".join(tick.label for tick in ticks if tick.status == "unclear")
    return f"{confirmed} of {len(ticks)} confirmed · not sure about: {unsure}"


def _day(day: date) -> str:
    return f"{day:%a} {day.day} {day:%b}"


def _rating_text(product: Product) -> str | None:
    if product.rating_avg is None:
        return None
    if product.rating_count == 0:
        return f"{product.rating_avg:.1f}★"
    reviews = "review" if product.rating_count == 1 else "reviews"
    return f"{product.rating_avg:.1f}★ ({product.rating_count} {reviews})"
