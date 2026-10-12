"""Step 4, 'what to give up': relax one limit at a time and recount the products we've already checked."""

from datetime import timedelta

from pydantic import BaseModel, Field

from agent.models import ShoppingBrief
from agent.rank import Candidate, rank

DEADLINE_EXTRA_DAYS = (1, 2)
BUDGET_RAISE_PERCENT = 20
BUDGET_ROUND_TO = 50
MAX_SUGGESTIONS = 4


class Tradeoff(BaseModel):
    relax: str = Field(description="'deadline', 'budget' or 'must_have:<attribute>'")
    to: str | int | None = Field(default=None, description="ISO date for the deadline, rupees for the budget")
    matches: int


def suggest_tradeoffs(brief: ShoppingBrief, candidates: list[Candidate], min_matches: int = 3) -> list[Tradeoff]:
    """Ways to get more full matches by giving up one thing, best kept to at most MAX_SUGGESTIONS.

    Counts are what `rank()` would show after the change, so they match what one tap gives.
    Dropping a must-have drops every must-have on that attribute.
    """
    now = _matches(brief, candidates)
    if now >= min_matches:
        return []
    limits = [t for t in (_relax_deadline(brief, candidates, now), _relax_budget(brief, candidates, now)) if t]
    dropped = sorted(_drop_must_haves(brief, candidates, now), key=lambda t: -t.matches)
    return (limits + dropped)[:MAX_SUGGESTIONS]


def _matches(brief: ShoppingBrief, candidates: list[Candidate]) -> int:
    return len(rank(brief, candidates).matches)


def _relax_deadline(brief: ShoppingBrief, candidates: list[Candidate], now: int) -> Tradeoff | None:
    if brief.deadline_date is None:
        return None
    for days in DEADLINE_EXTRA_DAYS:
        later = brief.deadline_date + timedelta(days=days)
        matches = _matches(brief.model_copy(update={"deadline_date": later}), candidates)
        if matches > now:
            return Tradeoff(relax="deadline", to=later.isoformat(), matches=matches)
    return None


def _relax_budget(brief: ShoppingBrief, candidates: list[Candidate], now: int) -> Tradeoff | None:
    if brief.budget_max_inr is None:
        return None
    raised = _raised_budget(brief.budget_max_inr)
    matches = _matches(brief.model_copy(update={"budget_max_inr": raised}), candidates)
    return Tradeoff(relax="budget", to=raised, matches=matches) if matches > now else None


def _drop_must_haves(brief: ShoppingBrief, candidates: list[Candidate], now: int) -> list[Tradeoff]:
    options = []
    for attribute in dict.fromkeys(m.attribute for m in brief.must_haves):
        kept = [m for m in brief.must_haves if m.attribute != attribute]
        matches = _matches(brief.model_copy(update={"must_haves": kept}), candidates)
        if matches > now:
            options.append(Tradeoff(relax=f"must_have:{attribute}", matches=matches))
    return options


def _raised_budget(budget: int) -> int:
    """The budget plus BUDGET_RAISE_PERCENT, rounded up to the next BUDGET_ROUND_TO rupees."""
    hundredths = budget * (100 + BUDGET_RAISE_PERCENT)
    step = 100 * BUDGET_ROUND_TO
    return -(-hundredths // step) * BUDGET_ROUND_TO
